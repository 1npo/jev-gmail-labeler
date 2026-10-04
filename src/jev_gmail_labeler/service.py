"""The listener service: Pub/Sub notifications trigger history syncs and labelling."""

import logging
import signal
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from jev_gmail_labeler.config import AppConfig
from jev_gmail_labeler.errors import GmailError, HistoryExpiredError, PubSubError
from jev_gmail_labeler.google_api.gmail import GmailClient, Profile
from jev_gmail_labeler.google_api.pubsub import (
    PubSubPuller,
    PulledMessage,
    parse_notification,
)
from jev_gmail_labeler.models import PipelineStatus
from jev_gmail_labeler.pipeline import Pipeline
from jev_gmail_labeler.state import StateStore

log = logging.getLogger(__name__)

WATCH_RENEW_SECONDS = 24 * 3600
WATCH_MIN_REMAINING_SECONDS = 3600
PRUNE_AFTER_SECONDS = 30 * 86400


def renew_watch(
    gmail: GmailClient,
    state: StateStore,
    topic: str,
    *,
    now: float,
    force: bool = False,
) -> bool:
    """Renew the Gmail watch if it is due (or ``force``); return True if renewed."""
    expiration_ms = state.get('watch_expiration_ms')
    renewed_at = state.get('watch_renewed_at')
    due = (
        force
        or expiration_ms is None
        or renewed_at is None
        or now - float(renewed_at) >= WATCH_RENEW_SECONDS
        or int(expiration_ms) / 1000 - now <= WATCH_MIN_REMAINING_SECONDS
    )
    if not due:
        return False
    resp = gmail.watch(topic)
    state.set('watch_expiration_ms', str(resp.expiration_ms))
    state.set('watch_renewed_at', str(now))
    if state.get_cursor() is None:
        state.advance_cursor(resp.history_id)
    until = datetime.fromtimestamp(resp.expiration_ms / 1000, UTC).isoformat()
    log.info('Watch renewed until %s', until)
    return True


class LabelerService:
    """Single-threaded pull loop that labels new INBOX messages."""

    def __init__(
        self,
        *,
        config: AppConfig,
        gmail: GmailClient,
        puller: PubSubPuller,
        pipeline: Pipeline,
        state: StateStore,
        dry_run: bool = False,
        stop_event: threading.Event | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._gmail = gmail
        self._puller = puller
        self._pipeline = pipeline
        self._state = state
        self._dry_run = dry_run
        self._stop = stop_event or threading.Event()
        self._clock = clock
        self._monotonic = monotonic
        self.profile: Profile | None = None

    def request_stop(self, *_: object) -> None:
        """Signal handler: ask the loop to finish and exit."""
        log.info('Stop requested')
        self._stop.set()

    def _renew(self, *, force: bool = False) -> None:
        assert self._config.pubsub_topic is not None
        renew_watch(
            self._gmail,
            self._state,
            self._config.pubsub_topic,
            now=self._clock(),
            force=force,
        )

    def run(self) -> None:
        """Run until a stop is requested."""
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        try:
            self._state.prune(self._clock() - PRUNE_AFTER_SECONDS)
            self.profile = self._gmail.get_profile()
            log.info('Listening for new mail (dry run: %s)', self._dry_run)
            self._renew()
            self.retry_due()
            self.sync()
            last_activity = self._monotonic()
            failures = 0
            while not self._stop.is_set():
                try:
                    batch = self._puller.pull(
                        self._config.pull_max_messages,
                        self._config.pull_timeout_seconds,
                    )
                except PubSubError as e:
                    if not e.transient:
                        raise
                    failures += 1
                    delay = min(60, 2**failures)
                    log.warning('Pub/Sub pull failed (%s); retrying in %ss', e, delay)
                    self._stop.wait(delay)
                    continue
                failures = 0
                if batch:
                    self.handle_batch(batch)
                    last_activity = self._monotonic()
                elif (
                    self._monotonic() - last_activity
                    >= self._config.idle_resync_minutes * 60
                ):
                    self.sync()
                    last_activity = self._monotonic()
                self.retry_due()
                self._renew()
        finally:
            self._puller.close()
            self._pipeline.close()
            self._state.close()
            log.info('Stopped')

    def handle_batch(self, batch: Sequence[PulledMessage]) -> None:
        """Turn notifications into a sync, then ack. Notifications are only triggers."""
        if self.profile is None:
            self.profile = self._gmail.get_profile()
        mine = self.profile.email_address.casefold()
        cursor = self._state.get_cursor()
        newest = -1
        for msg in batch:
            note = parse_notification(msg.data)
            if note is None:
                log.warning('Ignoring invalid notification %s', msg.message_id)
            elif note.email_address.casefold() != mine:
                log.warning('Ignoring notification for another mailbox')
            else:
                newest = max(newest, note.history_id)
        ack_ids = [m.ack_id for m in batch]
        if newest > (cursor if cursor is not None else -1):
            try:
                self.sync()
            except GmailError as e:
                if not e.transient:
                    raise
                log.warning('Sync failed (%s); will retry', e)
                self._puller.nack(ack_ids)
                self._stop.wait(5)
                return
        self._puller.ack(ack_ids)

    def sync(self) -> None:
        """Process messages added since the stored history cursor."""
        cursor = self._state.get_cursor()
        if cursor is None:
            self._state.advance_cursor(self._gmail.get_profile().history_id)
            return
        try:
            page = self._gmail.list_history(cursor)
        except HistoryExpiredError:
            log.warning('History cursor expired; catching up from a message search')
            self.catch_up()
            return
        for message_id in page.message_ids:
            self.process(message_id)
        self._state.advance_cursor(page.history_id)
        self._state.set('last_sync_at', str(self._clock()))

    def catch_up(self) -> None:
        """Recover from an expired cursor by searching recent INBOX mail."""
        profile = self._gmail.get_profile()
        now = self._clock()
        since = float(self._state.get('last_sync_at') or now - 86400) - 3600
        ids = self._gmail.list_message_ids(
            query=f'in:inbox after:{int(since)}',
            max_results=self._config.catchup_max_messages,
        )
        for message_id in reversed(ids):
            self.process(message_id)
        self._state.advance_cursor(profile.history_id)
        self._state.set('last_sync_at', str(self._clock()))
        log.warning('Caught up on %d messages', len(ids))

    def process(self, message_id: str) -> None:
        """Run the pipeline on one message, recording the outcome."""
        now = self._clock()
        rec = self._state.message(message_id)
        if rec is not None and (
            rec.status in ('done', 'failed')
            or (rec.status == 'retry' and (rec.next_retry_at or 0) > now)
        ):
            return
        result = self._pipeline.process(message_id, apply=not self._dry_run)
        if self._dry_run:
            return
        if result.status is PipelineStatus.ERROR:
            attempts = (rec.attempts if rec else 0) + 1
            error = result.error or 'unknown error'
            if result.retryable and attempts < self._config.max_attempts:
                delay = min(3600, 60 * 2 ** (attempts - 1))
                self._state.mark_retry(
                    message_id, attempts=attempts, error=error, next_retry_at=now + delay
                )
            else:
                self._state.mark_failed(message_id, attempts=attempts, error=error)
                log.error(
                    'Giving up on %s after %d attempts: %s', message_id, attempts, error
                )
            return
        self._state.mark_done(
            message_id,
            result_status=result.status.value,
            category=result.classification.category if result.classification else None,
            label_name=result.decision.label_name if result.decision else None,
        )

    def retry_due(self) -> None:
        """Process messages whose retry time has come."""
        for message_id in self._state.due_retries(self._clock()):
            self.process(message_id)

"""The labelling pipeline: fetch -> anonymize -> classify -> decide -> apply label."""

import logging
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from types import TracebackType

from jev_gmail_labeler.anonymize import Anonymizer
from jev_gmail_labeler.criteria import NO_MATCH_ID, Criteria
from jev_gmail_labeler.errors import AuthError, ConfigError, MessageNotFoundError
from jev_gmail_labeler.google_api.gmail import GmailClient
from jev_gmail_labeler.google_api.labels import LabelManager
from jev_gmail_labeler.models import (
    ClassificationResult,
    DecisionReason,
    EmailMessage,
    LabelDecision,
    PipelineResult,
    PipelineStatus,
    SkipReason,
)
from jev_gmail_labeler.typesafe_api.classifier import JevClassifier

log = logging.getLogger(__name__)


def decide(classification: ClassificationResult, criteria: Criteria) -> LabelDecision:
    """Map a classification to the Gmail label to apply (or None)."""
    category = classification.category
    if category == NO_MATCH_ID:
        reason, label = DecisionReason.NO_MATCH, criteria.no_match_label
    elif classification.confidence < criteria.min_confidence:
        reason, label = DecisionReason.LOW_CONFIDENCE, criteria.uncertain_label
    else:
        reason = DecisionReason.MATCHED
        matched = criteria.category(category)
        label = matched.label_name if matched else None
    return LabelDecision(
        message_id=classification.message_id,
        category=category,
        reason=reason,
        label_name=label,
    )


class Pipeline:
    """Process Gmail messages one at a time, isolating per-message failures."""

    def __init__(
        self,
        *,
        gmail: GmailClient,
        labels: LabelManager,
        anonymizer: Anonymizer,
        classifier: JevClassifier,
        criteria: Criteria,
        max_body_chars: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._gmail = gmail
        self._labels = labels
        self._anonymizer = anonymizer
        self._classifier = classifier
        self._criteria = criteria
        self._max_body_chars = max_body_chars
        self._now = now

    def _result(
        self,
        message_id: str,
        status: PipelineStatus,
        email: EmailMessage | None = None,
        **fields: object,
    ) -> PipelineResult:
        defaults: dict[str, object] = {
            'skip_reason': None,
            'classification': None,
            'decision': None,
            'applied': False,
            'label_id': None,
            'error': None,
            'retryable': False,
        }
        defaults.update(fields)
        result = PipelineResult(
            message_id=message_id,
            thread_id=email.thread_id if email else None,
            date=email.date if email else None,
            sender=email.sender if email else None,
            subject=email.subject if email else None,
            status=status,
            processed_at=self._now().isoformat(),
            **defaults,  # type: ignore[arg-type]
        )
        c, d = result.classification, result.decision
        log.info(
            '%s %s category=%s label=%s confidence=%s',
            message_id,
            status.value,
            c.category if c else '-',
            (d.label_name or '-') if d else '-',
            f'{c.confidence:.2f}' if c else '-',
        )
        if email:
            log.debug('%s subject=%r', message_id, email.subject)
        return result

    def process(
        self, message_id: str, *, apply: bool, force: bool = False
    ) -> PipelineResult:
        """Run one message through the pipeline. Never raises for per-email failures.

        AuthError and ConfigError are fatal and propagate.
        """
        step = 'fetch'
        email: EmailMessage | None = None
        classification: ClassificationResult | None = None
        decision: LabelDecision | None = None
        try:
            try:
                email = self._gmail.get_message(message_id)
            except MessageNotFoundError:
                return self._result(
                    message_id, PipelineStatus.SKIPPED, skip_reason=SkipReason.NOT_FOUND
                )

            step = 'skip_check'
            managed = self._labels.existing_ids(self._criteria.managed_label_names())
            present = managed & set(email.label_ids)
            if present and not force:
                return self._result(
                    message_id,
                    PipelineStatus.SKIPPED,
                    email,
                    skip_reason=SkipReason.ALREADY_LABELLED,
                )

            step = 'anonymize'
            anonymized = self._anonymizer.anonymize(
                email, max_body_chars=self._max_body_chars
            )

            step = 'classify'
            classification = self._classifier.classify(anonymized)

            step = 'decide'
            decision = decide(classification, self._criteria)
            if decision.label_name is None:
                return self._result(
                    message_id,
                    PipelineStatus.NO_LABEL,
                    email,
                    classification=classification,
                    decision=decision,
                )

            step = 'apply'
            if not apply:
                return self._result(
                    message_id,
                    PipelineStatus.WOULD_LABEL,
                    email,
                    classification=classification,
                    decision=decision,
                    label_id=self._labels.find_id(decision.label_name),
                )
            label_id = self._labels.ensure(decision.label_name)
            remove = (present - {label_id}) if force else set()
            self._gmail.modify_labels(message_id, add=[label_id], remove=sorted(remove))
            return self._result(
                message_id,
                PipelineStatus.LABELLED,
                email,
                classification=classification,
                decision=decision,
                applied=True,
                label_id=label_id,
            )
        except (AuthError, ConfigError):
            raise
        except Exception as e:
            return self._result(
                message_id,
                PipelineStatus.ERROR,
                email,
                classification=classification,
                decision=decision,
                error=f'{step}: {type(e).__name__}: {e}',
                retryable=bool(getattr(e, 'transient', False)),
            )

    def process_many(
        self, message_ids: Iterable[str], *, apply: bool, force: bool = False
    ) -> Iterator[PipelineResult]:
        """Process each id in order, yielding results as they complete."""
        for message_id in message_ids:
            yield self.process(message_id, apply=apply, force=force)

    def close(self) -> None:
        """Close the classifier."""
        self._classifier.close()

    def __enter__(self) -> 'Pipeline':
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

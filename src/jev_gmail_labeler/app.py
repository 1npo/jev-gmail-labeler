"""Factories that wire configuration into runtime objects."""

from collections.abc import Mapping

from jev_gmail_labeler.anonymize import Anonymizer
from jev_gmail_labeler.config import AppConfig, require, resolve_typesafe_api_key
from jev_gmail_labeler.criteria import load_criteria
from jev_gmail_labeler.google_api.auth import load_credentials
from jev_gmail_labeler.google_api.gmail import GmailClient
from jev_gmail_labeler.google_api.labels import LabelManager
from jev_gmail_labeler.google_api.pubsub import PubSubPuller
from jev_gmail_labeler.google_api.quota import QuotaLimiter
from jev_gmail_labeler.pipeline import Pipeline
from jev_gmail_labeler.service import LabelerService
from jev_gmail_labeler.state import StateStore
from jev_gmail_labeler.typesafe_api.classifier import JevClassifier


def _gmail(config: AppConfig, credentials: object) -> GmailClient:
    return GmailClient(
        credentials,
        user_id=config.gmail_user,
        quota=QuotaLimiter(config.gmail_quota_units_per_minute),
    )


def build_gmail(config: AppConfig) -> GmailClient:
    """Create a Gmail client from the stored OAuth token."""
    return _gmail(config, load_credentials(config.token_file))


def build_pipeline(
    config: AppConfig, *, gmail: GmailClient, environ: Mapping[str, str]
) -> Pipeline:
    """Create the labelling pipeline from the criteria file and API key."""
    criteria = load_criteria(config.criteria_file)
    classifier = JevClassifier(
        criteria,
        api_key=resolve_typesafe_api_key(config, environ),
        model=config.jev_model,
        timeout=config.jev_timeout_seconds,
    )
    return Pipeline(
        gmail=gmail,
        labels=LabelManager(gmail),
        anonymizer=Anonymizer(config.spacy_model),
        classifier=classifier,
        criteria=criteria,
        max_body_chars=config.max_body_chars,
    )


def build_service(
    config: AppConfig, *, environ: Mapping[str, str], dry_run: bool
) -> LabelerService:
    """Create the listener service; one set of Google credentials is shared."""
    require(config, 'pubsub_topic', 'pubsub_subscription', command='listen')
    assert config.pubsub_subscription is not None
    credentials = load_credentials(config.token_file)
    gmail = _gmail(config, credentials)
    pipeline = build_pipeline(config, gmail=gmail, environ=environ)
    return LabelerService(
        config=config,
        gmail=gmail,
        puller=PubSubPuller(config.pubsub_subscription, credentials),
        pipeline=pipeline,
        state=StateStore(config.state_db),
        dry_run=dry_run,
    )

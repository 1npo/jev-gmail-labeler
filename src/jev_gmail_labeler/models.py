"""Pipeline dataclasses with JSON-safe (de)serialization."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PipelineStatus(StrEnum):
    """Final outcome of processing one email."""

    LABELLED = 'labelled'
    WOULD_LABEL = 'would_label'
    NO_LABEL = 'no_label'
    SKIPPED = 'skipped'
    ERROR = 'error'


class DecisionReason(StrEnum):
    """Why a label decision was made."""

    MATCHED = 'matched'
    NO_MATCH = 'no_match'
    LOW_CONFIDENCE = 'low_confidence'


class SkipReason(StrEnum):
    """Why an email was skipped."""

    ALREADY_LABELLED = 'already_labelled'
    NOT_FOUND = 'not_found'


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A parsed Gmail message."""

    id: str
    thread_id: str
    history_id: str
    internal_date_ms: int
    label_ids: tuple[str, ...]
    sender: str
    to: str
    cc: str
    reply_to: str
    date: str
    subject: str
    snippet: str
    body_text: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict."""
        return {
            'id': self.id,
            'thread_id': self.thread_id,
            'history_id': self.history_id,
            'internal_date_ms': self.internal_date_ms,
            'label_ids': list(self.label_ids),
            'sender': self.sender,
            'to': self.to,
            'cc': self.cc,
            'reply_to': self.reply_to,
            'date': self.date,
            'subject': self.subject,
            'snippet': self.snippet,
            'body_text': self.body_text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'EmailMessage':
        """Build from the output of ``to_dict``."""
        return cls(
            id=data['id'],
            thread_id=data['thread_id'],
            history_id=data['history_id'],
            internal_date_ms=data['internal_date_ms'],
            label_ids=tuple(data['label_ids']),
            sender=data['sender'],
            to=data['to'],
            cc=data['cc'],
            reply_to=data['reply_to'],
            date=data['date'],
            subject=data['subject'],
            snippet=data['snippet'],
            body_text=data['body_text'],
        )


@dataclass(frozen=True, slots=True)
class AnonymizedEmail:
    """An email whose body has had personal details replaced."""

    id: str
    sender: str
    to: str
    reply_to: str
    date: str
    subject: str
    body: str
    body_truncated: bool

    def to_jev_state(self) -> dict[str, str]:
        """Return the state sent to Jev."""
        return {
            'from': self.sender,
            'to': self.to,
            'reply_to': self.reply_to,
            'date': self.date,
            'subject': self.subject,
            'body': self.body,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict."""
        return {
            'id': self.id,
            'sender': self.sender,
            'to': self.to,
            'reply_to': self.reply_to,
            'date': self.date,
            'subject': self.subject,
            'body': self.body,
            'body_truncated': self.body_truncated,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'AnonymizedEmail':
        """Build from the output of ``to_dict``."""
        return cls(
            id=data['id'],
            sender=data['sender'],
            to=data['to'],
            reply_to=data['reply_to'],
            date=data['date'],
            subject=data['subject'],
            body=data['body'],
            body_truncated=data['body_truncated'],
        )


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Jev's answer for one email."""

    message_id: str
    category: str
    confidence: float
    probabilities: dict[str, float]
    model: str
    request_id: str | None
    input_tokens: int | None
    cost_usd: float | None
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict."""
        return {
            'message_id': self.message_id,
            'category': self.category,
            'confidence': self.confidence,
            'probabilities': dict(self.probabilities),
            'model': self.model,
            'request_id': self.request_id,
            'input_tokens': self.input_tokens,
            'cost_usd': self.cost_usd,
            'elapsed_ms': self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'ClassificationResult':
        """Build from the output of ``to_dict``."""
        return cls(
            message_id=data['message_id'],
            category=data['category'],
            confidence=data['confidence'],
            probabilities=dict(data['probabilities']),
            model=data['model'],
            request_id=data['request_id'],
            input_tokens=data['input_tokens'],
            cost_usd=data['cost_usd'],
            elapsed_ms=data['elapsed_ms'],
        )


@dataclass(frozen=True, slots=True)
class LabelDecision:
    """Which Gmail label (if any) a classification maps to."""

    message_id: str
    category: str
    reason: DecisionReason
    label_name: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict."""
        return {
            'message_id': self.message_id,
            'category': self.category,
            'reason': self.reason.value,
            'label_name': self.label_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'LabelDecision':
        """Build from the output of ``to_dict``."""
        return cls(
            message_id=data['message_id'],
            category=data['category'],
            reason=DecisionReason(data['reason']),
            label_name=data['label_name'],
        )


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Everything that happened to one email in the pipeline."""

    message_id: str
    thread_id: str | None
    date: str | None
    sender: str | None
    subject: str | None
    status: PipelineStatus
    skip_reason: SkipReason | None
    classification: ClassificationResult | None
    decision: LabelDecision | None
    applied: bool
    label_id: str | None
    error: str | None
    retryable: bool
    processed_at: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict."""
        return {
            'message_id': self.message_id,
            'thread_id': self.thread_id,
            'date': self.date,
            'sender': self.sender,
            'subject': self.subject,
            'status': self.status.value,
            'skip_reason': self.skip_reason.value if self.skip_reason else None,
            'classification': (
                self.classification.to_dict() if self.classification else None
            ),
            'decision': self.decision.to_dict() if self.decision else None,
            'applied': self.applied,
            'label_id': self.label_id,
            'error': self.error,
            'retryable': self.retryable,
            'processed_at': self.processed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'PipelineResult':
        """Build from the output of ``to_dict``."""
        skip = data['skip_reason']
        classification = data['classification']
        decision = data['decision']
        return cls(
            message_id=data['message_id'],
            thread_id=data['thread_id'],
            date=data['date'],
            sender=data['sender'],
            subject=data['subject'],
            status=PipelineStatus(data['status']),
            skip_reason=SkipReason(skip) if skip else None,
            classification=(
                ClassificationResult.from_dict(classification) if classification else None
            ),
            decision=LabelDecision.from_dict(decision) if decision else None,
            applied=data['applied'],
            label_id=data['label_id'],
            error=data['error'],
            retryable=data['retryable'],
            processed_at=data['processed_at'],
        )

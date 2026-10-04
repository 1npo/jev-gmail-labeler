"""Classify anonymized emails with Jev through the TypeSafe API."""

import time
from types import TracebackType

from typesafe_sdk import (
    Choice,
    RetryPolicy,
    TypeSafeAPIConnectionError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAuthenticationError,
    TypeSafeBadRequestError,
    TypeSafeClient,
    TypeSafeError,
    TypeSafeInternalServerError,
    TypeSafePermissionDeniedError,
    TypeSafeRateLimitError,
    TypeSafeUnprocessableEntityError,
)

from jev_gmail_labeler.criteria import Criteria
from jev_gmail_labeler.errors import AuthError, ClassificationError, LabelerError
from jev_gmail_labeler.logging_setup import register_secret
from jev_gmail_labeler.models import AnonymizedEmail, ClassificationResult

QUESTION_ID = 'category'
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000

_AUTH_ERRORS = (TypeSafeAuthenticationError, TypeSafePermissionDeniedError)
_PERMANENT_ERRORS = (TypeSafeBadRequestError, TypeSafeUnprocessableEntityError)
_TRANSIENT_ERRORS = (
    TypeSafeRateLimitError,
    TypeSafeInternalServerError,
    TypeSafeAPIConnectionError,
    TypeSafeAPIResponseValidationError,
)


def build_questions(criteria: Criteria) -> dict[str, Choice]:
    """Build the single Jev question for a criteria file."""
    return {
        QUESTION_ID: Choice(
            instructions=criteria.instructions, criteria=criteria.choice_criteria()
        )
    }


def cost_usd(input_tokens: int | None) -> float | None:
    """Estimate the cost of a request from its input tokens."""
    return None if input_tokens is None else input_tokens * USD_PER_INPUT_TOKEN


def _map_error(e: TypeSafeError) -> LabelerError:
    message = f'{type(e).__name__}: {e}'
    if isinstance(e, _AUTH_ERRORS):
        return AuthError('TypeSafe API key was rejected')
    return ClassificationError(message, transient=isinstance(e, _TRANSIENT_ERRORS))


class JevClassifier:
    """Ask Jev which category an email belongs to."""

    def __init__(
        self,
        criteria: Criteria,
        *,
        api_key: str,
        model: str = 'jev-latest',
        timeout: float = 10.0,
        client: TypeSafeClient | None = None,
    ) -> None:
        register_secret(api_key)
        self._questions = build_questions(criteria)
        self._valid_choices = set(criteria.choice_criteria())
        self._client = client or TypeSafeClient(
            api_key=api_key,
            model=model,
            timeout=timeout,
            retry=RetryPolicy(max_retries=4, timeout=60.0),
        )

    def classify(self, email: AnonymizedEmail) -> ClassificationResult:
        """Classify one email; SDK failures become LabelerError subclasses."""
        started = time.perf_counter()
        try:
            response = self._client.system_one(
                state=email.to_jev_state(), questions=self._questions
            )
        except TypeSafeError as e:
            raise _map_error(e) from e
        elapsed_ms = (time.perf_counter() - started) * 1000
        answer = response.choices[QUESTION_ID]
        if answer.choice not in self._valid_choices:
            raise ClassificationError(
                f'Jev returned unknown category "{answer.choice}"', transient=False
            )
        input_tokens = response.usage.input_tokens
        return ClassificationResult(
            message_id=email.id,
            category=answer.choice,
            confidence=answer.confidence,
            probabilities=dict(answer.probabilities),
            model=response.model,
            request_id=response.request_id,
            input_tokens=input_tokens,
            cost_usd=cost_usd(input_tokens),
            elapsed_ms=elapsed_ms,
        )

    def close(self) -> None:
        """Release the HTTP client."""
        self._client.close()

    def __enter__(self) -> 'JevClassifier':
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

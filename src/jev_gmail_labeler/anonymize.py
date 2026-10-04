"""Replace personal details in email bodies with placeholders such as <PERSON>."""

from typing import Any

from jev_gmail_labeler.errors import AnonymizationError, ConfigError
from jev_gmail_labeler.models import AnonymizedEmail, EmailMessage


class Anonymizer:
    """Presidio-based anonymizer. Heavy NLP models load on first use."""

    def __init__(
        self, spacy_model: str = 'en_core_web_md', *, language: str = 'en'
    ) -> None:
        self._spacy_model = spacy_model
        self._language = language
        self._cached: tuple[Any, Any] | None = None

    def _engines(self) -> tuple[Any, Any]:
        if self._cached is None:
            # Imported here so importing this package never pulls in spaCy.
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            from presidio_anonymizer import AnonymizerEngine

            provider = NlpEngineProvider(
                nlp_configuration={
                    'nlp_engine_name': 'spacy',
                    'models': [
                        {'lang_code': self._language, 'model_name': self._spacy_model}
                    ],
                }
            )
            try:
                nlp_engine = provider.create_engine()
            except OSError as e:
                raise ConfigError(
                    f"spaCy model '{self._spacy_model}' is not installed"
                ) from e
            analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine, supported_languages=[self._language]
            )
            self._cached = (analyzer, AnonymizerEngine())
        return self._cached

    def anonymize_text(self, text: str) -> str:
        """Return ``text`` with detected personal details replaced by placeholders."""
        if not text:
            return ''
        analyzer, anonymizer = self._engines()
        from presidio_anonymizer.entities import OperatorConfig

        try:
            found = analyzer.analyze(text=text, language=self._language)
            result = anonymizer.anonymize(
                text=text,
                analyzer_results=found,
                operators={'DEFAULT': OperatorConfig('replace')},
            )
        except Exception as e:
            # Deliberately omit the exception text: it may quote the email body.
            raise AnonymizationError(f'Anonymization failed: {type(e).__name__}') from e
        return result.text

    def anonymize(self, email: EmailMessage, *, max_body_chars: int) -> AnonymizedEmail:
        """Truncate the body to ``max_body_chars`` and then anonymize it."""
        truncated = len(email.body_text) > max_body_chars
        body = self.anonymize_text(email.body_text[:max_body_chars])
        return AnonymizedEmail(
            id=email.id,
            sender=email.sender,
            to=email.to,
            reply_to=email.reply_to,
            date=email.date,
            subject=email.subject,
            body=body,
            body_truncated=truncated,
        )

"""Exception hierarchy. Each error says whether retrying later may succeed."""


class LabelerError(Exception):
    """Base class for all errors raised by this package."""

    transient: bool = False

    def __init__(self, message: str, *, transient: bool | None = None) -> None:
        super().__init__(message)
        if transient is not None:
            self.transient = transient


class ConfigError(LabelerError):
    """Invalid or missing configuration (CLI exit 2)."""


class CriteriaError(ConfigError):
    """The email criteria file is invalid; carries every problem found."""

    def __init__(self, errors: list[str], source: str) -> None:
        self.errors = errors
        self.source = source
        super().__init__(f'{source}:\n  ' + '\n  '.join(errors))


class AuthError(LabelerError):
    """Google token or TypeSafe key is missing, invalid or revoked (CLI exit 3)."""


class GmailError(LabelerError):
    """A Gmail API call failed."""

    def __init__(
        self, message: str, *, status: int | None = None, transient: bool | None = None
    ) -> None:
        super().__init__(message, transient=transient)
        self.status = status


class MessageNotFoundError(GmailError):
    """The requested message no longer exists."""


class HistoryExpiredError(GmailError):
    """The history cursor is older than Gmail keeps."""


class PubSubError(LabelerError):
    """A Pub/Sub call failed."""


class ClassificationError(LabelerError):
    """Jev could not classify an email."""


class AnonymizationError(LabelerError):
    """Anonymizing an email body failed."""

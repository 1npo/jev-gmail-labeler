"""Find and lazily create Gmail labels (and their parent labels)."""

from collections.abc import Iterable

from jev_gmail_labeler.errors import GmailError
from jev_gmail_labeler.google_api.gmail import GmailClient, GmailLabel

CONFLICT = 409


class LabelManager:
    """Resolve label names to ids, creating missing labels on demand."""

    def __init__(self, gmail: GmailClient) -> None:
        self._gmail = gmail
        self._cache: dict[str, GmailLabel] | None = None

    def _labels(self, *, reload: bool = False) -> dict[str, GmailLabel]:
        if self._cache is None or reload:
            self._cache = {x.name.casefold(): x for x in self._gmail.list_labels()}
        return self._cache

    def find_id(self, name: str) -> str | None:
        """Return the id of an existing label (case-insensitive), else None."""
        label = self._labels().get(name.casefold())
        return label.id if label else None

    def _create(self, name: str) -> None:
        try:
            label = self._gmail.create_label(name)
        except GmailError as e:
            if e.status != CONFLICT:
                raise
            # Someone created it since we last listed; pick it up.
            if name.casefold() not in self._labels(reload=True):
                raise
            return
        self._labels()[name.casefold()] = label

    def ensure(self, name: str) -> str:
        """Return the label's id, creating it and any missing parents first."""
        existing = self.find_id(name)
        if existing is not None:
            return existing
        parts = name.split('/')
        for depth in range(1, len(parts) + 1):
            partial = '/'.join(parts[:depth])
            if self.find_id(partial) is None:
                self._create(partial)
        label_id = self.find_id(name)
        assert label_id is not None
        return label_id

    def existing_ids(self, names: Iterable[str]) -> set[str]:
        """Return ids of those labels that already exist; never creates."""
        ids = (self.find_id(n) for n in names)
        return {i for i in ids if i is not None}

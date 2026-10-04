"""Email criteria: the categories Jev chooses between and the Gmail labels they map to."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jev_gmail_labeler import files
from jev_gmail_labeler.errors import CriteriaError

DEFAULT_INSTRUCTIONS = (
    'Which category best describes this email? Judge by its main purpose. '
    '`subject` and `body` matter most. Personal details in `body` are replaced by '
    'placeholders such as <PERSON>.'
)
NO_MATCH_ID = '_none'
NO_MATCH_DESCRIPTION = 'None of the other categories fit this email.'

SYSTEM_LABELS = frozenset(
    {'INBOX', 'SENT', 'DRAFT', 'SPAM', 'TRASH', 'UNREAD', 'STARRED', 'IMPORTANT', 'CHAT'}
)
ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
MAX_LABEL_CHARS = 200
MAX_CATEGORIES = 254
_TOP_KEYS = {
    'version',
    'instructions',
    'label_prefix',
    'min_confidence',
    'uncertain_label',
    'no_match_label',
    'categories',
}
_CATEGORY_KEYS = {'id', 'description', 'label'}


@dataclass(frozen=True, slots=True)
class Category:
    """One option Jev can choose; ``label_name`` is the full Gmail label (or None)."""

    id: str
    description: Any
    label_name: str | None


@dataclass(frozen=True, slots=True)
class Criteria:
    """Validated criteria. Label names include the prefix."""

    version: int
    instructions: str
    label_prefix: str
    min_confidence: float
    uncertain_label: str | None
    no_match_label: str | None
    categories: tuple[Category, ...]

    def choice_criteria(self) -> dict[str, Any]:
        """Return the options sent to Jev, with the no-match option last."""
        options = {c.id: c.description for c in self.categories}
        options[NO_MATCH_ID] = NO_MATCH_DESCRIPTION
        return options

    def category(self, category_id: str) -> Category | None:
        """Look up a category by id."""
        for c in self.categories:
            if c.id == category_id:
                return c
        return None

    def managed_label_names(self) -> set[str]:
        """Every Gmail label name this criteria file can apply."""
        names = {c.label_name for c in self.categories}
        names |= {self.uncertain_label, self.no_match_label}
        return {n for n in names if n is not None}


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _label_problem(label: str) -> str | None:
    if not label:
        return 'must not be empty'
    if len(label) > MAX_LABEL_CHARS:
        return f'must be at most {MAX_LABEL_CHARS} characters'
    if '//' in label:
        return 'must not contain "//"'
    if label != label.strip() or label.startswith('/') or label.endswith('/'):
        return 'must not start or end with "/" or whitespace'
    return None


def _full_name(prefix: str, label: str) -> str:
    return f'{prefix}/{label}' if prefix else label


def _check_label(
    value: Any, path: str, prefix: str | None, errors: list[str]
) -> str | None:
    """Validate one label value; return its full name, or None if invalid."""
    if not isinstance(value, str):
        errors.append(f'{path}: must be a string or null')
        return None
    problem = _label_problem(value)
    if problem:
        errors.append(f'{path}: {problem}')
        return None
    if prefix is None:
        return None
    full = _full_name(prefix, value)
    if full.upper() in SYSTEM_LABELS or full.upper().startswith('CATEGORY_'):
        errors.append(f'{path}: "{full}" is reserved by Gmail')
        return None
    return full


def _derive_label(category_id: str) -> str:
    return ' '.join(part.capitalize() for part in category_id.split('_') if part)


def _parse_category(
    raw: Any, index: int, prefix: str | None, seen: set[str], errors: list[str]
) -> Category | None:
    path = f'categories[{index}]'
    if not isinstance(raw, dict):
        errors.append(f'{path}: must be an object')
        return None
    errors.extend(f'{path}.{k}: unknown key' for k in raw if k not in _CATEGORY_KEYS)
    before = len(errors)

    category_id = raw.get('id')
    if category_id is None:
        errors.append(f'{path}.id: required')
    elif not isinstance(category_id, str) or not ID_RE.match(category_id):
        errors.append(f'{path}.id: must match {ID_RE.pattern} (got "{category_id}")')
    elif category_id in seen:
        errors.append(f'{path}.id: duplicate id "{category_id}"')
    else:
        seen.add(category_id)

    description = raw.get('description')
    if isinstance(description, str) and not description:
        errors.append(f'{path}.description: must not be empty')
    elif description is not None and not isinstance(description, str | dict | list):
        errors.append(f'{path}.description: must be a string, object, array or null')

    label_name = None
    if 'label' not in raw:
        if isinstance(category_id, str) and ID_RE.match(category_id):
            label_name = _check_label(
                _derive_label(category_id), f'{path}.label', prefix, errors
            )
    elif raw['label'] is not None:
        label_name = _check_label(raw['label'], f'{path}.label', prefix, errors)

    if len(errors) > before:
        return None
    return Category(id=category_id, description=description, label_name=label_name)


def parse_criteria(data: Any, *, source: str = '<criteria>') -> Criteria:
    """Validate criteria data, collecting every problem before raising CriteriaError."""
    if not isinstance(data, dict):
        raise CriteriaError(['(root): must be a JSON object'], source)
    errors = [f'{k}: unknown key' for k in data if k not in _TOP_KEYS]

    version = data.get('version')
    if 'version' not in data:
        errors.append('version: required')
    elif isinstance(version, bool) or version != 1:
        errors.append(f'version: must be 1 (got {version!r})')

    instructions = data.get('instructions', DEFAULT_INSTRUCTIONS)
    if not isinstance(instructions, str) or not instructions.strip():
        errors.append('instructions: must be a non-empty string')

    prefix: str | None = data.get('label_prefix', 'Jev')
    if not isinstance(prefix, str):
        errors.append('label_prefix: must be a string')
        prefix = None
    elif prefix and (problem := _label_problem(prefix)):
        errors.append(f'label_prefix: {problem}')
        prefix = None

    min_confidence = data.get('min_confidence', 0.0)
    if not _is_number(min_confidence) or not 0 <= min_confidence <= 1:
        errors.append(
            f'min_confidence: must be a number from 0 to 1 (got {min_confidence!r})'
        )

    extra_labels: dict[str, str | None] = {}
    for key in ('uncertain_label', 'no_match_label'):
        raw = data.get(key)
        extra_labels[key] = (
            None if raw is None else _check_label(raw, key, prefix, errors)
        )

    categories: list[Category] = []
    raw_categories = data.get('categories')
    if 'categories' not in data:
        errors.append('categories: required')
    elif not isinstance(raw_categories, list):
        errors.append('categories: must be an array')
    elif not 1 <= len(raw_categories) <= MAX_CATEGORIES:
        errors.append(f'categories: must have 1 to {MAX_CATEGORIES} items')
    else:
        seen: set[str] = set()
        for i, raw in enumerate(raw_categories):
            category = _parse_category(raw, i, prefix, seen, errors)
            if category is not None:
                categories.append(category)

    if errors:
        raise CriteriaError(errors, source)
    return Criteria(
        version=1,
        instructions=instructions,
        label_prefix=prefix,
        min_confidence=float(min_confidence),
        uncertain_label=extra_labels['uncertain_label'],
        no_match_label=extra_labels['no_match_label'],
        categories=tuple(categories),
    )


def load_criteria(path: Path) -> Criteria:
    """Read and validate a criteria file."""
    try:
        data = files.read_json(path)
    except FileNotFoundError:
        raise CriteriaError(['file not found'], str(path)) from None
    except ValueError as e:
        raise CriteriaError([str(e)], str(path)) from e
    return parse_criteria(data, source=str(path))


EXAMPLE_CRITERIA: dict[str, Any] = {
    'version': 1,
    'label_prefix': 'Jev',
    'min_confidence': 0.0,
    'categories': [
        {
            'id': 'marketing',
            'description': (
                'An advertisement, product announcement, or other marketing email'
            ),
        },
        {
            'id': 'request_for_feedback',
            'description': (
                'An email asking me to leave a review or provide feedback on a product '
                'or service'
            ),
        },
        {'id': 'news', 'description': 'A news report or article'},
        {'id': 'receipt', 'description': None},
        {
            'id': 'bill',
            'description': 'A utility bill, eg., internet, phone, electricity, gas, etc',
        },
        {
            'id': 'personal',
            'description': (
                'A personal email between me and another individual, not an email from '
                'a company or agency'
            ),
        },
        {
            'id': 'order_confirmation',
            'description': (
                'A confirmation that I have a placed an order, eg. at an e-commerce store'
            ),
        },
        {
            'id': 'subscription_confirmation',
            'description': (
                'A confirmation that I signed up for or cancelled a subscription'
            ),
        },
        {
            'id': 'order_shipped',
            'description': (
                'A notice that a package has shipped or delivery has been scheduled'
            ),
        },
        {'id': 'order_delivered', 'description': None},
        {
            'id': 'delivery_update',
            'description': (
                'A notice that there has been a delay or other change to an in-flight '
                'delivery'
            ),
        },
        {
            'id': 'bank_statement',
            'description': (
                'An email containing a statement or transaction history from a '
                'financial institution'
            ),
        },
        {
            'id': 'credit_info',
            'description': (
                'An email from a financial institution about my credit or credit report'
            ),
        },
        {
            'id': 'policy_change_notice',
            'description': (
                'A notice from a company or agency about a change to '
                'one of their policies'
            ),
        },
        {
            'id': 'info_change_notice',
            'description': (
                'A notice from a company or agency about a change to my own information'
            ),
        },
        {
            'id': 'informed_delivery',
            'description': 'A daily "Informed Delivery" email from USPS',
        },
        {
            'id': 'tickets',
            'description': (
                'An acknowledgement that a ticket was opened, or someone responded to '
                'that ticket'
            ),
        },
        {'id': 'forgot_password', 'description': None},
        {'id': 'magic_link', 'description': 'An email containing a magic sign-in link'},
        {
            'id': 'totp',
            'description': 'An email containing a time-based one-time password',
        },
        {
            'id': 'new_login',
            'description': (
                'An email indicating that my account logged in from a new device'
            ),
        },
        {
            'id': 'note_to_self',
            'description': (
                'An email that I sent to myself (including emails I send '
                'from my personal '
                'email to my work emails)'
            ),
        },
        {'id': 'membership_renewal', 'description': None},
        {'id': 'backup', 'description': 'An email containing a daily data backup'},
        {
            'id': 'mention',
            'description': 'A notice that someone mentioned me in a chat channel',
        },
        {
            'id': 'networking_activity',
            'description': (
                'An email showing activity from a specific person on a '
                'social media website'
            ),
        },
        {
            'id': 'networking_invite',
            'description': (
                'A "friend request"-type invitation from a specific person on a social '
                'media website'
            ),
        },
        {
            'id': 'data_breach',
            'description': (
                'An email indicating that my data was found in a data leak or breach'
            ),
        },
    ],
}

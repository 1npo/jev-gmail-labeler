"""Layered configuration: defaults < env < config file < --config-json < CLI flags."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from platformdirs import user_config_path, user_state_path

from jev_gmail_labeler import files
from jev_gmail_labeler.errors import ConfigError

APP_NAME = 'jev-gmail-labeler'
ENV_PREFIX = 'JEV_LABELER_'
LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')
TOPIC_RE = re.compile(r'^projects/[^/]+/topics/[^/]+$')
SUBSCRIPTION_RE = re.compile(r'^projects/[^/]+/subscriptions/[^/]+$')


def default_config_dir() -> Path:
    """Return the per-user config directory."""
    return user_config_path(APP_NAME)


def default_state_dir() -> Path:
    """Return the per-user state directory."""
    return user_state_path(APP_NAME)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Fully resolved settings."""

    credentials_file: Path
    token_file: Path
    criteria_file: Path
    state_db: Path
    typesafe_api_key_file: Path | None
    jev_model: str
    jev_timeout_seconds: float
    gmail_user: str
    pubsub_topic: str | None
    pubsub_subscription: str | None
    max_body_chars: int
    spacy_model: str
    log_level: str
    max_attempts: int
    idle_resync_minutes: int
    catchup_max_messages: int
    pull_max_messages: int
    pull_timeout_seconds: float
    gmail_quota_units_per_minute: int
    config_file: Path | None = None


# name -> (kind, nullable, CLI flag)
_SCHEMA: dict[str, tuple[str, bool, str | None]] = {
    'credentials_file': ('path', False, '--credentials-file'),
    'token_file': ('path', False, '--token-file'),
    'criteria_file': ('path', False, '--criteria-file'),
    'state_db': ('path', False, '--state-db'),
    'typesafe_api_key_file': ('path', True, '--typesafe-api-key-file'),
    'jev_model': ('str', False, '--jev-model'),
    'jev_timeout_seconds': ('float', False, None),
    'gmail_user': ('str', False, None),
    'pubsub_topic': ('str', True, '--topic'),
    'pubsub_subscription': ('str', True, '--subscription'),
    'max_body_chars': ('int', False, '--max-body-chars'),
    'spacy_model': ('str', False, None),
    'log_level': ('str', False, '--log-level'),
    'max_attempts': ('int', False, None),
    'idle_resync_minutes': ('int', False, None),
    'catchup_max_messages': ('int', False, None),
    'pull_max_messages': ('int', False, None),
    'pull_timeout_seconds': ('float', False, None),
    'gmail_quota_units_per_minute': ('int', False, None),
}

# name -> (low, high); None means unbounded on that side
_RANGES: dict[str, tuple[float | None, float | None]] = {
    'max_body_chars': (100, 60000),
    'max_attempts': (1, None),
    'idle_resync_minutes': (1, None),
    'catchup_max_messages': (1, 1000),
    'pull_max_messages': (1, 100),
    'pull_timeout_seconds': (1, 60),
    'gmail_quota_units_per_minute': (100, 6000),
}

API_KEY_MESSAGE = (
    'typesafe_api_key is not allowed in config; '
    'use TYPESAFE_API_KEY or typesafe_api_key_file'
)


def _env_name(key: str) -> str:
    return ENV_PREFIX + key.upper()


def _defaults() -> dict[str, Any]:
    config_dir = default_config_dir()
    return {
        'credentials_file': config_dir / 'credentials.json',
        'token_file': config_dir / 'token.json',
        'criteria_file': config_dir / 'criteria.json',
        'state_db': default_state_dir() / 'state.sqlite3',
        'typesafe_api_key_file': None,
        'jev_model': 'jev-latest',
        'jev_timeout_seconds': 10.0,
        'gmail_user': 'me',
        'pubsub_topic': None,
        'pubsub_subscription': None,
        'max_body_chars': 8000,
        'spacy_model': 'en_core_web_md',
        'log_level': 'INFO',
        'max_attempts': 5,
        'idle_resync_minutes': 15,
        'catchup_max_messages': 200,
        'pull_max_messages': 10,
        'pull_timeout_seconds': 20.0,
        'gmail_quota_units_per_minute': 5000,
    }


def _to_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _coerce(
    key: str, value: Any, *, base: Path, source: str, from_text: bool, errors: list[str]
) -> tuple[bool, Any]:
    """Convert one raw value to its schema type. Returns (ok, value)."""
    kind, nullable, _ = _SCHEMA[key]
    if value is None or (from_text and value == 'null'):
        if nullable:
            return True, None
        errors.append(f'{source}: {key} must not be null')
        return False, None
    if from_text:
        try:
            if kind == 'int':
                value = int(value)
            elif kind == 'float':
                value = float(value)
        except ValueError:
            errors.append(f'{source}: {key} must be a {kind} (got "{value}")')
            return False, None
    if kind in ('str', 'path'):
        if not isinstance(value, str) or not value:
            errors.append(f'{source}: {key} must be a non-empty string')
            return False, None
        return True, _to_path(value, base) if kind == 'path' else value
    is_number = isinstance(value, int | float) and not isinstance(value, bool)
    if kind == 'int' and (not is_number or int(value) != value):
        errors.append(f'{source}: {key} must be an integer')
        return False, None
    if kind == 'float' and not is_number:
        errors.append(f'{source}: {key} must be a number')
        return False, None
    return True, int(value) if kind == 'int' else float(value)


def _apply_layer(
    merged: dict[str, Any],
    raw: Mapping[str, Any],
    *,
    base: Path,
    source: str,
    from_text: bool,
    errors: list[str],
) -> None:
    for key, value in raw.items():
        ok, converted = _coerce(
            key, value, base=base, source=source, from_text=from_text, errors=errors
        )
        if ok:
            merged[key] = converted


def _json_layer(raw: Any, source: str) -> Mapping[str, Any]:
    """Check a JSON config object's shape and return it."""
    if not isinstance(raw, dict):
        raise ConfigError(f'{source}: must be a JSON object')
    if 'typesafe_api_key' in raw:
        raise ConfigError(API_KEY_MESSAGE)
    unknown = [f'{source}: unknown key "{k}"' for k in raw if k not in _SCHEMA]
    if unknown:
        raise ConfigError('\n'.join(unknown))
    return raw


def _read_config_file(config_path: Path | None) -> tuple[Path | None, Any]:
    """Load the explicit or default config file (the default may be absent)."""
    explicit = config_path is not None
    path = config_path.expanduser() if explicit else default_config_dir() / 'config.json'
    try:
        return path, files.read_json(path)
    except FileNotFoundError:
        if explicit:
            raise ConfigError(f'{path}: file not found') from None
        return None, None
    except ValueError as e:
        raise ConfigError(str(e)) from e


def _validate(merged: dict[str, Any], errors: list[str]) -> None:
    if merged['log_level'] not in LOG_LEVELS:
        errors.append(f'log_level: must be one of {", ".join(LOG_LEVELS)}')
    if merged['jev_timeout_seconds'] <= 0:
        errors.append('jev_timeout_seconds: must be greater than 0')
    for key, (low, high) in _RANGES.items():
        value = merged[key]
        if (low is not None and value < low) or (high is not None and value > high):
            if high is None:
                errors.append(f'{key}: must be at least {low} (got {value})')
            else:
                errors.append(f'{key}: must be between {low} and {high} (got {value})')
    for key, pattern, shape in (
        ('pubsub_topic', TOPIC_RE, 'projects/<project>/topics/<topic>'),
        (
            'pubsub_subscription',
            SUBSCRIPTION_RE,
            'projects/<project>/subscriptions/<name>',
        ),
    ):
        value = merged[key]
        if value is not None and not pattern.match(value):
            errors.append(f'{key}: must look like {shape} (got "{value}")')


def load_config(
    *,
    cli: Mapping[str, Any],
    config_path: Path | None,
    config_json: str | None,
    environ: Mapping[str, str],
) -> AppConfig:
    """Merge every layer, validate, and return the final settings."""
    errors: list[str] = []
    merged = _defaults()
    cwd = Path.cwd()

    env_values = {k: environ[_env_name(k)] for k in _SCHEMA if environ.get(_env_name(k))}
    for key, value in env_values.items():
        _apply_layer(
            merged,
            {key: value},
            base=cwd,
            source=_env_name(key),
            from_text=True,
            errors=errors,
        )

    config_file, file_data = _read_config_file(config_path)
    if config_file is not None:
        layer = _json_layer(file_data, str(config_file))
        _apply_layer(
            merged,
            layer,
            base=config_file.parent,
            source=str(config_file),
            from_text=False,
            errors=errors,
        )

    if config_json is not None:
        try:
            parsed = json.loads(config_json)
        except json.JSONDecodeError as e:
            raise ConfigError(f'--config-json: invalid JSON: {e}') from e
        layer = _json_layer(parsed, '--config-json')
        _apply_layer(
            merged,
            layer,
            base=cwd,
            source='--config-json',
            from_text=False,
            errors=errors,
        )

    given = {k: v for k, v in cli.items() if k in _SCHEMA and v is not None}
    _apply_layer(
        merged, given, base=cwd, source='command line', from_text=False, errors=errors
    )

    if not errors:
        _validate(merged, errors)
    if errors:
        raise ConfigError('\n'.join(errors))
    return AppConfig(**merged, config_file=config_file)


def require(config: AppConfig, *keys: str, command: str) -> None:
    """Raise ConfigError if any of ``keys`` is unset."""
    for key in keys:
        if getattr(config, key) is None:
            flag = _SCHEMA[key][2]
            how = f'with {flag}, ' if flag else ''
            raise ConfigError(
                f'{key} is required for `{command}`. '
                f'Set it in the config file, {how}or {_env_name(key)}.'
            )


def resolve_typesafe_api_key(config: AppConfig, environ: Mapping[str, str]) -> str:
    """Return the TypeSafe API key from its file or the environment."""
    if config.typesafe_api_key_file is not None:
        try:
            key = files.read_text(config.typesafe_api_key_file).strip()
        except OSError as e:
            raise ConfigError(
                f'Cannot read typesafe_api_key_file {config.typesafe_api_key_file}: {e}'
            ) from e
    else:
        key = environ.get('TYPESAFE_API_KEY', '').strip()
    if not key:
        raise ConfigError('Set TYPESAFE_API_KEY or typesafe_api_key_file')
    return key


def redacted_dict(config: AppConfig, environ: Mapping[str, str]) -> dict[str, Any]:
    """Return the settings as JSON-safe data, never including the API key."""
    data: dict[str, Any] = {}
    for f in fields(config):
        value = getattr(config, f.name)
        data[f.name] = str(value) if isinstance(value, Path) else value
    try:
        resolve_typesafe_api_key(config, environ)
        data['typesafe_api_key'] = 'set'
    except ConfigError:
        data['typesafe_api_key'] = 'not set'
    return data

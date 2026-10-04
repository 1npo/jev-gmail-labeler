"""Command-line interface."""

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jev_gmail_labeler import __version__
from jev_gmail_labeler.app import build_gmail, build_pipeline, build_service
from jev_gmail_labeler.config import (
    LOG_LEVELS,
    AppConfig,
    load_config,
    redacted_dict,
    require,
)
from jev_gmail_labeler.criteria import EXAMPLE_CRITERIA, load_criteria
from jev_gmail_labeler.errors import AuthError, ConfigError
from jev_gmail_labeler.google_api.auth import run_consent_flow
from jev_gmail_labeler.logging_setup import configure_logging
from jev_gmail_labeler.models import PipelineResult, PipelineStatus
from jev_gmail_labeler.output import results_to_json, summary_line, write_results
from jev_gmail_labeler.service import renew_watch
from jev_gmail_labeler.state import StateStore

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_ITEM_ERRORS = 4
EXIT_INTERRUPTED = 130

QUERY_ONLY_LIMIT = 100


def _count(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'invalid integer: {value!r}') from None
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError('must be between 1 and 1000')
    return number


def _add_flag(
    parser: argparse.ArgumentParser, flag: str, dest: str, **kwargs: Any
) -> None:
    parser.add_argument(flag, dest=dest, default=argparse.SUPPRESS, **kwargs)


def _common(parser: argparse.ArgumentParser) -> None:
    _add_flag(
        parser,
        '--config',
        'config_path',
        type=Path,
        metavar='PATH',
        help='config file (default: <config dir>/config.json if present)',
    )
    _add_flag(
        parser,
        '--config-json',
        'config_json',
        metavar='JSON',
        help='config overrides as a JSON object',
    )
    _add_flag(parser, '--log-level', 'log_level', choices=LOG_LEVELS)
    _add_flag(parser, '--credentials-file', 'credentials_file', metavar='PATH')
    _add_flag(parser, '--token-file', 'token_file', metavar='PATH')


def _jev_flags(parser: argparse.ArgumentParser) -> None:
    _add_flag(parser, '--criteria-file', 'criteria_file', metavar='PATH')
    _add_flag(parser, '--typesafe-api-key-file', 'typesafe_api_key_file', metavar='PATH')
    _add_flag(parser, '--jev-model', 'jev_model')
    _add_flag(parser, '--max-body-chars', 'max_body_chars', type=int, metavar='N')


def _state_flag(parser: argparse.ArgumentParser) -> None:
    _add_flag(parser, '--state-db', 'state_db', metavar='PATH')


def _topic_flag(parser: argparse.ArgumentParser) -> None:
    _add_flag(
        parser,
        '--topic',
        'pubsub_topic',
        metavar='TOPIC',
        help='projects/<project>/topics/<topic>',
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every command."""
    parser = argparse.ArgumentParser(
        prog='jev-gmail-labeler', description='Label Gmail messages with Jev.'
    )
    parser.add_argument('--version', action='version', version=__version__)
    sub = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        _common(p)
        return p

    p = command('auth', 'Authorize access to Gmail and Pub/Sub.')
    p.add_argument(
        '--port', type=int, default=0, help='local port for the OAuth redirect'
    )
    p.add_argument(
        '--no-browser', action='store_true', help='print the URL, do not open it'
    )

    p = command('label', 'Label existing emails now.')
    p.add_argument('--count', type=_count, help='label the newest N inbox emails')
    p.add_argument('--query', help='Gmail search query (up to 100 results)')
    p.add_argument(
        '--id', action='append', dest='ids', metavar='ID', help='message id (repeatable)'
    )
    p.add_argument('--apply', action='store_true', help='apply labels (default: dry run)')
    p.add_argument('--force', action='store_true', help='also reclassify labelled emails')
    p.add_argument('--output', type=Path, metavar='PATH', help='write results to a file')
    p.add_argument('--format', choices=('json', 'csv'), help='output format')
    _jev_flags(p)

    p = command('listen', 'Run the listener service.')
    _topic_flag(p)
    _add_flag(
        p,
        '--subscription',
        'pubsub_subscription',
        metavar='SUBSCRIPTION',
        help='projects/<project>/subscriptions/<name>',
    )
    _state_flag(p)
    _jev_flags(p)
    p.add_argument('--dry-run', action='store_true', help='classify but never label')

    watch = command('watch', 'Manage the Gmail watch.')
    watch_sub = watch.add_subparsers(dest='subcommand', required=True, metavar='ACTION')
    for name, text in (
        ('start', 'Start or renew the watch.'),
        ('stop', 'Stop the watch.'),
        ('status', 'Show watch and sync state.'),
    ):
        wp = watch_sub.add_parser(name, help=text, description=text)
        _common(wp)
        _state_flag(wp)
        if name == 'start':
            _topic_flag(wp)

    crit = command('criteria', 'Work with the criteria file.')
    crit_sub = crit.add_subparsers(dest='subcommand', required=True, metavar='ACTION')
    cp = crit_sub.add_parser('validate', help='Check the criteria file.')
    _common(cp)
    _add_flag(cp, '--criteria-file', 'criteria_file', metavar='PATH')
    cp = crit_sub.add_parser('example', help='Print an example criteria file.')
    _common(cp)

    cfg = command('config', 'Inspect the configuration.')
    cfg_sub = cfg.add_subparsers(dest='subcommand', required=True, metavar='ACTION')
    cp = cfg_sub.add_parser('show', help='Print the resolved configuration.')
    _common(cp)
    return parser


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _opt_float(value: str | None) -> float | None:
    return float(value) if value is not None else None


def _cmd_auth(args: argparse.Namespace, config: AppConfig) -> int:
    run_consent_flow(
        config.credentials_file,
        config.token_file,
        port=args.port,
        open_browser=not args.no_browser,
    )
    print(f'Saved token to {config.token_file}', file=sys.stderr)
    return EXIT_OK


def _select_ids(args: argparse.Namespace, gmail: Any) -> list[str]:
    if args.ids:
        return list(args.ids)
    if args.query is None:
        return gmail.list_message_ids(query='in:inbox', max_results=args.count)
    return gmail.list_message_ids(
        query=args.query, max_results=args.count or QUERY_ONLY_LIMIT
    )


def _cmd_label(args: argparse.Namespace, config: AppConfig) -> int:
    gmail = build_gmail(config)
    ids = _select_ids(args, gmail)
    with build_pipeline(config, gmail=gmail, environ=os.environ) as pipeline:
        results: list[PipelineResult] = list(
            pipeline.process_many(ids, apply=args.apply, force=args.force)
        )
    path: Path | None = args.output
    if path is None:
        print(results_to_json(results))
    else:
        fmt: Literal['json', 'csv'] = args.format or (
            'csv' if path.suffix.lower() == '.csv' else 'json'
        )
        write_results(results, path, fmt)
    print(summary_line(results, path), file=sys.stderr)
    failed = any(r.status is PipelineStatus.ERROR for r in results)
    return EXIT_ITEM_ERRORS if failed else EXIT_OK


def _cmd_listen(args: argparse.Namespace, config: AppConfig) -> int:
    build_service(config, environ=os.environ, dry_run=args.dry_run).run()
    return EXIT_OK


def _watch_status(state: StateStore) -> dict[str, Any]:
    expiration_ms = _opt_float(state.get('watch_expiration_ms'))
    return {
        'history_cursor': state.get_cursor(),
        'watch_expiration': _iso(None if expiration_ms is None else expiration_ms / 1000),
        'watch_renewed_at': _iso(_opt_float(state.get('watch_renewed_at'))),
        'last_sync_at': _iso(_opt_float(state.get('last_sync_at'))),
    }


def _cmd_watch(args: argparse.Namespace, config: AppConfig) -> int:
    action = args.subcommand
    if action == 'start':
        require(config, 'pubsub_topic', command='watch start')
    state = StateStore(config.state_db)
    try:
        if action == 'start':
            assert config.pubsub_topic is not None
            renew_watch(
                build_gmail(config),
                state,
                config.pubsub_topic,
                now=time.time(),
                force=True,
            )
        elif action == 'stop':
            build_gmail(config).stop_watch()
            state.clear_watch()
            print('Watch stopped', file=sys.stderr)
            return EXIT_OK
        print(json.dumps(_watch_status(state), indent=2))
    finally:
        state.close()
    return EXIT_OK


def _cmd_criteria(args: argparse.Namespace, config: AppConfig) -> int:
    if args.subcommand == 'example':
        print(json.dumps(EXAMPLE_CRITERIA, indent=2))
        return EXIT_OK
    criteria = load_criteria(config.criteria_file)
    print(f'OK: {len(criteria.categories)} categories')
    for category in criteria.categories:
        print(f'{category.id} -> {category.label_name or "(no label)"}')
    return EXIT_OK


def _cmd_config(args: argparse.Namespace, config: AppConfig) -> int:
    print(json.dumps(redacted_dict(config, os.environ), indent=2))
    return EXIT_OK


_COMMANDS: dict[str, Callable[[argparse.Namespace, AppConfig], int]] = {
    'auth': _cmd_auth,
    'label': _cmd_label,
    'listen': _cmd_listen,
    'watch': _cmd_watch,
    'criteria': _cmd_criteria,
    'config': _cmd_config,
}


def _parse(
    parser: argparse.ArgumentParser, argv: Sequence[str] | None
) -> argparse.Namespace:
    args = parser.parse_args(argv)
    if args.command == 'label':
        if args.ids and (args.count is not None or args.query is not None):
            parser.error('--id cannot be combined with --count or --query')
        if not args.ids and args.count is None and args.query is None:
            parser.error('one of --count, --query or --id is required')
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run the command and return the process exit code."""
    configure_logging('INFO')
    try:
        args = _parse(build_parser(), argv)
        config = load_config(
            cli=vars(args),
            config_path=getattr(args, 'config_path', None),
            config_json=getattr(args, 'config_json', None),
            environ=os.environ,
        )
        configure_logging(config.log_level)
        return _COMMANDS[args.command](args, config)
    except ConfigError as e:
        log.error('%s', e)
        return EXIT_CONFIG
    except AuthError as e:
        if str(e).startswith('TypeSafe'):
            log.error('%s', e)
        else:
            log.error('Authorization failed: %s. Run `jev-gmail-labeler auth`', e)
        return EXIT_AUTH
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception:
        log.exception('Unexpected error')
        return EXIT_UNEXPECTED

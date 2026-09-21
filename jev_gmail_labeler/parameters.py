import argparse
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 5
DEFAULT_EMAIL_CACHE = 'workspace/email_cache.json'
DEFAULT_ANONYMIZED_EMAIL_CACHE = 'workspace/anonymized_email_cache.json'
DEFAULT_CLASSIFICATION_CACHE = 'workspace/classification_cache.json'
DEFAULT_CREDENTIALS_PATH = 'workspace/credentials.json'
DEFAULT_REPORT_FILE = 'workspace/report.csv'


def get_params() -> dict:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')
    parser_get = subparsers.add_parser(
        'get-emails', help='Fetch and cache email messages from Gmail'
    )
    parser_get.add_argument(
        '--max-results',
        action='store',
        type=int,
        default=DEFAULT_MAX_RESULTS,
        help='Number of emails to fetch',
    )
    parser_get.add_argument(
        '--save-to',
        action='store',
        type=str,
        default=DEFAULT_EMAIL_CACHE,
        help='Cache your email messages to this JSON file',
    )
    parser_get.add_argument(
        '--credentials-path',
        action='store',
        type=str,
        default=DEFAULT_CREDENTIALS_PATH,
        help='Path to your Gmail API credentials file',
    )
    parser_get.add_argument(
        '--query',
        action='store',
        type=str,
        help='Optional Gmail search query',
    )
    parser_anonymize = subparsers.add_parser(
        'anonymize-emails', help='Anonymize cached email messages with presidio'
    )
    parser_anonymize.add_argument(
        '--email-cache-path',
        action='store',
        type=str,
        default=DEFAULT_EMAIL_CACHE,
        help='Get your cached emails from this file',
    )
    parser_anonymize.add_argument(
        '--save-to',
        action='store',
        type=str,
        default=DEFAULT_ANONYMIZED_EMAIL_CACHE,
        help='Cache your anonymized email message payloads to this file',
    )
    parser_classify = subparsers.add_parser(
        'classify-emails', help='Classify cached email messages with Jev'
    )
    parser_classify.add_argument(
        '--anonymized-email-cache-path',
        action='store',
        type=str,
        default=DEFAULT_ANONYMIZED_EMAIL_CACHE,
        help='Get your cached emails from this file',
    )
    parser_classify.add_argument(
        '--save-to',
        action='store',
        type=str,
        default=DEFAULT_CLASSIFICATION_CACHE,
        help='Cache your email classifications to this JSON file',
    )
    parser_label = subparsers.add_parser('label-emails', help='Label classified emails')
    parser_label.add_argument(
        '--classification-cache-path',
        action='store',
        type=str,
        default=DEFAULT_EMAIL_CACHE,
        help='Get your cached emails from this file',
    )
    parser_label.add_argument(
        '--credentials-path',
        action='store',
        type=str,
        default=DEFAULT_CREDENTIALS_PATH,
        help='Path to your Gmail API credentials file',
    )
    parser_report = subparsers.add_parser(
        'report', help='Generate report of email classifications'
    )
    parser_report.add_argument(
        '--classification-cache-path',
        action='store',
        type=str,
        default=DEFAULT_CLASSIFICATION_CACHE,
        help='Get your classified emails from this file',
    )
    parser_report.add_argument(
        '--save-to',
        action='store',
        type=str,
        default=DEFAULT_REPORT_FILE,
        help='Save the report to this file',
    )

    namespace, unknown = parser.parse_known_args()

    if unknown:
        for arg in unknown:
            logger.warning('Ignoring unknown argument: %s', arg)

    max_results = getattr(namespace, 'max_results', DEFAULT_MAX_RESULTS)
    credentials_path = getattr(namespace, 'credentials_path', DEFAULT_CREDENTIALS_PATH)
    email_cache_path = getattr(namespace, 'email_cache_path', DEFAULT_EMAIL_CACHE)
    anonymized_email_cache_path = getattr(
        namespace, 'anonymized_email_cache_path', DEFAULT_ANONYMIZED_EMAIL_CACHE
    )
    classification_cache_path = getattr(
        namespace, 'classification_cache_path', DEFAULT_CLASSIFICATION_CACHE
    )
    query = getattr(namespace, 'query', None)
    command = getattr(namespace, 'command', None)

    match command:
        case 'get-emails':
            save_to = getattr(namespace, 'save_to', DEFAULT_EMAIL_CACHE)
        case 'anonymize-emails':
            save_to = getattr(namespace, 'save_to', DEFAULT_ANONYMIZED_EMAIL_CACHE)
        case 'classify-emails':
            save_to = getattr(namespace, 'save_to', DEFAULT_CLASSIFICATION_CACHE)
        case 'report':
            save_to = getattr(namespace, 'save_to', DEFAULT_REPORT_FILE)
        case _:
            save_to = getattr(namespace, 'save_to', None)

    Path(save_to).parent.mkdir(exist_ok=True, parents=True)

    return {
        'max_results': max_results,
        'credentials_path': credentials_path,
        'email_cache_path': email_cache_path,
        'classification_cache_path': classification_cache_path,
        'anonymized_email_cache_path': anonymized_email_cache_path,
        'query': query,
        'save_to': save_to,
        'command': command,
    }

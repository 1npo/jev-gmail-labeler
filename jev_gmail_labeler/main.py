import asyncio
import logging
from datetime import datetime, timedelta

import pandas as pd
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul

from jev_gmail_labeler import gmail_api_util, parameters, scrub

logger = logging.getLogger(__name__)
logging.basicConfig(
    level='INFO',
    format='%(asctime)s [ %(levelname)-8s ] %(message)s',
    datefmt='%Y-%m-%d %I:%M:%S %p',
)
logging.getLogger('presidio').setLevel(logging.ERROR)
logging.getLogger('presidio-analyzer').setLevel(logging.ERROR)
logging.getLogger('presidio-anonymizer').setLevel(logging.ERROR)

COST_PER_MTOK_IN = 0.042
KEEP_EMAIL_FIELDS = [
    'id',
    'sender',
    'to',
    'reply_to',
    'date',
    'subject',
    'body_text',
]
EMAIL_CRITERIA = {
    'marketing': 'An advertisement, product announcement, or other marketing email',
    'request_for_feedback': 'An email asking me to leave a review or provide feedback on a product or service',
    'news': 'A news report or article',
    'receipt': None,
    'bill': 'A utility bill, eg., internet, phone, electricity, gas, etc',
    'personal': 'A personal email between me and another individual, not an email from a company or agency',
    'order_confirmation': 'A confirmation that I have a placed an order, eg. at an e-commerce store',
    'subscription_confirmation': 'A confirmation that I signed up for or cancelled a subscription',
    'order_shipped': 'A notice that a package has shipped or delivery has been scheduled',
    'order_delivered': None,
    'delivery_update': 'A notice that there has been a delay or other change to an in-flight delivery',
    'bank_statement': 'An email containing a statement or transaction history from a financial institution',
    'credit_info': 'An email from a financial instution about my credit or credit report',
    'policy_change_notice': 'A notice from a company or agency about a change to one of their policies',
    'info_change_notice': 'A notice from a company or agency about a change to my own information',
    'informed_delivery': 'A daily "Informed Delivery" email from USPS',
    'tickets': 'An acknowledgement that a ticket was opened, or someone responded to that ticket',
    'forgot_password': None,
    'magic_link': 'An email containing a magic sign-in link',
    'totp': 'An email containing a time-based one-time password',
    'new_login': 'An email indicating that my account logged in from a new device',
    'note_to_self': 'An email that I sent to myself (including emails I send from my personal email to my work emails)',
    'membership_renewal': None,
    'backup': 'An email containing a daily data backup',
    'other': None,
    'mention': 'A notice that someone mentioned me in a chat channel',
    'networking_activity': 'An email showing activity from a specific person on a social media website',
    'networking_invite': 'An "friend request"-type invitation from a specific person on a social media website',
    'data_breach': 'An email indicating that my data was found in a data leak or breach',
}


def generate_report(params: dict):
    emails = gmail_api_util.load_json_cache(params.get('classification_cache_path'))
    records = []
    for email in emails:
        new_record = email.get('email')
        new_record.update(email.get('classification'))
        records.append(new_record)
    save_to = params.get('save_to')
    df = pd.DataFrame(records)
    df.to_csv(save_to)
    logger.info('Saved %s records to a CSV file: %s', len(df.index), save_to)


async def get_email_classifications(email_message: dict) -> dict:
    async with AsyncTypeSafeClient() as client:
        result = await client.system_one(
            state=email_message,
            questions={
                'from_government': Noul(
                    instructions='Was this email sent from a government agency?'
                ),
                'email_type': Choice(
                    instructions='What type of email is this?', criteria=EMAIL_CRITERIA
                ),
            },
        )
        return {
            'request_id': result.request_id,
            'elapsed_ms': result.raw_http_response.elapsed.total_seconds() * 1000,
            'tokens_in': result.usage.input_tokens,
            'tokens_out': result.usage.output_tokens,
            'from_government': result.nouls['from_government'].noul,
            'email_type': result.choices['email_type'].choice,
            'email_type_probabilities': result.choices['email_type'].probabilities,
            'email_type_confidence': result.choices['email_type'].confidence,
        }


def calculate_request_cost(tokens_in: int) -> float:
    return (tokens_in / 1_000_000) * COST_PER_MTOK_IN


async def classify_with_jev(params: dict, save_every: int = 25) -> list[dict]:
    emails = gmail_api_util.load_json_cache(params.get('anonymized_email_cache_path'))
    logger.info('Got %s anonymized emails from file', len(emails))

    cache_path = params.get('save_to')
    classifications = gmail_api_util.load_json_cache(cache_path)
    if not isinstance(classifications, list):
        classifications = []

    classified_ids = {result['email']['id'] for result in classifications}
    to_classify = [email for email in emails if email.get('id') not in classified_ids]

    classified_since_save = 0
    try:
        for i, email in enumerate(to_classify, start=1):
            classification = await get_email_classifications(email)
            classification['cost'] = calculate_request_cost(classification['tokens_in'])
            result = {
                'email': email,
                'classification': classification,
            }
            classifications.append(result)
            classified_since_save += 1

            if classified_since_save >= save_every:
                gmail_api_util.save_json_cache(cache_path, classifications)
                classified_since_save = 0
                logger.info(f'  ...{i}/{len(to_classify)} classified, cache saved.')
    finally:
        gmail_api_util.save_json_cache(cache_path, classifications)

    return classifications


def get_anonymized_emails(
    params: dict,
    analyzer: AnalyzerEngine,
    anonymizer: AnonymizerEngine,
    save_every: int = 25,
) -> list[dict]:
    emails_raw = gmail_api_util.load_json_cache(params.get('email_cache_path'))
    cache_path = params.get('save_to')
    cache = gmail_api_util.load_json_cache(cache_path)
    if not cache:
        cache = []

    anonymized_ids = {email['id'] for email in cache}
    to_anonymize = [mid for mid in emails_raw if mid not in anonymized_ids]

    anonymized_since_save = 0
    try:
        for i, mid in enumerate(to_anonymize, start=1):
            email = emails_raw[mid]
            email = {key: email[key] for key in KEEP_EMAIL_FIELDS if key in email}
            email['body_text'] = scrub.anonymize_text(
                analyzer, anonymizer, email['body_text']
            )
            cache.append(email)
            anonymized_since_save += 1

            if anonymized_since_save >= save_every:
                gmail_api_util.save_json_cache(cache_path, cache)
                anonymized_since_save = 0
                logger.info(f'  ...{i}/{len(to_anonymize)} anonymized, cache saved.')
    finally:
        gmail_api_util.save_json_cache(cache_path, cache)

    return cache


def get_runtime(duration: timedelta) -> str:
    total_seconds = int(duration.total_seconds())
    hours = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    milisecs = duration.microseconds // 1000
    return f'{hours:02d}h {mins:02d}m {secs:02d}s {milisecs}ms'


async def main():
    started_at = datetime.now()
    analyzer = AnalyzerEngine(supported_languages=['en'])
    anonymizer = AnonymizerEngine()
    params = parameters.get_params()

    match params.get('command'):
        case 'get-emails':
            gmail_api_util.get_emails(
                max_results=params.get('max_results'),
                query=params.get('query'),
                cache_path=params.get('email_cache_path'),
                credentials_path=params.get('credentials_path'),
            )
        case 'anonymize-emails':
            get_anonymized_emails(params, analyzer, anonymizer)
        case 'classify-emails':
            await classify_with_jev(params)
        case 'report':
            generate_report(params)
        case _:
            pass

    duration = datetime.now() - started_at
    run_time = get_runtime(duration)
    logger.info('Finished in %s', run_time)


def entrypoint():
    asyncio.run(main())


if __name__ == '__main__':
    asyncio.run(main())

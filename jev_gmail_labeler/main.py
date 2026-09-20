import asyncio
import logging
from datetime import timedelta

from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul

from jev_gmail_labeler.lib import gmail_api_util

logger = logging.getLogger(__name__)
logging.basicConfig(
    level='INFO',
    format='%(asctime)s [ %(levelname)-8s ] %(message)s',
    datefmt='%Y-%m-%d %I:%M:%S %p',
)


def anonymize_text(
    analyzer: AnalyzerEngine, anonymizer: AnonymizerEngine, input_text: str
) -> str:
    results = analyzer.analyze(text=input_text, language='en')
    redacted_text = anonymizer.redact(text=input_text, analyzer_results=results)
    return redacted_text


async def classify_with_jev(email_message: dict) -> dict:
    async with AsyncTypeSafeClient() as client:
        result = await client.system_one(
            state=email_message,
            questions={
                'email_type': Choice(
                    instructions='What type of email is this?',
                    criteria={
                        'marketing': 'An advertisement, product announcement, or other marketing email',
                        'request_for_feedback': 'An email asking me to leave a review or provide feedback on a product or service',
                        'news': 'A news report or article',
                        'receipt': None,
                        'bill': 'A utility bill, eg., internet, phone, electricity, gas, etc',
                        'personal': 'A personal email between me and another individual, not an email from a company or agency',
                        'order_confirmation': 'A confirmation that I have a placed an order, eg. at an e-commerce store',
                        'order_shipped': None,
                        'order_delivered': None,
                        'transaction_history': None,
                        'forgot_password': None,
                        'magic_link': 'An email containing a magic sign-in link',
                        'totp': 'An email containing a time-based one-time password',
                        'new_login': 'An email indicating that my account logged in from a new device',
                        'note_to_self': 'An email that I sent to myself',
                        'membership_renewal': None,
                        'other': None,
                        'data_breach': 'An email indicating that my data was found in a data leak or breach',
                    },
                ),
                'from_government': Noul(
                    instructions='Was this email send from a government agency?'
                ),
            },
        )
        return {
            'from_government': result.nouls['from_government'].noul,
        }


def get_runtime(duration: timedelta) -> str:
    total_seconds = int(duration.total_seconds())
    hours = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    milisecs = duration.microseconds // 1000
    return f'{hours:02d}h {mins:02d}m {secs:02d}s {milisecs}ms'


async def main():
    analyzer = AnalyzerEngine()
    anonymizer = AnonymizerEngine()

    _ = analyzer
    _ = anonymizer


if __name__ == '__main__':
    asyncio.run(main())

import sys
from dataclasses import replace
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from jev_gmail_labeler.anonymize import Anonymizer
from jev_gmail_labeler.errors import AnonymizationError, ConfigError


@pytest.fixture
def presidio(monkeypatch):
    """Inject fake presidio modules so spaCy is never loaded."""
    fakes = {
        'presidio_analyzer': ModuleType('presidio_analyzer'),
        'presidio_analyzer.nlp_engine': ModuleType('presidio_analyzer.nlp_engine'),
        'presidio_anonymizer': ModuleType('presidio_anonymizer'),
        'presidio_anonymizer.entities': ModuleType('presidio_anonymizer.entities'),
    }
    ns = MagicMock()
    ns.analyzer = MagicMock(name='analyzer')
    ns.analyzer.analyze.return_value = ['finding']
    ns.anonymizer = MagicMock(name='anonymizer')
    ns.anonymizer.anonymize.side_effect = lambda **kw: MagicMock(
        text=kw['text'].replace('Alice', '<PERSON>')
    )
    ns.AnalyzerEngine = MagicMock(return_value=ns.analyzer)
    ns.AnonymizerEngine = MagicMock(return_value=ns.anonymizer)
    ns.provider = MagicMock()
    ns.NlpEngineProvider = MagicMock(return_value=ns.provider)
    ns.OperatorConfig = MagicMock()
    fakes['presidio_analyzer'].AnalyzerEngine = ns.AnalyzerEngine
    fakes['presidio_analyzer.nlp_engine'].NlpEngineProvider = ns.NlpEngineProvider
    fakes['presidio_anonymizer'].AnonymizerEngine = ns.AnonymizerEngine
    fakes['presidio_anonymizer.entities'].OperatorConfig = ns.OperatorConfig
    for name, module in fakes.items():
        monkeypatch.setitem(sys.modules, name, module)
    return ns


def test_init_is_lazy(presidio):
    Anonymizer()
    presidio.NlpEngineProvider.assert_not_called()
    presidio.AnalyzerEngine.assert_not_called()


def test_anonymize_text_uses_replace_operator(presidio):
    a = Anonymizer('en_core_web_sm')
    assert a.anonymize_text('Hi Alice') == 'Hi <PERSON>'
    presidio.NlpEngineProvider.assert_called_once_with(
        nlp_configuration={
            'nlp_engine_name': 'spacy',
            'models': [{'lang_code': 'en', 'model_name': 'en_core_web_sm'}],
        }
    )
    presidio.AnalyzerEngine.assert_called_once_with(
        nlp_engine=presidio.provider.create_engine.return_value,
        supported_languages=['en'],
    )
    presidio.analyzer.analyze.assert_called_once_with(text='Hi Alice', language='en')
    presidio.OperatorConfig.assert_called_once_with('replace')
    kwargs = presidio.anonymizer.anonymize.call_args.kwargs
    assert kwargs['analyzer_results'] == ['finding']
    assert kwargs['operators'] == {'DEFAULT': presidio.OperatorConfig.return_value}


def test_empty_text_skips_engines(presidio):
    assert Anonymizer().anonymize_text('') == ''
    presidio.NlpEngineProvider.assert_not_called()


def test_engines_cached(presidio):
    a = Anonymizer()
    a.anonymize_text('one')
    a.anonymize_text('two')
    presidio.NlpEngineProvider.assert_called_once()
    presidio.AnonymizerEngine.assert_called_once()


def test_missing_model_is_config_error(presidio):
    presidio.provider.create_engine.side_effect = OSError('[E050] not found')
    with pytest.raises(
        ConfigError, match="spaCy model 'en_core_web_md' is not installed"
    ):
        Anonymizer().anonymize_text('x')


def test_other_errors_become_anonymization_error_without_text(presidio):
    presidio.analyzer.analyze.side_effect = RuntimeError('secret body text')
    with pytest.raises(AnonymizationError) as e:
        Anonymizer().anonymize_text('x')
    assert 'secret body text' not in str(e.value)
    assert 'RuntimeError' in str(e.value)
    assert e.value.transient is False


def test_anonymize_email_truncates_before_analysis(presidio, email_message):
    long = replace(email_message, body_text='Alice ' + 'x' * 200)
    result = Anonymizer().anonymize(long, max_body_chars=100)
    analyzed = presidio.analyzer.analyze.call_args.kwargs['text']
    assert len(analyzed) == 100
    assert result.body_truncated is True
    assert result.body.startswith('<PERSON> ')
    assert (result.id, result.sender, result.subject) == (
        'm1',
        'Alice <alice@example.com>',
        'Your receipt',
    )
    assert result.to == 'me@example.com'
    assert result.date == email_message.date


def test_anonymize_email_not_truncated(presidio, email_message):
    result = Anonymizer().anonymize(email_message, max_body_chars=8000)
    assert result.body_truncated is False
    assert result.body == 'Hello <PERSON>, here is your receipt.'


def test_anonymize_email_with_empty_body(presidio, email_message):
    result = Anonymizer().anonymize(
        replace(email_message, body_text=''), max_body_chars=100
    )
    assert result.body == ''
    assert result.body_truncated is False

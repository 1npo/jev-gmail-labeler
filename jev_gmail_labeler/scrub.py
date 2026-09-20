from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig


def anonymize_text(
    analyzer: AnalyzerEngine, anonymizer: AnonymizerEngine, input_text: str
) -> str:
    results = analyzer.analyze(text=input_text, language='en')
    redacted_text = anonymizer.anonymize(
        text=input_text,
        analyzer_results=results,
        operators={'DEFAULT': OperatorConfig('redact', {})},
    )
    return redacted_text.text

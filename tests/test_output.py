import csv
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

from jev_gmail_labeler import output
from jev_gmail_labeler.models import (
    ClassificationResult,
    DecisionReason,
    LabelDecision,
    PipelineResult,
    PipelineStatus,
    SkipReason,
)


def make_result(status=PipelineStatus.LABELLED, *, full=True, **kw) -> PipelineResult:
    base = dict(
        message_id='m1',
        thread_id='t1',
        date='d',
        sender='s',
        subject='sub, with comma',
        status=status,
        skip_reason=None,
        classification=None,
        decision=None,
        applied=False,
        label_id=None,
        error=None,
        retryable=False,
        processed_at='2026-10-03T12:00:00+00:00',
    )
    if full:
        base['classification'] = ClassificationResult(
            'm1',
            'receipt',
            0.9,
            {'receipt': 0.9, '_none': 0.1},
            'jev-1',
            'r1',
            100,
            0.1,
            5.0,
        )
        base['decision'] = LabelDecision(
            'm1', 'receipt', DecisionReason.MATCHED, 'Jev/Receipt'
        )
        base['applied'] = True
        base['label_id'] = 'L1'
    base.update(kw)
    return PipelineResult(**base)


def test_json_array_shape():
    data = json.loads(output.results_to_json([make_result(), make_result(full=False)]))
    assert isinstance(data, list)
    assert data[0]['status'] == 'labelled'
    assert data[0]['classification']['category'] == 'receipt'
    assert data[1]['classification'] is None


def test_json_empty():
    assert json.loads(output.results_to_json([])) == []


def test_csv_header_and_rows():
    skipped = make_result(
        PipelineStatus.SKIPPED, full=False, skip_reason=SkipReason.ALREADY_LABELLED
    )
    text = output.results_to_csv([make_result(), skipped])
    reader = csv.DictReader(io.StringIO(text))
    assert reader.fieldnames == output.CSV_COLUMNS
    rows = list(reader)
    assert rows[0]['subject'] == 'sub, with comma'
    assert rows[0]['label_name'] == 'Jev/Receipt'
    assert rows[0]['decision_reason'] == 'matched'
    assert json.loads(rows[0]['probabilities']) == {'receipt': 0.9, '_none': 0.1}
    assert rows[0]['applied'] == 'True'
    assert rows[1]['skip_reason'] == 'already_labelled'
    assert rows[1]['category'] == ''
    assert rows[1]['probabilities'] == ''
    assert rows[1]['cost_usd'] == ''


def test_write_results_uses_files(monkeypatch):
    write = MagicMock()
    monkeypatch.setattr(output.files, 'write_text_atomic', write)
    results = [make_result()]
    output.write_results(results, Path('/o/x.csv'), 'csv')
    path, text = write.call_args.args
    assert path == Path('/o/x.csv')
    assert text.startswith('processed_at,message_id')
    output.write_results(results, Path('/o/x.json'), 'json')
    assert json.loads(write.call_args.args[1])[0]['message_id'] == 'm1'


def test_summarize_includes_every_status():
    counts = output.summarize([make_result(), make_result(PipelineStatus.ERROR)])
    assert counts[PipelineStatus.LABELLED] == 1
    assert counts[PipelineStatus.ERROR] == 1
    assert counts[PipelineStatus.SKIPPED] == 0
    assert set(counts) == set(PipelineStatus)


def test_summary_line():
    results = [
        make_result(),
        make_result(PipelineStatus.NO_LABEL),
        make_result(PipelineStatus.SKIPPED),
        make_result(PipelineStatus.ERROR),
        make_result(PipelineStatus.WOULD_LABEL),
    ]
    assert output.summary_line(results, None) == (
        'Processed 5 emails: 1 labelled, 1 would label, 1 no label, 1 skipped, 1 errors'
    )
    assert output.summary_line([], Path('/o/x.csv')).endswith('0 errors -> /o/x.csv')

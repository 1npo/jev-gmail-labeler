"""Render pipeline results as JSON or CSV text."""

import csv
import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from jev_gmail_labeler import files
from jev_gmail_labeler.models import PipelineResult, PipelineStatus

CSV_COLUMNS = [
    'processed_at',
    'message_id',
    'thread_id',
    'date',
    'sender',
    'subject',
    'status',
    'skip_reason',
    'category',
    'confidence',
    'decision_reason',
    'label_name',
    'label_id',
    'applied',
    'model',
    'request_id',
    'input_tokens',
    'cost_usd',
    'elapsed_ms',
    'probabilities',
    'error',
]


def results_to_json(results: Sequence[PipelineResult]) -> str:
    """Return a JSON array of results."""
    return json.dumps([r.to_dict() for r in results], indent=2)


def _csv_row(result: PipelineResult) -> dict[str, Any]:
    c = result.classification
    d = result.decision
    return {
        'processed_at': result.processed_at,
        'message_id': result.message_id,
        'thread_id': result.thread_id,
        'date': result.date,
        'sender': result.sender,
        'subject': result.subject,
        'status': result.status.value,
        'skip_reason': result.skip_reason.value if result.skip_reason else None,
        'category': c.category if c else None,
        'confidence': c.confidence if c else None,
        'decision_reason': d.reason.value if d else None,
        'label_name': d.label_name if d else None,
        'label_id': result.label_id,
        'applied': result.applied,
        'model': c.model if c else None,
        'request_id': c.request_id if c else None,
        'input_tokens': c.input_tokens if c else None,
        'cost_usd': c.cost_usd if c else None,
        'elapsed_ms': c.elapsed_ms if c else None,
        'probabilities': json.dumps(c.probabilities) if c else None,
        'error': result.error,
    }


def results_to_csv(results: Sequence[PipelineResult]) -> str:
    """Return results as CSV text with a header row; missing values are empty."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for result in results:
        row = _csv_row(result)
        writer.writerow({k: '' if v is None else v for k, v in row.items()})
    return buffer.getvalue()


def write_results(
    results: Sequence[PipelineResult], path: Path, fmt: Literal['json', 'csv']
) -> None:
    """Write results to ``path`` in the given format."""
    text = results_to_csv(results) if fmt == 'csv' else results_to_json(results)
    files.write_text_atomic(path, text)


def summarize(results: Sequence[PipelineResult]) -> dict[PipelineStatus, int]:
    """Count results by status; every status is present."""
    counts = dict.fromkeys(PipelineStatus, 0)
    for result in results:
        counts[result.status] += 1
    return counts


def summary_line(results: Sequence[PipelineResult], path: Path | None) -> str:
    """Return a one-line summary, e.g. ``Processed 3 emails: 2 labelled, ...``."""
    n = summarize(results)
    line = (
        f'Processed {len(results)} emails: '
        f'{n[PipelineStatus.LABELLED]} labelled, '
        f'{n[PipelineStatus.WOULD_LABEL]} would label, '
        f'{n[PipelineStatus.NO_LABEL]} no label, '
        f'{n[PipelineStatus.SKIPPED]} skipped, '
        f'{n[PipelineStatus.ERROR]} errors'
    )
    return f'{line} -> {path}' if path else line

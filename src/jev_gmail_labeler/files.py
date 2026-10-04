"""All disk I/O helpers. Tests mock this module instead of touching the disk."""

import json
import os
from pathlib import Path
from typing import Any


def read_text(path: Path) -> str:
    """Read a UTF-8 text file; FileNotFoundError propagates."""
    with open(path, encoding='utf-8') as f:
        return f.read()


def read_json(path: Path) -> Any:
    """Read and parse a JSON file; bad JSON raises ValueError naming the path."""
    text = read_text(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f'{path}: invalid JSON: {e}') from e


def ensure_dir(path: Path, mode: int = 0o700) -> None:
    """Create a directory (and parents) if it does not exist."""
    path.mkdir(parents=True, exist_ok=True, mode=mode)


def write_text_atomic(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write text to a temp file next to ``path``, then rename it into place."""
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)

"""Command-line interface (stub; the full CLI arrives in a later session)."""

import argparse
from collections.abc import Sequence

from jev_gmail_labeler import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the requested command."""
    parser = argparse.ArgumentParser(prog='jev-gmail-labeler')
    parser.add_argument('--version', action='version', version=__version__)
    parser.parse_args(argv)
    return 0

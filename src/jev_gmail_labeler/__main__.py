"""Allow ``python -m jev_gmail_labeler``."""

import sys

from jev_gmail_labeler.cli import main

if __name__ == '__main__':
    sys.exit(main())

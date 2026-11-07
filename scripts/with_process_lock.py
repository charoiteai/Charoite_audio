#!/usr/bin/env python3
"""Exec a command under the bounded process-lock contract (see module docs)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
from charoite_paths import harden_umask  # noqa: E402
from process_lock_exec import main  # noqa: E402

if __name__ == '__main__':
    harden_umask()
    sys.exit(main())

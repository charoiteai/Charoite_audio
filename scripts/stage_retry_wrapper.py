#!/usr/bin/env python3
"""Stage a hash-bound private wrapper candidate; never installs it."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
from charoite_paths import harden_umask  # noqa: E402
from retry_wrapper_stage import (  # noqa: E402,F401 - library API used by diagnostics
    EXPECTED, UX_PROBE, digest, main, outside_git, read_source, stage, transform,
)

if __name__ == '__main__':
    harden_umask()
    sys.exit(main())

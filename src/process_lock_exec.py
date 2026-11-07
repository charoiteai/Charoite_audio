#!/usr/bin/env python3
"""Exec argv under a stable POSIX lock; this is not a process-tree sandbox.

The lock file and its trusted containing directory must not be replaced or
unlinked. Protection lasts while at least one process retains the descriptor;
even the immediate command can close it. Descendants using close_fds are not
protected after their last retaining ancestor exits. Do not use this helper as
an installed orphan-work exclusion guarantee without proving those boundaries.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys

from file_locks import BUSY, TAKEN, acquire_outcome


def locked_exec(lock: Path, argv: list[str]) -> int:
    if not argv:
        return 64
    fd = None
    try:
        parent = lock.parent.resolve(strict=True)
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return 73
        path = parent / lock.name
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1):
            return 73
        result = acquire_outcome(fd, attempts=1, pause=0)
        if result != TAKEN:
            return 75 if result == BUSY else 73
        # Detect an already-replaced path. Concurrent replacement remains outside
        # the trusted-directory contract; never unlink a stale-looking lock.
        current = path.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            return 73
        os.set_inheritable(fd, True)
        env = os.environ.copy()
        # Informational only: no branch accepts this value as lock authority.
        env['CHAROITE_PROCESS_LOCK_FD'] = str(fd)
        os.execvpe(argv[0], argv, env)
    except OSError:
        return 126 if fd is not None else 73
    finally:
        if fd is not None:
            os.close(fd)
    return 126  # pragma: no cover - successful exec never returns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    result = locked_exec(args.lock, command)
    if result:
        print(f'process-lock: refused ({result})', file=sys.stderr)
    return result



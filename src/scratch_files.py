"""Temporary audio files cleaned up at normal interpreter exit.

Retention does not cover these directories. Keep cleanup registered until exit;
forced termination and cleanup errors have the same limits as atexit itself.
"""
import atexit
import pathlib
import shutil
import tempfile


def scratch_dir() -> pathlib.Path:
    """Create an independent directory and remove it at normal process exit."""
    directory = pathlib.Path(tempfile.mkdtemp(prefix="charoite-"))
    atexit.register(shutil.rmtree, directory, True)
    return directory

"""Synthetic backup retention probe; run only under the host resource gate.

No model calls or owner data. Reports observations; does not repair backups.
Exit 0: helper preserves both preimages (or FileExistsError preserves A/source B).
Caller non-write behavior is untested; this is not a full-pipeline safety verdict.
Exit 1: retention loss reproduced. Exit 2: invalid setup/control or exception.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
A = "# Synthetic dossier\nFirst revision: alpha.\n".encode()
B = "# Synthetic dossier\nSecond revision: beta.\n".encode()
STAMP = "2030-01-01_010101"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_backup(path, root):
    if path is None or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Returned backup escaped synthetic root")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Backup symlink refused")
    if not path.is_file():
        raise ValueError("Backup is not a file")
    return path


def case(root, backup, wrapper, distinct):
    folder = root / "dossiers"
    folder.mkdir()
    source = folder / "Sample.md"
    expected = [folder / ".backup" / stamp / source.name for stamp in
                (STAMP, "2030-01-01_010102" if distinct else STAMP)]
    for path in [source, *expected, root / "restore-a.md", root / "restore-b.md"]:
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Path escaped synthetic root")
    source.write_bytes(A)
    first = backup(folder, STAMP, source)
    first = checked_backup(expected[0] if wrapper else first, root)
    if first is None or not first.is_file() or first.read_bytes() != A:
        raise ValueError("First backup did not preserve the initial preimage")
    first_hash = sha(first.read_bytes())
    source.write_bytes(B)
    refusal = None
    second = None
    try:
        second = backup(folder, expected[1].parent.name, source)
        second = checked_backup(expected[1] if wrapper else second, root)
        if second is None or not second.is_file():
            raise ValueError("Second backup returned no file")
    except FileExistsError as error:
        refusal = type(error).__name__
    source_unchanged = source.read_bytes() == B
    restored = []
    for label, path, preimage in (("a", first, A), ("b", second, B)):
        if path is None:
            restored.append(None)
        else:
            target = root / f"restore-{label}.md"
            shutil.copyfile(path, target)
            restored.append(target.read_bytes() == preimage)
    all_backups = sorted((folder / ".backup").rglob("*.md"))
    safe = source_unchanged and restored[0] and (restored[1] or refusal is not None)
    return {
        "wrapper_only": wrapper, "distinct_timestamps": distinct,
        "first_path": str(first.relative_to(root)),
        "second_path": str(second.relative_to(root)) if second is not None else None,
        "paths_equal": first == second,
        "preimage_sha256": [sha(A), sha(B)],
        "first_backup_before_second_sha256": first_hash,
        "first_backup_after_second_sha256": sha(first.read_bytes()),
        "second_refusal": refusal, "source_unchanged": source_unchanged,
        "restored_a": restored[0], "restored_b": restored[1],
        "backups": [{"path": str(p.relative_to(root)), "sha256": sha(p.read_bytes())}
                    for p in all_backups],
        "safe_retention": bool(safe),
    }


def main():
    result = {"python": platform.python_version(), "platform": platform.system(),
              "scope": "production backup helper and review wrapper only",
              "full_pipeline_executed": False, "model_calls": 0}
    try:
        with tempfile.TemporaryDirectory(prefix="dossier-backup-probe-") as name:
            root = Path(name).resolve()
            # Set before importing any product module; config/data remain synthetic.
            os.environ["CHAROITE_ROOT"] = str(root)
            sys.path.insert(0, str(ROOT / "src"))
            from charoite_graph import dossier
            spec = importlib.util.spec_from_file_location(
                "backup_probe_review", ROOT / "scripts/nightly_dossier_review.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result["source_hashes"] = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in
                (ROOT / "src/charoite_graph/dossier.py", ROOT / "scripts/nightly_dossier_review.py")}
            cases = result["cases"] = []
            for wrapper, backup in ((False, dossier.backup), (True, module._backup)):
                for distinct in (False, True):
                    case_root = root / f"case-{int(wrapper)}-{int(distinct)}"
                    case_root.mkdir()
                    cases.append(case(case_root, backup, wrapper, distinct))
            result["cases"] = cases
            controls = all(c["restored_a"] and c["restored_b"] and
                           not c["paths_equal"] and c["source_unchanged"] and
                           c["second_refusal"] is None for c in cases if c["distinct_timestamps"])
            if not controls:
                raise ValueError("Distinct-timestamp control failed")
            same_stamp = [c for c in cases if not c["distinct_timestamps"]]
            collision = all(c["paths_equal"] and c["source_unchanged"] and
                            c["restored_a"] is False and c["restored_b"] is True and
                            c["first_backup_after_second_sha256"] == sha(B) and
                            c["second_refusal"] is None for c in same_stamp)
            if all(c["safe_retention"] for c in cases):
                result["status"], code = "helper_preimages_preserved", 0
            elif collision:
                result["status"], code = "collision_reproduced", 1
            else:
                result["status"], code = "unexpected_retention_failure", 2
    except (OSError, ValueError, ImportError, AttributeError, TypeError) as error:
        # Never print arbitrary exception messages containing host paths/config.
        result.update(status="probe_error", error_type=type(error).__name__)
        code = 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

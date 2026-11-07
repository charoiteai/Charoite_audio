"""Hash-bound private transform/syntax proof; never executes the private wrapper.

Use installed quiet_guard && heavy_run. Private wrapper bytes and manifest remain
in a new mode0700 native temporary directory. Only fixed fields/hashes are public.
This does not establish actual UX/child descriptor retention.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'src'))
    import retry_wrapper_stage as stage

    before = stage.read_source(args.source)
    if sha(before) != stage.EXPECTED:
        raise ValueError('sealed_source_hash_mismatch')
    fixture = Path(tempfile.mkdtemp(prefix='n955-private-boundary-')).resolve()
    data = fixture / 'synthetic-data'
    data.mkdir(mode=0o700)
    code = fixture / 'synthetic-code'
    code.mkdir(mode=0o700)
    nightly = code / 'nightly.sh'
    nightly.write_text('#!/bin/bash\nexit 0\n')
    (code / 'night_gate.py').write_text('print("go")\n')
    original_syntax = subprocess.run(['/bin/bash', '-n', str(args.source)],
                                     capture_output=True, timeout=10)
    assert original_syntax.returncode == 0, 'sealed_source_syntax_failed'
    receipt = stage.stage(args.source, fixture / 'candidate', Path(sys.executable),
                          ROOT / 'scripts/with_process_lock.py', nightly,
                          data / 'run.lock', data)
    after = (fixture / 'candidate/wrapper.sh').read_bytes()
    pattern = rb'^ux_step\(\)\s*\{.*?^\}'
    old_ux = re.search(pattern, before, re.M | re.S)
    new_ux = re.search(pattern, after, re.M | re.S)
    assert old_ux is not None and new_ux is not None
    assert old_ux.group() == new_ux.group(), 'private_UX_changed'
    assert stage.read_source(args.source) == before, 'private_source_changed'
    assert after == stage.transform(before), 'candidate_transform_mismatch'
    manifest_bytes = (fixture / 'candidate/manifest.json').read_bytes()
    assert sha(manifest_bytes) == receipt['manifest_sha256']
    assert receipt['installation_ready'] is False
    report = {
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_sha256': sha(before), 'candidate_sha256': sha(after),
        'manifest_sha256': sha(manifest_bytes), 'ux_function_sha256': sha(old_ux.group()),
        'source_unchanged': True, 'ux_function_unchanged': True,
        'source_bash_syntax_rc': original_syntax.returncode,
        'candidate_bash_syntax_rc': 0,
        'candidate_directory_mode': (fixture / 'candidate').stat().st_mode & 0o777,
        'candidate_wrapper_mode': (fixture / 'candidate/wrapper.sh').stat().st_mode & 0o777,
        'runtime_module_sha256': sha((ROOT / 'src/retry_wrapper_stage.py').read_bytes()),
        'private_bytes_or_paths_exported': False, 'private_wrapper_executed': False,
        'actual_UX_child_FD_proven': False, 'installation_ready': False,
        'unclosed_boundary': 'actual background UX/OS child lifetime and independent orphan exclusion',
    }
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'private_stage_basename': fixture.name, 'proof': report}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

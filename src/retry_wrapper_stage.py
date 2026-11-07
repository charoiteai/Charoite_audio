#!/usr/bin/env python3
"""Stage one hash-bound runtime wrapper candidate outside every Git worktree.

Never changes the source, installed launchd configuration, or runtime state.
Private source and argv stay in the private output directory. Stdout contains
only fixed decisions and content hashes, not paths or wrapper text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

EXPECTED = 'c88f5d6f10ca458e17f7d5e0ab94da70899b2ce0e3d3621b67f05e9c823a5a1c'
# Keep record boundaries: command substitution otherwise strips extra newlines.
# night_gate emits canonical integers in its sole "defer battery {pct}%" form.
UX_PROBE = '''N955_UX_DECISION=go
if N955_UX_PROBE=$("$N955_PYTHON" "$N955_GATE_SCRIPT" decide --root "$CHAROITE_ROOT" 2>/dev/null && printf .); then
  N955_UX_PROBE=${N955_UX_PROBE%.}
  N955_UX_PROBE=${N955_UX_PROBE%$'\\n'}
  case "$N955_UX_PROBE" in
    go|brief|skip) N955_UX_DECISION="$N955_UX_PROBE" ;;
    *)
      if [[ "$N955_UX_PROBE" =~ ^defer\\ battery\\ (0|[1-9][0-9]*)%$ ]]; then
        N955_UX_DECISION=defer
      fi ;;
  esac
fi
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(data: bytes, expected: str = EXPECTED) -> bytes:
    if digest(data) != expected:
        raise ValueError('source_hash_mismatch')
    rows = data.decode('utf-8').splitlines(keepends=True)
    if (len(rows) != 216 or not rows[70].startswith('slot_decide() {')
            or not rows[73].lstrip().startswith('if [ -e ')
            or rows[75].strip() != 'return 1' or rows[76].strip() != 'fi'
            or not rows[195].startswith('mkdir -p ')
            or not rows[196].startswith('MARK=')
            or not rows[197].startswith('( set -C; : > ')
            or not rows[198].startswith('find ')
            or rows[208].strip() != 'ux_step'
            or rows[214].strip() != 'exec /bin/bash "$NIGHTLY"'):
        raise ValueError('source_shape_mismatch')
    result = []
    for number, row in enumerate(rows, 1):
        if 74 <= number <= 77 or 197 <= number <= 199:
            continue
        if number == 196:
            result.append(row)
            result.append(UX_PROBE)
            continue
        if number == 209:
            result.append('case "$N955_UX_DECISION" in brief|skip|defer) ;; *) ux_step ;; esac\n')
            continue
        if number == 215:
            result.append('export CHAROITE_SLOT=1\n')
        result.append(row)
    return ''.join(result).encode('utf-8')


def outside_git(directory: Path) -> bool:
    # No Git environment override or failed CLI may turn a worktree into an
    # apparent private location. Reject worktree markers and ordinary bare repos.
    return all(not os.path.lexists(p / '.git')
               and not ((p / 'HEAD').exists() and (p / 'objects').is_dir()
                        and (p / 'refs').is_dir())
               for p in (directory, *directory.parents))


def read_source(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('invalid_source')
        return source.read()


def stage(source: Path, output: Path, python: Path, helper: Path,
          nightly: Path, lock: Path, data_root: Path, *, expected_sha: str = EXPECTED) -> dict:
    parent = output.parent.resolve(strict=True)
    st = parent.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022 or not outside_git(parent):
        raise ValueError('untrusted_output_parent')
    output = parent / output.name
    data = read_source(source)
    candidate = transform(data, expected_sha)
    canonical_root = data_root.expanduser().resolve(strict=True)
    gate = nightly.parent / 'night_gate.py'
    helper_hash = digest(helper.read_bytes())
    lock_implementation_hash = digest((helper.parent.parent / 'src/process_lock_exec.py').read_bytes())
    lock_primitive_hash = digest((helper.parent.parent / 'src/file_locks.py').read_bytes())
    nightly_hash, gate_hash = digest(nightly.read_bytes()), digest(gate.read_bytes())
    # Exclusive directory creation refuses symlinks, existing outputs and races.
    output.mkdir(mode=0o700, exist_ok=False)
    wrapper = output / 'wrapper.sh'
    wrapper.write_bytes(candidate)
    wrapper.chmod(0o600)
    check = subprocess.run(['/bin/bash', '-n', str(wrapper)], capture_output=True)
    if check.returncode:
        raise ValueError('candidate_syntax_failed')
    argv = [str(python), str(helper), '--lock', str(lock), '--', '/usr/bin/env',
            'CHAROITE_ROOT=' + str(canonical_root), 'N955_PYTHON=' + str(python),
            'N955_GATE_SCRIPT=' + str(gate), '/bin/bash', str(wrapper), str(nightly)]
    manifest = {
        'schema': 1, 'source_sha256': digest(data),
        'candidate_sha256': digest(candidate), 'argv': argv,
        'helper_sha256': helper_hash, 'lock_implementation_sha256': lock_implementation_hash,
        'lock_primitive_sha256': lock_primitive_hash,
        'nightly_sha256': nightly_hash, 'gate_sha256': gate_hash,
        'data_root': str(canonical_root),
        'installation_ready': False,
        'blocking_boundary': 'actual_command_ux_child_fd_retention_or_orphan_exclusion_unproved',
        'changes': ['remove_started_marker_check', 'remove_started_marker_creation',
                    'preserve_state_mkdir', 'advisory_ux_probe_with_existing_gate',
                    'enable_existing_final_status_gate', 'outer_process_lock'],
    }
    path = output / 'manifest.json'
    # The receipt binds the actual bytes; readers consume the JSON fields.
    path.write_text(json.dumps(manifest) + '\n')
    path.chmod(0o600)
    assert read_source(source) == data
    assert wrapper.read_bytes() == candidate
    return {'status': 'staged_not_installed', 'source_sha256': digest(data),
            'candidate_sha256': digest(candidate),
            'manifest_sha256': digest(path.read_bytes()), 'installation_ready': False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for key in ('source', 'output', 'python', 'helper', 'nightly', 'lock', 'data-root'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = stage(args.source, args.output, args.python, args.helper, args.nightly,
                       args.lock, args.data_root)
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({'status': 'staging_refused'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0



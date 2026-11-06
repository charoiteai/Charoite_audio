"""Byte-bound staging never installs or leaks a runtime wrapper."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('retry_stage', ROOT / 'scripts/stage_retry_wrapper.py')
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)
FIXTURE_SPEC = importlib.util.spec_from_file_location(
    'synthetic_retry_wrapper', ROOT / 'tests/fixtures/nightly_955/synthetic_wrapper.py')
FIXTURE_MODULE = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(FIXTURE_MODULE)


def fixture():
    return FIXTURE_MODULE.make_wrapper()


class RetryStaging(unittest.TestCase):
    def test_only_exact_selected_lines_change_and_slot_gate_is_enabled(self):
        before = fixture()
        after = STAGE.transform(before, hashlib.sha256(before).hexdigest())
        expected = [r for n, r in enumerate(before.decode().splitlines(keepends=True), 1)
                    if not (74 <= n <= 77 or 197 <= n <= 199)]
        expected.insert(expected.index('mkdir -p "$STATE"\n') + 1, STAGE.UX_PROBE)
        expected[expected.index('ux_step\n')] = (
            'case "$N955_UX_DECISION" in brief|skip|defer*) ;; *) ux_step ;; esac\n')
        expected.insert(expected.index('exec /bin/bash "$NIGHTLY"\n'), 'export CHAROITE_SLOT=1\n')
        self.assertEqual(after, ''.join(expected).encode())
        self.assertNotIn(b': > "$MARK"', after)
        self.assertIn(b'export CHAROITE_SLOT=1\nexec /bin/bash "$NIGHTLY"', after)

    def test_hash_mismatch_refuses_before_transformation(self):
        with self.assertRaisesRegex(ValueError, 'source_hash_mismatch'):
            STAGE.transform(fixture())

    def test_wrong_shape_refuses_even_with_matching_supplied_hash(self):
        data = fixture().replace(b'    return 1\n', b'    return 2\n')
        with self.assertRaisesRegex(ValueError, 'source_shape_mismatch'):
            STAGE.transform(data, hashlib.sha256(data).hexdigest())

    def test_public_git_worktree_is_not_a_private_output_location(self):
        self.assertFalse(STAGE.outside_git(ROOT / 'docs'))
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            self.assertTrue(STAGE.outside_git(parent))
            (parent / '.git').write_text('gitdir: synthetic\n')
            self.assertFalse(STAGE.outside_git(parent))

    def test_stage_creates_private_manifest_and_preserves_source(self):
        with tempfile.TemporaryDirectory(prefix='wrapper-stage-') as tmp:
            parent = Path(tmp).resolve()
            source, nightly, output = parent / 'source.sh', parent / 'nightly.sh', parent / 'candidate'
            data = fixture()
            source.write_bytes(data)
            nightly.write_text('#!/bin/bash\nexit 0\n')
            (parent / 'night_gate.py').write_text('print("go")\n')
            helper = ROOT / 'scripts/with_process_lock.py'
            result = STAGE.stage(source, output, Path(sys.executable), helper, nightly,
                                 parent / 'run.lock', parent, expected_sha=hashlib.sha256(data).hexdigest())
            self.assertEqual(result['status'], 'staged_not_installed')
            self.assertFalse(result['installation_ready'])
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            for name in ('wrapper.sh', 'manifest.json'):
                self.assertEqual((output / name).stat().st_mode & 0o777, 0o600)
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertIn('CHAROITE_ROOT=' + str(parent), manifest['argv'])
            self.assertEqual(manifest['candidate_sha256'], hashlib.sha256((output / 'wrapper.sh').read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                STAGE.stage(source, output, Path(sys.executable), helper, nightly,
                            parent / 'run.lock', parent, expected_sha=hashlib.sha256(data).hexdigest())
            self.assertEqual(source.read_bytes(), data)

    def test_cli_hash_refusal_does_not_create_output_or_print_source(self):
        with tempfile.TemporaryDirectory(prefix='wrapper-refusal-') as tmp:
            parent = Path(tmp)
            source = parent / 'sensitive-name-source.sh'
            source.write_bytes(fixture())
            output = parent / 'candidate'
            command = [sys.executable, str(ROOT / 'scripts/stage_retry_wrapper.py'),
                       '--source', str(source), '--output', str(output), '--python', sys.executable,
                       '--helper', str(ROOT / 'scripts/with_process_lock.py'),
                       '--nightly', str(parent / 'nightly.sh'), '--lock', str(parent / 'run.lock'),
                       '--data-root', str(parent)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {'status': 'staging_refused'})
            self.assertNotIn(source.name, result.stdout + result.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()

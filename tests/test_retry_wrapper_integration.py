"""Same synthetic shell wrapper before/after marker repair, under a real lock."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import retry_wrapper_stage as STAGE  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURE = load('contract_retry_fixture', ROOT / 'tests/fixtures/nightly_955/synthetic_wrapper.py')


class RetryContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='retry-contract-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = self.root / 'completed-work.bin'
        self.artifact.write_bytes(b'already-completed-immutable-fixture')
        self.artifact_hash = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.marker = self.root / 'started-2026-01-02'
        self.marker.touch()
        original = FIXTURE.make_wrapper()
        self.old, self.new = self.root / 'before.sh', self.root / 'after.sh'
        self.old.write_bytes(original)
        self.new.write_bytes(STAGE.transform(original, hashlib.sha256(original).hexdigest()))
        self.nightly = self.root / 'nightly.sh'
        self.nightly.write_text('cd "$CHAROITE_ROOT" || exit 2\n'
                                'exec "$N955_PYTHON" "$FAKE_COMMAND" "$CHAROITE_ROOT" '
                                '"$FAKE_CLOCK" "$FAKE_OUTCOME" nightly\n')
        self.gate = self.root / 'night_gate.py'
        self.gate.write_text('import os,runpy,sys\n'
                             'sys.argv=[os.environ["FAKE_COMMAND"],os.environ["CHAROITE_ROOT"],'
                             'os.environ["FAKE_CLOCK"],os.environ["FAKE_OUTCOME"],"probe"]\n'
                             'runpy.run_path(sys.argv[0],run_name="__main__")\n')

    def run_night(self, clock='2026-01-02T20:00:00', outcome='failed', *, before=False, **extra):
        env = os.environ.copy()
        env.update(CHAROITE_ROOT=str(self.root), N955_PYTHON=sys.executable,
                   N955_GATE_SCRIPT=str(self.gate), FAKE_CLOCK=clock, FAKE_OUTCOME=outcome,
                   FAKE_COMMAND=str(ROOT / 'tests/fixtures/nightly_955/fake_nightly.py'),
                   FAKE_WINDOW='yes', FAKE_RESOURCE='yes', FAKE_OTHER_RUNNING='no',
                   FAKE_DAY='2026-01-02', FAKE_PROBE_FAILURE='no')
        env.pop('FAKE_PROBE_DECISION', None)
        env.update(extra)
        command = [sys.executable, str(ROOT / 'scripts/with_process_lock.py'),
                   '--lock', str(self.root / 'run.lock'), '--', '/bin/bash',
                   str(self.old if before else self.new), str(self.nightly), str(self.root)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.artifact_hash)
        self.assertTrue(self.marker.exists())
        return result, json.loads(result.stdout) if result.stdout.strip() else {}

    def status(self, state, finished='2026-01-02 19:30:00'):
        (self.root / 'status.json').write_text(json.dumps({'state': state, 'rc': 0, 'finished': finished}))

    def ux_count(self):
        path = self.root / 'steps'
        return path.read_text().splitlines().count('ux') if path.exists() else 0

    def test_started_marker_blocks_before_and_failed_rc0_retries_after(self):
        self.status('failed')
        result, receipt = self.run_night(before=True)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(receipt, {})
        self.assertFalse((self.root / 'counts.json').exists())
        self.assertEqual(self.ux_count(), 0)
        for expected in (1, 2):
            result, receipt = self.run_night()
            self.assertEqual(result.returncode, 0)
            self.assertEqual(receipt['decision'], 'go')
            self.assertEqual(receipt['counts']['heavy'], expected)
            self.assertEqual(self.ux_count(), expected)
        self.assertEqual(json.loads((self.root / 'status.json').read_text())['state'], 'failed')

    def test_interrupted_and_missing_final_status_retry(self):
        self.status('interrupted')
        result, receipt = self.run_night(outcome='running')
        self.assertEqual(result.returncode, 9)
        self.assertEqual(receipt['counts']['heavy'], 1)
        result, receipt = self.run_night(outcome='ok')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(receipt['counts']['heavy'], 2)

    def test_success_skips_ux_and_heavy_but_keeps_final_gate_and_brief(self):
        self.status('ok')
        result, receipt = self.run_night()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(receipt, {'decision': 'skip', 'counts': {'heavy': 0, 'brief': 0}})
        self.assertEqual(self.ux_count(), 0)
        self.assertEqual(json.loads((self.root / 'nightly_gate.json').read_text())['decision'], 'skip')
        result, receipt = self.run_night('2026-01-03T08:00:00')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(receipt, {'decision': 'brief', 'counts': {'heavy': 0, 'brief': 1}})
        self.assertEqual(self.ux_count(), 0)
        self.assertEqual(json.loads((self.root / 'nightly_gate.json').read_text())['decision'], 'brief')

    def test_new_night_retries_after_previous_success(self):
        self.status('ok', '2026-01-01 20:00:00')
        result, receipt = self.run_night(outcome='ok')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(receipt['decision'], 'go')
        self.assertEqual(receipt['counts']['heavy'], 1)
        self.assertEqual(self.ux_count(), 1)

    def test_resource_window_and_other_owner_refusals_remain(self):
        for key in ('FAKE_RESOURCE', 'FAKE_WINDOW', 'FAKE_OTHER_RUNNING'):
            with self.subTest(key=key):
                value = 'yes' if key == 'FAKE_OTHER_RUNNING' else 'no'
                result, receipt = self.run_night(**{key: value})
                self.assertEqual(result.returncode, 75)
                self.assertEqual(receipt, {})
                self.assertEqual(self.ux_count(), 0)
                self.assertFalse((self.root / 'counts.json').exists())

    def test_probe_failure_is_advisory_and_final_gate_still_decides(self):
        self.status('ok')
        result, receipt = self.run_night(FAKE_PROBE_FAILURE='yes')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.ux_count(), 1)  # Explicit fail-open matches the source fallback.
        self.assertEqual(receipt['decision'], 'skip')
        self.assertEqual(receipt['counts']['heavy'], 0)

    def test_changed_probe_decision_cannot_bypass_resources_or_final_gate(self):
        self.status('failed')
        result, receipt = self.run_night(FAKE_PROBE_DECISION='skip')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.ux_count(), 0)
        self.assertEqual(receipt['decision'], 'go')
        self.assertEqual(receipt['counts']['heavy'], 1)


if __name__ == '__main__':
    unittest.main()

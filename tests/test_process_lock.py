"""Synthetic POSIX exec-lock contract, including its unprotected boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/with_process_lock.py'
sys.path.insert(0, str(ROOT / 'src'))
import process_lock_exec  # noqa: E402


class ProcessLock(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='exec-lock-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = self.root / 'run.lock'
        self.nonce = 'lock-fixture-' + uuid.uuid4().hex

    def command(self, code='pass', *args):
        return [sys.executable, str(SCRIPT), '--lock', str(self.lock), '--',
                sys.executable, '-c', code, self.nonce, *map(str, args)]

    def run_command(self, code='pass', *args):
        return subprocess.run(self.command(code, *args), capture_output=True, text=True, timeout=10)

    def wait_file(self, path):
        until = time.monotonic() + 5
        while not path.exists() and time.monotonic() < until:
            time.sleep(0.01)
        self.assertTrue(path.exists(), 'synthetic process did not reach its observable boundary')

    def kill_own(self, process):
        if process.poll() is not None:
            return
        # These are test-owned isolated processes; never signal an unverified PID.
        command = subprocess.run(['ps', '-ww', '-p', str(process.pid), '-o', 'command='],
                                 capture_output=True, text=True, check=True).stdout
        self.assertIn(self.nonce, command)
        process.kill()
        process.wait(timeout=5)

    def owner(self, code, *args):
        process = subprocess.Popen(self.command(code, *args), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self.kill_own, process)
        return process

    def cleanup_descendant(self, ready, release):
        release.touch()
        if not ready.exists():
            return
        pid = int(ready.read_text())
        until = time.monotonic() + 1
        while True:
            command = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'command='],
                                     capture_output=True, text=True).stdout
            if self.nonce not in command:
                return  # Exited, reaped, or no longer our process: never signal.
            if time.monotonic() >= until:
                os.kill(pid, signal.SIGKILL)  # Full command identity was read above.
                return
            time.sleep(0.02)

    def test_argv_is_not_a_shell_and_lock_contents_survive(self):
        self.lock.write_bytes(b'keep-existing-bytes')
        output = self.root / 'argv.json'
        r = self.run_command('import json,pathlib,sys;pathlib.Path(sys.argv[2]).write_text(json.dumps(sys.argv[3:]))',
                             output, 'a b', '$(never-run)', '*')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(output.read_text()), ['a b', '$(never-run)', '*'])
        self.assertEqual(self.lock.read_bytes(), b'keep-existing-bytes')

    def test_empty_argv_is_refused_without_creating_a_lock(self):
        self.assertEqual(process_lock_exec.locked_exec(self.lock, []), 64)
        self.assertFalse(self.lock.exists())

    def test_concurrent_owner_blocks_duplicate_and_then_releases(self):
        ready, release = self.root / 'ready', self.root / 'release'
        owner = self.owner('import pathlib,sys,time; p=pathlib.Path(sys.argv[2]);p.write_text("ready");'
                           'r=pathlib.Path(sys.argv[3]); end=time.monotonic()+8;'
                           '\nwhile not r.exists() and time.monotonic()<end: time.sleep(.01)', ready, release)
        self.wait_file(ready)
        output = self.root / 'duplicate'
        duplicate = self.run_command('import pathlib,sys;pathlib.Path(sys.argv[2]).touch()', output)
        self.assertEqual(duplicate.returncode, 75)
        self.assertFalse(output.exists())
        release.touch()
        self.assertEqual(owner.wait(timeout=5), 0)
        self.assertEqual(self.run_command().returncode, 0)

    def test_exec_failure_is_nonzero_and_releases_lock(self):
        r = subprocess.run([sys.executable, str(SCRIPT), '--lock', str(self.lock), '--',
                            str(self.root / 'absent-executable')], capture_output=True, timeout=5)
        self.assertEqual(r.returncode, 126)
        self.assertEqual(self.run_command().returncode, 0)

    def test_entry_hardens_the_child_output_mask(self):
        output = self.root / 'private-child-output'
        argv = self.command('import pathlib,sys;pathlib.Path(sys.argv[2]).write_text("private fixture")', output)
        launcher = 'import os,sys;os.umask(0o022);os.execv(sys.executable,sys.argv[1:])'
        result = subprocess.run([sys.executable, '-c', launcher, *argv],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_symlink_lock_is_rejected_without_touching_target(self):
        target = self.root / 'target'
        target.write_bytes(b'untouched')
        self.lock.symlink_to(target)
        self.assertNotEqual(self.run_command().returncode, 0)
        self.assertEqual(target.read_bytes(), b'untouched')

    def test_hardlink_and_fifo_are_rejected(self):
        target = self.root / 'target'
        target.touch()
        os.link(target, self.lock)
        self.assertEqual(self.run_command().returncode, 73)
        self.lock.unlink()  # Only this test-owned unheld fixture link.
        os.mkfifo(self.lock)
        self.assertEqual(self.run_command().returncode, 73)

    def test_untrusted_directory_is_rejected(self):
        self.root.chmod(0o770)
        self.assertEqual(self.run_command().returncode, 73)

    def test_command_can_close_descriptor_boundary_is_not_hidden(self):
        ready, release = self.root / 'closed', self.root / 'release'
        owner = self.owner('import os,pathlib,sys,time;os.close(int(os.environ["CHAROITE_PROCESS_LOCK_FD"]));'
                           'pathlib.Path(sys.argv[2]).touch();r=pathlib.Path(sys.argv[3]);end=time.monotonic()+8;'
                           '\nwhile not r.exists() and time.monotonic()<end: time.sleep(.01)', ready, release)
        self.wait_file(ready)
        self.assertIsNone(owner.poll())
        self.assertEqual(self.run_command().returncode, 0)  # Deliberately unprotected contract.
        release.touch()
        self.assertEqual(owner.wait(timeout=5), 0)

    def test_descendant_retention_and_close_fds_counterexample(self):
        child_code = ('import os,pathlib,sys,time;pathlib.Path(sys.argv[2]).write_text(str(os.getpid()));'
                      'r=pathlib.Path(sys.argv[3]);end=time.monotonic()+8;'
                      '\nwhile not r.exists() and time.monotonic()<end:time.sleep(.01)')
        for retain in (True, False):
            with self.subTest(retain=retain):
                ready = self.root / f'child-{retain}'
                release = self.root / f'release-{retain}'
                owner_code = ('import os,subprocess,sys,time;fd=int(os.environ["CHAROITE_PROCESS_LOCK_FD"]);'
                              'subprocess.Popen([sys.executable,"-c",sys.argv[2],sys.argv[1],sys.argv[3],sys.argv[4]],'
                              'pass_fds=(fd,) if sys.argv[5]=="yes" else (),start_new_session=True);time.sleep(8)')
                owner = self.owner(owner_code, child_code, ready, release, 'yes' if retain else 'no')
                self.addCleanup(self.cleanup_descendant, ready, release)
                self.wait_file(ready)
                self.kill_own(owner)
                self.assertEqual(self.run_command().returncode, 75 if retain else 0)
                release.touch()
                until = time.monotonic() + 3
                while self.run_command().returncode == 75 and time.monotonic() < until:
                    time.sleep(0.02)
                self.assertEqual(self.run_command().returncode, 0)


if __name__ == '__main__':
    unittest.main()

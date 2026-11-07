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
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import retry_wrapper_stage as STAGE  # noqa: E402
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
            'case "$N955_UX_DECISION" in brief|skip|defer) ;; *) ux_step ;; esac\n')
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
            manifest_bytes = (output / 'manifest.json').read_bytes()
            self.assertEqual(result['manifest_sha256'], hashlib.sha256(manifest_bytes).hexdigest())
            manifest = json.loads(manifest_bytes)
            self.assertEqual(manifest['schema'], 1)
            self.assertIs(manifest['installation_ready'], False)
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

    def test_stage_refuses_git_output_before_writing_private_bytes(self):
        with tempfile.TemporaryDirectory(prefix='stage-input-') as tmp:
            parent = Path(tmp).resolve()
            source, nightly = parent / 'source.sh', parent / 'nightly.sh'
            data = fixture()
            source.write_bytes(data)
            nightly.write_text('#!/bin/bash\nexit 0\n')
            (parent / 'night_gate.py').write_text('print("go")\n')
            with tempfile.TemporaryDirectory(prefix='stage-denied-', dir=ROOT / 'docs') as within:
                output = Path(within) / 'candidate'
                with self.assertRaisesRegex(ValueError, 'untrusted_output_parent'):
                    STAGE.stage(source, output, Path(sys.executable),
                                 ROOT / 'scripts/with_process_lock.py', nightly,
                                 parent / 'run.lock', parent,
                                 expected_sha=hashlib.sha256(data).hexdigest())
                self.assertFalse(output.exists())


def stage_inputs(parent):
    """Complete synthetic inputs so a refused guard cannot hide behind bad data."""
    parent = parent.resolve()
    source, nightly = parent / 'source.sh', parent / 'nightly.sh'
    data = fixture()
    source.write_bytes(data)
    nightly.write_text('#!/bin/bash\nexit 0\n')
    (parent / 'night_gate.py').write_text('print("go")\n')
    return {
        'source': source, 'output': parent / 'candidate',
        'python': Path(sys.executable), 'helper': ROOT / 'scripts/with_process_lock.py',
        'nightly': nightly, 'lock': parent / 'run.lock', 'data_root': parent,
        'expected_sha': hashlib.sha256(data).hexdigest(),
    }


@pytest.mark.parametrize(('head', 'objects', 'refs', 'allowed'), [
    (False, False, False, True), (False, False, True, True),
    (False, True, False, True), (False, True, True, True),
    (True, False, False, True), (True, False, True, True),
    (True, True, False, True), (True, True, True, False),
])
def test_bare_repository_and_partial_layouts_in_ancestors(tmp_path, head, objects, refs, allowed):
    bare = tmp_path / 'synthetic-repository'
    child = bare / 'nested'
    child.mkdir(parents=True)
    if head:
        (bare / 'HEAD').write_text('ref: refs/heads/synthetic\n')
    if objects:
        (bare / 'objects').mkdir()
    if refs:
        (bare / 'refs').mkdir()
    for directory in (bare, child):
        try:
            result = STAGE.outside_git(directory)
        except TypeError as error:
            pytest.fail(f'outside_git must classify a valid synthetic filesystem layout: {error}')
        assert result is allowed


@pytest.mark.parametrize('mode', [0o720, 0o702])
def test_writable_output_parent_refuses_before_source_read(tmp_path, mode):
    inputs = stage_inputs(tmp_path)
    untrusted = tmp_path / 'untrusted'
    untrusted.mkdir(mode=0o700)
    untrusted.chmod(mode)
    inputs['output'] = untrusted / 'candidate'
    with patch.object(STAGE, 'read_source', wraps=STAGE.read_source) as read:
        with pytest.raises(ValueError, match='^untrusted_output_parent$'):
            STAGE.stage(**inputs)
        read.assert_not_called()
    assert not inputs['output'].exists()
    assert inputs['source'].read_bytes() == fixture()


@pytest.mark.parametrize('dangling', [False, True])
def test_missing_data_root_refuses_before_output_creation(tmp_path, dangling):
    inputs = stage_inputs(tmp_path)
    missing = tmp_path / 'absent-data-root'
    if dangling:
        link = tmp_path / 'data-root-link'
        link.symlink_to(missing, target_is_directory=True)
        inputs['data_root'] = link
    else:
        inputs['data_root'] = missing
    with pytest.raises(FileNotFoundError):
        STAGE.stage(**inputs)
    assert not inputs['output'].exists()
    assert not missing.exists()
    assert inputs['source'].read_bytes() == fixture()


def test_missing_output_parent_component_before_dotdot_refuses_before_source_read(tmp_path):
    inputs = stage_inputs(tmp_path)
    missing = tmp_path / 'absent-output-parent'
    inputs['output'] = missing / '..' / 'candidate'
    with patch.object(STAGE, 'read_source', wraps=STAGE.read_source) as read:
        with pytest.raises(FileNotFoundError):
            STAGE.stage(**inputs)
        read.assert_not_called()
    # Also check the location non-strict resolution would have selected.
    assert not (tmp_path / 'candidate').exists()
    assert not missing.exists()
    assert inputs['source'].read_bytes() == fixture()


def test_syntax_refusal_captures_shell_diagnostics_without_exposing_source(tmp_path, capfd):
    inputs = stage_inputs(tmp_path)
    # Preserve line count and every checked transform seam; break shell syntax
    # on an unrelated synthetic line so the real bash -n emits diagnostics.
    invalid = fixture().replace(b'set -u\n', b'set -u ) # synthetic parser error\n', 1)
    inputs['source'].write_bytes(invalid)
    inputs['expected_sha'] = hashlib.sha256(invalid).hexdigest()
    with pytest.raises(ValueError, match='^candidate_syntax_failed$'):
        STAGE.stage(**inputs)
    captured = capfd.readouterr()
    assert captured.out == ''
    assert captured.err == ''
    assert not (inputs['output'] / 'manifest.json').exists()
    assert inputs['source'].read_bytes() == invalid


def test_manifest_consumer_verifies_actual_bytes_and_executes_literal_argv(tmp_path):
    parent = tmp_path / 'private fixture with spaces'
    parent.mkdir(mode=0o700)
    inputs = stage_inputs(parent)
    # This public wrapper supplies state from the explicit manifest environment,
    # as a private wrapper would from its configuration. No private source used.
    source = fixture().replace(b'STATE="$2"\n', b'STATE="$CHAROITE_ROOT"\n')
    inputs['source'].write_bytes(source)
    inputs['expected_sha'] = hashlib.sha256(source).hexdigest()
    inputs['nightly'].write_text(
        'exec "$N955_PYTHON" -c \'import json,os,pathlib; '
        'root=pathlib.Path(os.environ["CHAROITE_ROOT"]); '
        'fd=int(os.environ["CHAROITE_PROCESS_LOCK_FD"]); '
        'held=os.fstat(fd); actual=(root/"run.lock").stat(); '
        '(root/"command.json").write_text(json.dumps({'
        '"slot":os.environ["CHAROITE_SLOT"],'
        '"same_inode":(held.st_dev,held.st_ino)==(actual.st_dev,actual.st_ino)}))\'\n')
    receipt = STAGE.stage(**inputs)
    raw = (inputs['output'] / 'manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == receipt['manifest_sha256']
    manifest = json.loads(raw)
    assert manifest['schema'] == 1
    assert manifest['installation_ready'] is False
    for field, path in (
        ('source_sha256', inputs['source']),
        ('candidate_sha256', inputs['output'] / 'wrapper.sh'),
        ('helper_sha256', inputs['helper']),
        ('nightly_sha256', inputs['nightly']),
        ('gate_sha256', parent / 'night_gate.py'),
    ):
        assert manifest[field] == hashlib.sha256(path.read_bytes()).hexdigest()
    result = subprocess.run(manifest['argv'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads((parent / 'command.json').read_bytes()) == {'slot': '1', 'same_inode': True}
    assert (parent / 'steps').read_text().splitlines() == ['resource', 'ux']
    assert inputs['source'].read_bytes() == source


if __name__ == '__main__':
    unittest.main()

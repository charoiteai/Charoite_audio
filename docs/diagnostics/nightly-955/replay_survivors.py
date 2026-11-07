"""Replay the exact original 18 survivors in an isolated tracked-source clone.

Run only under the project's quiet_guard && heavy_run. No live product roots,
models or private wrapper bytes are used. This is an addressed experiment, not
the full mandatory mutation verdict or installed-chain acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
SOURCE = 'fdbf9166a7224e5fa36772fcf32fd1dfd51e45a4'
TESTS = ('tests/test_process_lock.py', 'tests/test_stage_retry_wrapper.py')
IDENTITIES = [
    ('process_lock_exec', 26, 'True → False @44-48', 'test_missing_parent_component_before_dotdot'),
    ('process_lock_exec', 38, '73 → 0 @45-47', 'test_no_flock_refuses'),
    ('process_lock_exec', 41, 'False → True @44-49', 'test_replacement_symlink'),
    ('process_lock_exec', 43, '73 → 0 @19-21', 'test_replaced_inode'),
    ('process_lock_exec', 43, 'return X → return None @12-21', 'test_replaced_inode'),
    ('process_lock_exec', 54, '126 → 0 @11-14', 'test_returning_exec_seam'),
    ('process_lock_exec', 54, 'return X → return None @4-14', 'test_returning_exec_seam'),
    ('retry_wrapper_stage', 67, 'And → Or @24-49', 'test_bare_repository'),
    ('retry_wrapper_stage', 67, 'Div → Mult @51-64', 'test_bare_repository'),
    ('retry_wrapper_stage', 68, 'Div → Mult @29-39', 'test_bare_repository'),
    ('retry_wrapper_stage', 82, 'True → False @42-46', 'test_missing_output_parent_component'),
    ('retry_wrapper_stage', 84, '18 → 0 @48-53', 'test_writable_output_parent'),
    ('retry_wrapper_stage', 89, 'True → False @59-63', 'test_missing_data_root'),
    ('retry_wrapper_stage', 100, 'True → False @77-81', 'test_syntax_refusal_captures'),
    ('retry_wrapper_stage', 107, '1 → 0 @18-19', 'test_stage_creates_private_manifest'),
    ('retry_wrapper_stage', 113, 'False → True @30-35', 'test_stage_creates_private_manifest'),
    ('retry_wrapper_stage', 120, 'True → False @51-55', ''),
    ('retry_wrapper_stage', 120, '2 → 0 @64-65', ''),
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--mode', choices=('original', 'candidate', 'both'), default='both')
    parser.add_argument('--identities', default=','.join(map(str, range(1, 19))))
    parser.add_argument('--consumer-only', action='store_true')
    args = parser.parse_args()
    selected = {int(value) for value in args.identities.split(',')}
    if not selected or not selected <= set(range(1, 19)):
        parser.error('identities must be a nonempty subset of 1..18')
    if args.consumer_only and (args.mode != 'candidate' or not selected <= {17, 18}):
        parser.error('consumer-only requires candidate mode and identities 17/18')
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    sys.path.insert(0, str(ROOT / 'scripts'))
    import mutate_check as mutator
    candidate_tests = {p: (ROOT / p).read_bytes() for p in TESTS}

    report = {
        'original_source': SOURCE,
        'candidate_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'python': sys.version.split()[0], 'platform': sys.platform,
        'test_sha256': {p: sha(data) for p, data in candidate_tests.items()},
        'scope': '18 exact original critical identities; original source with original/candidate tests',
        'selected_identities': sorted(selected), 'consumer_only': args.consumer_only,
        'full_ci': False, 'installed_chain': False, 'baselines': [], 'mutants': [],
    }

    def save():
        (output / 'result.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')

    with tempfile.TemporaryDirectory(prefix='n955-replay-') as temp:
        work = mutator.copy_tree(ROOT, SOURCE, Path(temp))
        originals = {p: (work / p).read_bytes() for p in TESTS}
        sources = {name: (work / f'src/{name}.py').read_text() for name in
                   ('process_lock_exec', 'retry_wrapper_stage')}

        def run(label, test):
            xml = output / f'{label}.xml'
            env = {k: v for k, v in os.environ.items() if k != 'CHAROITE_ROOT'}
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            started = time.monotonic()
            command = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                       '-p', 'no:xdist', test, '--junitxml=' + str(xml)]
            result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=120)
            (output / f'{label}.log').write_text(result.stdout + result.stderr)
            tree = ET.parse(xml)
            failures = [case.attrib['name'] for case in tree.findall('.//testcase')
                        if case.find('failure') is not None]
            errors = tree.findall('.//error')
            skipped = tree.findall('.//skipped')
            return {'rc': result.returncode, 'seconds': round(time.monotonic()-started, 3),
                    'failures': failures, 'errors': len(errors), 'skipped': len(skipped),
                    'tests': len(tree.findall('.//testcase'))}

        modes = ('original', 'candidate') if args.mode == 'both' else (args.mode,)
        for mode in modes:
            for p in TESTS:
                (work / p).write_bytes(originals[p] if mode == 'original' else candidate_tests[p])
            selected_tests = {TESTS[0 if name == 'process_lock_exec' else 1]
                              for number, (name, *_rest) in enumerate(IDENTITIES, 1)
                              if number in selected}
            if args.consumer_only:
                selected_tests = {TESTS[1] + '::test_manifest_consumer_verifies_actual_bytes_and_executes_literal_argv'}
            for test in sorted(selected_tests):
                row = {'mode': mode, 'test': test, **run(f'{mode}-base-{Path(test).stem}', test)}
                report['baselines'].append(row)
                save()
                if row['rc'] != 0 or row['errors'] or row['skipped']:
                    raise RuntimeError('baseline did not pass completely')
            for number, (name, line, what, expected) in enumerate(IDENTITIES, 1):
                if number not in selected:
                    continue
                path = work / f'src/{name}.py'
                source = sources[name]
                matches = [m for m in mutator.scan(path, {line}, source, f'src/{name}.py').mutations
                           if m.what == what]
                if len(matches) != 1:
                    raise RuntimeError(f'identity {number}: expected exactly one original mutation')
                mutation = matches[0]
                changed, why = mutator.applied(mutation, source)
                if changed is None:
                    raise RuntimeError(f'identity {number}: mutation refused: {why}')
                test = TESTS[0 if name == 'process_lock_exec' else 1]
                if args.consumer_only:
                    test += '::test_manifest_consumer_verifies_actual_bytes_and_executes_literal_argv'
                path.write_text(changed)
                try:
                    result = run(f'{mode}-{number:02}', test)
                finally:
                    path.write_text(source)
                observed = 'survived' if result['rc'] == 0 else 'unjudged'
                if (mode == 'candidate' and expected and result['rc'] == 1
                        and not result['errors'] and not result['skipped']
                        and any(expected in f for f in result['failures'])):
                    observed = 'killed_by_expected_assertion'
                row = {'mode': mode, 'number': number, 'path': f'src/{name}.py', 'line': line,
                       'mutation': what, 'key': mutation.key, 'mutated_sha256': sha(changed.encode()),
                       'observed': observed, **result}
                report['mutants'].append(row)
                save()
                print(f'{mode} {number:02}: {observed}', flush=True)
        # Source files must be restored before the clone is removed.
        for name, source in sources.items():
            assert (work / f'src/{name}.py').read_text() == source
    expected = [row for row in report['mutants'] if row['mode'] == 'candidate' and row['number'] <= 16]
    report['addressed_controls_passed'] = all(row['observed'] == 'killed_by_expected_assertion' for row in expected)
    save()
    return 0 if report['addressed_controls_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

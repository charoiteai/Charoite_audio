"""Addressed behavioral shell-string mutations; use installed quiet/heavy gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
MODULE = 'src/retry_wrapper_stage.py'
SUITE = 'tests/test_retry_wrapper_integration.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--head', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    output = args.output.resolve()
    sys.path.insert(0, str(ROOT / 'scripts'))
    import mutate_check

    report = {'head': args.head, 'suite': SUITE, 'module': MODULE, 'mutants': []}
    with tempfile.TemporaryDirectory(prefix='n955-probe-mutants-') as temp:
        work = mutate_check.copy_tree(ROOT, args.head, Path(temp))
        target = work / MODULE
        source = target.read_text()
        report['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
        report['tests_sha256'] = hashlib.sha256((work / SUITE).read_bytes()).hexdigest()

        def run(label):
            env = {k: v for k, v in os.environ.items() if k != 'CHAROITE_ROOT'}
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            with tempfile.TemporaryDirectory(prefix='n955-probe-case-') as base:
                result = subprocess.run(
                    [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                     '-p', 'no:xdist', SUITE, '--basetemp=' + base,
                     '--junitxml=' + str(output / (label + '.xml'))],
                    cwd=work, env=env, capture_output=True, text=True, timeout=120)
            (output / (label + '.log')).write_text(result.stdout + result.stderr)
            tree = ET.parse(output / (label + '.xml'))
            return {'rc': result.returncode,
                    'errors': len(tree.findall('.//error')), 'skipped': len(tree.findall('.//skipped')),
                    'failures': [case.attrib['name'] for case in tree.findall('.//testcase')
                                 if case.find('failure') is not None]}

        report['baseline'] = run('baseline')
        if report['baseline']['rc'] or report['baseline']['errors'] or report['baseline']['skipped']:
            raise RuntimeError('baseline not clean; no mutants judged')
        lines = source.splitlines(keepends=True)
        regex = next(line for line in lines if 'if [[ "$N955_UX_PROBE" =~' in line)
        capture = next(line for line in lines if line.startswith('if N955_UX_PROBE=$('))
        unframed = ''.join(line for line in lines if not line.startswith('  N955_UX_PROBE=${'))
        mutations = (
            ('accept_defer_prefix', source.replace(regex, '      if [[ "$N955_UX_PROBE" == defer* ]]; then\n', 1),
             'test_invalid_successful_probe_keeps_ux_and_final_gate_authority'),
            ('strip_extra_record_newlines', unframed.replace(capture, capture.replace(' && printf .', ''), 1),
             'test_invalid_successful_probe_keeps_ux_and_final_gate_authority'),
            ('mask_failed_probe_status', source.replace(capture, capture.replace(' && printf .', '; printf .'), 1),
             'test_probe_failure_is_advisory_and_final_gate_still_decides'),
        )
        for label, changed, expected in mutations:
            assert changed != source
            target.write_text(changed)
            try:
                result = run(label)
            finally:
                target.write_text(source)
            killed = (result['rc'] == 1 and not result['errors'] and not result['skipped']
                      and any(expected in case for case in result['failures']))
            report['mutants'].append({'label': label,
                                      'mutated_sha256': hashlib.sha256(changed.encode()).hexdigest(),
                                      'killed_by_expected_assertion': killed, **result})
            print(label, 'expected assertion killed' if killed else 'NOT ACCEPTED', flush=True)
        report['all_expected_assertions_killed'] = all(
            row['killed_by_expected_assertion'] for row in report['mutants'])
        (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['all_expected_assertions_killed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

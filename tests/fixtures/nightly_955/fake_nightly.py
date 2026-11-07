#!/usr/bin/env python3
"""Synthetic status/artifact command for exec-lock tests; never a real night."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import runpy
import sys


def main() -> int:
    root, clock, outcome = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    mode = sys.argv[4] if len(sys.argv) > 4 else 'nightly'
    repo = Path(__file__).resolve().parents[3]
    gate = runpy.run_path(str(repo / 'scripts/night_gate.py'))
    path = root / 'status.json'
    state = json.loads(path.read_text()) if path.exists() else None
    counts_path = root / 'counts.json'
    counts = json.loads(counts_path.read_text()) if counts_path.exists() else {'heavy': 0, 'brief': 0}
    decision = gate['decide'](dt.datetime.fromisoformat(clock), state, (False, 100))
    if mode == 'probe':
        print(os.environ.get('FAKE_PROBE_DECISION', decision))
        return 2 if os.environ.get('FAKE_PROBE_FAILURE') == 'yes' else 0
    if os.environ.get('CHAROITE_SLOT') != '1':
        decision = 'go'
    else:
        (root / 'nightly_gate.json').write_text(json.dumps({'decision': decision, 'rc': 0}))
    if decision == 'brief':
        counts['brief'] += 1
    elif decision == 'go':
        counts['heavy'] += 1
        state = {'state': outcome, 'rc': 0, 'finished': clock.replace('T', ' ')}
        path.write_text(json.dumps(state))
    counts_path.write_text(json.dumps(counts))
    print(json.dumps({'decision': decision, 'counts': counts}, sort_keys=True))
    return 9 if outcome == 'running' else 0


if __name__ == '__main__':
    sys.exit(main())

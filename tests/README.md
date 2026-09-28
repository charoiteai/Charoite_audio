# Tests

*[**English**] · [Русский](../docs/ru/tests/README.md) · [中文](../docs/zh/tests/README.md)*

```bash
.venv/bin/python -m pytest tests/ -x -q
```

With pytest-xdist installed (CI pins it as `PYTEST_XDIST_VERSION` in `ci.yml`)
the full set runs in several processes, as CI does:

```bash
.venv/bin/python -m pytest tests/ -q -n 4 --dist loadgroup
```

`--dist loadgroup` keeps the graph wheel tests (group `wheel`) in one worker,
so the wheel is built once.

The per-test timeout (`timeout = 120` in `pyproject.toml`) needs the
plugin, and `pip install .` does not bring it: without
`pip install pytest-timeout` pytest just warns about an unknown option,
and a hung `join` will hang the whole run — exactly where you least want
it.

The graph-package wheel test builds the wheel offline, without build isolation, so it needs `setuptools` in the venv at the version pinned in `env` of `ci.yml`; a Python 3.12 venv has none. If it is missing or different, the test fails and names the exact command (`<that python> -m pip install setuptools==<pin>`).

No network, no models needed — everything heavy is stubbed. Two guard suites worth knowing: `test_privacy_defaults.py` (silence in the config means *no cloud*) and `test_cloud_call_sites.py` (every point where a request can leave the machine is registered and checked). Swift app tests live next to the apps and run via `swift test` / `xcodebuild test`.

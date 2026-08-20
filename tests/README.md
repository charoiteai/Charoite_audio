# Tests

*[**English**] · [Русский](../docs/ru/tests/README.md) · [中文](../docs/zh/tests/README.md)*

```bash
.venv/bin/python -m pytest tests/ -x -q
```

The per-test timeout (`timeout = 120` in `pyproject.toml`) needs the
plugin, and `pip install .` does not bring it: without
`pip install pytest-timeout` pytest just warns about an unknown option,
and a hung `join` will hang the whole run — exactly where you least want
it.

No network, no models needed — everything heavy is stubbed. Two guard suites worth knowing: `test_privacy_defaults.py` (silence in the config means *no cloud*) and `test_cloud_call_sites.py` (every point where a request can leave the machine is registered and checked). Swift app tests live next to the apps and run via `swift test` / `xcodebuild test`.

# Tests

*[**English**] · [Русский](README.ru.md) · [中文](README.zh.md)*

```bash
.venv/bin/python -m pytest tests/ -x -q
```

No network, no models needed — everything heavy is stubbed. Two guard suites worth knowing: `test_privacy_defaults.py` (silence in the config means *no cloud*) and `test_cloud_call_sites.py` (every point where a request can leave the machine is registered and checked). Swift app tests live next to the apps and run via `swift test` / `xcodebuild test`.

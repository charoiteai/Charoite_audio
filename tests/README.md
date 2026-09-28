# Tests

*[**English**] · [Русский](../docs/ru/tests/README.md) · [中文](../docs/zh/tests/README.md)*

```bash
.venv/bin/pip install pytest pytest-timeout
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
plugin, and `pip install .` does not bring it (nor pytest itself): without
`pytest-timeout` pytest just warns about an unknown option,
and a hung `join` will hang the whole run — exactly where you least want
it.

The graph-package wheel test builds the wheel offline, without build isolation, so it needs `setuptools` in the venv at the version pinned in `env` of `ci.yml`; a Python 3.12 venv has none. If it is missing or different, the test fails and names the exact command (`<that python> -m pip install setuptools==<pin>`).

No network, no models needed — everything heavy is stubbed. `conftest.py`
isolates every test, so a run never touches your real data:

- **Data root and graph.** The session gets a temporary data root
  (`CHAROITE_ROOT` from your shell is set aside and restored afterwards), each
  test gets its own under `tmp_path`, and the graph is an empty folder in
  `tmp_path` (`CHAROITE_GRAPH_DIR`). A test whose subject is the root itself
  says so with the `корень_называет_тест` marker and must still name a
  temporary one; `настоящий_корень_ревизии` leaves the cloud-review root
  un-stubbed.
- **Network.** Sockets are closed: an unexpected request fails the test with
  `pytest.fail`, which an `except Exception` in the code under test cannot
  swallow. Server scenarios are routes keyed by full address; the
  `сеть_разрешена` marker lifts the ban for a test that needs a real socket.
- **Ollama** is down by default; `@pytest.mark.ollama_отвечает("model", …)`
  makes it answer with that model list.
- **Background threads.** A crash in a thread started by a test fails the run
  (`PytestUnhandledThreadExceptionWarning` is an error), so every thread a test
  starts is joined within that test.

A test must be able to fail: `scripts/check_test_assertions.py` (CI and
pre-commit) rejects a test with nothing that can fail, and
`scripts/mutate_check.py` puts defects back into the changed lines and
demands red — see [Contributing](../CONTRIBUTING.md).

Two guard suites worth knowing: `test_privacy_defaults.py` (silence in the config means *no cloud*) and `test_cloud_call_sites.py` (every point where a request can leave the machine is registered and checked). `test_documented_commands.py` checks that commands in the user docs run with the interpreter written in them; `test_import_boundaries.py` and `test_entry_points_contract.py` hold the code layout and each entry point's run contract.

The apps have their own tests next to them: the macOS app —
`swift test --filter '^CharoiteAppTests\.'` in `app/` (live probes in
`app/Probes` run only by hand); iPhone — `xcodebuild … test` on a simulator
(see [app-ios](../app-ios/README.md)); Android — `./gradlew testDebugUnitTest`
in `app-android/`.

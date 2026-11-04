"""Run selected synthetic regression tests with owner-data/network tripwires.

Usage: python -I -B this_file.py PUBLIC_SOURCE OUTPUT_DIR [pytest selectors...]
"""
from pathlib import Path
import json
import os
import sys
import tempfile
import time

source, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
selectors = sys.argv[3:]
output.mkdir(parents=True, exist_ok=True)
runtime = Path(tempfile.mkdtemp(prefix="charoite843-pytest-"))
data = runtime / "data"
data.mkdir()
os.environ.update(CHAROITE_ROOT=str(data),
                  PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", XDG_CONFIG_HOME=str(runtime / "config"),
                  XDG_CACHE_HOME=str(runtime / "cache"), TMPDIR=str(runtime))
sys.dont_write_bytecode = True
events = []
active_node = None


def audit(event, args):
    if event in {"open", "os.listdir", "os.scandir"} and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).absolute()
        if path.is_relative_to(Path.home()) and not any(
            path.is_relative_to(allowed) for allowed in (source, Path(sys.prefix), output)
        ):
            events.append({"event": event, "kind": "owner-path", "nodeid": active_node,
                           "basename": path.name})
            raise PermissionError("owner path refused by synthetic test runner")
    if event == "socket.connect":
        events.append({"event": event, "kind": "network"})
        raise PermissionError("real network refused by synthetic test runner")


sys.addaudithook(audit)
sys.path[:0] = [str(source), str(source / "src"), str(source / "scripts")]
os.chdir(source)
import pytest  # noqa: E402


class Receipt:
    def __init__(self):
        self.nodeids, self.results = [], []

    def pytest_collection_modifyitems(self, items):
        self.nodeids = [item.nodeid for item in items]

    def pytest_runtest_setup(self, item):
        global active_node
        active_node = item.nodeid

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.failed or report.skipped:
            self.results.append({"nodeid": report.nodeid, "phase": report.when, "outcome": report.outcome,
                                 **({"failure": str(report.longrepr)} if report.failed else {})})


plugin = Receipt()
started = time.monotonic()
rc = pytest.main(["-p", "pytest_timeout", "-p", "no:cacheprovider", "-q", "--tb=short",
                  *selectors], plugins=[plugin])
receipt = {"returncode": rc, "source": str(source), "runtime": str(runtime),
           "wall_s": round(time.monotonic() - started, 3), "nodeids": plugin.nodeids,
           "results": plugin.results, "tripwire_events": events, "real_model_calls": 0}
(output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
print("SYNTHETIC_RECEIPT " + json.dumps({key: value for key, value in receipt.items()
                                      if key not in {"nodeids", "results"}}, ensure_ascii=False))
raise SystemExit(rc)

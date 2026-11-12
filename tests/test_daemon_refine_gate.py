"""Run the real refinement closure with synthetic transcript and scheduler.

The closure is extracted intact so this never starts daemon.main or a model.
Importing daemon also keeps the test in mutate_check's daemon test selection.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import daemon


@pytest.mark.parametrize("feed_on,cloud_hints,toggle,scheduled", [
    (False, True, True, True),
    (False, False, True, False),
    (False, True, False, False),
    (True, True, True, False),
])
def test_refinement_schedules_only_for_enabled_classic_cloud_hints(
        feed_on, cloud_hints, toggle, scheduled):
    source = Path(daemon.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    closure = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef) and node.name == "_cloud_refine_thread")
    scheduler = Mock()
    transcript = SimpleNamespace(tail=lambda _limit: "Synthetic fresh speech.",
                                 full=lambda: "Synthetic speech. " * 100)
    namespace = dict(feed_on=feed_on, cloud_hints=cloud_hints,
                     toggles={"cloud": toggle}, tr=transcript, max_ctx=3500,
                     thread=SimpleNamespace(as_context=lambda **_kw: "Synthetic thread."),
                     _refine_last={"len": 0}, threads=scheduler)
    exec(compile(ast.Module(body=[closure], type_ignores=[]), str(source), "exec"), namespace)

    namespace["_cloud_refine_thread"]()
    assert scheduler.spawn.call_count == int(scheduled)
    # The same speech must not schedule another paid refinement.
    namespace["_cloud_refine_thread"]()
    assert scheduler.spawn.call_count == int(scheduled)

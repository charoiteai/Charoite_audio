"""Default-off activation seam between daemon and local grounded feed."""

import ast
from pathlib import Path

from grounded_feed import bridge
from live_feed import FeedPolicy

DAEMON = Path(__file__).resolve().parents[1] / "src" / "daemon.py"


def _source() -> str:
    return DAEMON.read_text(encoding="utf-8")


def test_legacy_and_grounded_activation_keep_feed_policy_contract():
    emitted = []
    for config in (
        {"sufler": {}},
        {"sufler": {"live_feed": False, "auto_hint": False, "auto_expand": False, "instant": False}},
    ):
        policy = FeedPolicy.from_config(config)
        activation = bridge.select(policy, emitted.append)
        assert activation.mode == bridge.LEGACY and activation.session is None
        assert (activation.feed_on, activation.auto_hint, activation.auto_expand) == (
            policy.feed,
            policy.auto_hint,
            policy.auto_expand,
        )

    product = bridge.select(FeedPolicy.from_config({"sufler": {"live_feed": True}}), emitted.append)
    assert product.mode == bridge.GROUNDED and product.session is not None
    assert product.reason == "local_product"
    assert (product.feed_on, product.auto_hint, product.auto_expand) == (True, False, False)
    assert not emitted


GATED_CLASSIC_WORKERS = (
    "gen_hint",
    "gen_answer",
    "_do_summary",
    "expand_topic",
    "cloud_loop",
    "think_loop",
    "auto_hint_loop",
    "instant_loop",
    "fast_trigger_loop",
)


def _grounded_return(stmt, worker):
    if not isinstance(stmt, ast.If) or not stmt.body:
        return False
    body = stmt.body
    if worker == "gen_hint" and len(body) == 2 and isinstance(body[0], ast.Expr):
        call = body[0].value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_telemetry"
            and not call.keywords
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Constant)
            and type(call.args[0].value) is str
        ):
            body = body[1:]
    names = {node.id for node in ast.walk(stmt.test) if isinstance(node, ast.Name)}
    return isinstance(body[0], ast.Return) and body[0].value is None and "grounded_feed" in names


def _hint_pulse_is_local(worker):
    helpers = [stmt for stmt in worker.body if isinstance(stmt, ast.FunctionDef) and stmt.name == "_telemetry"]
    if len(helpers) != 1 or helpers[0].decorator_list:
        return False
    nodes = list(ast.walk(helpers[0]))
    if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in nodes):
        return False
    rebound = {node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    rebound |= {node.arg for node in nodes if isinstance(node, ast.arg)}
    if rebound & {"round", "time", "_telemetry"}:
        return False
    return all(ast.unparse(node.func) in {"round", "time.monotonic"} for node in nodes if isinstance(node, ast.Call))


def test_every_classic_worker_returns_before_any_model_or_thread_write():
    tree = ast.parse(_source())
    found = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for name in GATED_CLASSIC_WORKERS:
        node = found[name]
        if name == "gen_hint":
            assert _hint_pulse_is_local(node), "hint pulse exemption can only read the clock and round"
        guards = [stmt for stmt in ast.walk(node) if _grounded_return(stmt, name)]
        side_effects = []
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Call) and isinstance(stmt.func, ast.Attribute):
                chain = ast.unparse(stmt.func)
                if chain.startswith(("llm.", "cloud.")) or chain.startswith("thread.add_"):
                    side_effects.append(stmt.lineno)
        assert guards, f"{name}: no early grounded_feed return"
        first_guard = min(guard.lineno for guard in guards)
        assert not side_effects or first_guard < min(side_effects), name


def test_daemon_wires_local_grounded_feed_seam_and_checks_authority_before_transcript():
    source = _source()
    tree = ast.parse(source)
    assert isinstance(tree, ast.Module)
    for needle in (
        "grounded_feed_bridge.select(feed_policy, emit)",
        "feed_on, auto_hint_on, auto_expand_on = (grounded_activation.feed_on,",
        "local_authority, local_defer_err = LocalLoopbackAuthority.verify_or_defer(llm)",
        "channel_sink = grounded_feed.add_system if grounded_feed is not None else thread.add_system",
        'if grounded_feed is not None:\n            grounded_feed.run(stop, enabled=lambda: toggles["hints"])',
        "grounded_feed.close(export_path=export_path, session_epoch=tr.stamp,",
    ):
        assert needle in source, needle
    select_at = source.index("grounded_feed_bridge.select(feed_policy, emit)")
    authority_at = source.index("LocalLoopbackAuthority.verify_or_defer(llm)")
    transcript_at = source.index("tr = Transcript(")
    assert select_at < authority_at < transcript_at

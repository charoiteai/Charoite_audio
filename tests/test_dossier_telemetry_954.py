"""Synthetic causal boundaries: real collector/planner, no model or owner data."""
from __future__ import annotations

# Shared pytest fixtures are intentionally imported under their fixture names.
# ruff: noqa: F811, E402

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dossier_incremental as inc
import nightly_dossier as night_cli
import dossier_telemetry as telemetry
import llm
from charoite_graph import dossier
from test_dossier_incremental import world, _new, _responses, LOCAL  # noqa: F401
from test_dossier_encoding import night, GOOD, _watch  # noqa: F401
from test_dossier_ollama_contract import backend  # noqa: F401

SENTINEL = "PRIVATE_CONTENT_SENTINEL_954"


@pytest.fixture
def provider(monkeypatch):
    state = SimpleNamespace(reply=lambda *a, **k: GOOD, calls=0, contract_error=False)

    class Synthetic:
        cloud_ready = False
        engine = "ollama"
        base = "http://127.0.0.1:13701"
        _fell_back_local = False

        def __init__(self, c):
            pass

        def require_local_ollama_model(self, model):
            if state.contract_error:
                raise RuntimeError(SENTINEL)
            return model + ":local"

        def stream(self, prompt, **kwargs):
            state.calls += 1
            assert kwargs["truncate"] is False and kwargs["shift"] is False
            yield state.reply(prompt, **kwargs)

    monkeypatch.setattr(llm, "LLM", Synthetic)
    return state


def aggregate(capsys):
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("dossier_telemetry ")]
    assert len(lines) == 1
    assert SENTINEL not in lines[0]
    data = json.loads(lines[0].split(" ", 1)[1])
    assert data["version"] == 1  # The consumer accepts the declared v1 wire protocol.
    return data


@pytest.mark.parametrize("case,phase,reason,entries", [
    ("preflight", "preflight", "preflight_refused", 0),
    ("admission", "admission", "context_budget", 0),
    ("contract", "contract", "contract_refused", 0),
    ("timeout", "stream", "timeout", 1),
    ("interrupt", "stream", "interrupted", 1),
    ("http", "stream", "http_error", 1),
    ("requests_http", "stream", "http_error", 1),
    ("transport", "stream", "transport_error", 1),
    ("unexpected", "stream", "unexpected_error", 1),
    ("empty", "stream", "empty_reply", 1),
    ("success", "stream", "returned", 1),
])
def test_each_completion_has_one_closed_outcome(provider, capsys, case, phase, reason, entries):
    c = LOCAL
    if case == "preflight":
        c = {"llm": {"engine": "cloud"}}
    if case == "admission":
        c = {"llm": {**LOCAL["llm"], "num_ctx": 1}}
    provider.contract_error = case == "contract"
    errors = {"interrupt": KeyboardInterrupt(SENTINEL), "timeout": requests.Timeout(SENTINEL), "http": llm.LLMHTTPError(503, SENTINEL),
              "requests_http": requests.HTTPError(SENTINEL),
              "transport": requests.ConnectionError(SENTINEL), "unexpected": ValueError(SENTINEL)}

    def reply(*a, **k):
        if case in errors:
            raise errors[case]
        return "" if case == "empty" else SENTINEL

    provider.reply = reply
    collector = telemetry.Collector()
    with collector.activate():
        if case in ("empty", "success"):
            assert inc.local_complete(SENTINEL, c, system=SENTINEL) == reply()
        else:
            with pytest.raises(BaseException if case == "interrupt" else Exception):
                inc.local_complete(SENTINEL, c, system=SENTINEL)
    data = aggregate(capsys)
    assert data["local_completion_attempts"] == 1
    assert data["adapter_stream_entries"] == provider.calls == entries
    assert data["request_outcomes"] == {phase: {reason: 1}}
    assert data["final_refusals"] == 0
    assert telemetry.current() is None


def test_unknown_freeform_values_cannot_enter_projection(capsys):
    collector = telemetry.Collector()
    with collector.activate():
        with pytest.raises(TypeError):
            telemetry.phase(SENTINEL)
        with pytest.raises(TypeError):
            telemetry.refusal(SENTINEL)
    assert aggregate(capsys)["request_outcomes"] == {}


@pytest.mark.parametrize("kind,expected", [("preflight", "preflight_refused"),
                                            ("admission", "context_budget"),
                                            ("contract", "contract_refused"), ("timeout", "timeout"), ("empty", "empty_reply"),
                                            ("format", "format_refused"), ("success", None)])
def test_full_build_terminal_counts_are_topics_not_requests(night, provider, capsys, kind, expected):
    nd, graph, _, _, _ = night
    # Use actual local_complete/generate rather than the fixture's generation double.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(nd, "generate", lambda theme, members, files, c, **kw:
                        inc.local_complete("synthetic", c, system="synthetic", **kw))

    def reply(*a, **k):
        if kind == "timeout":
            raise requests.Timeout(SENTINEL)
        return {"empty": "", "format": SENTINEL}.get(kind, GOOD)

    provider.reply = reply
    provider.contract_error = kind == "contract"
    config = {"llm": {"engine": "cloud"}} if kind == "preflight" else (
        {"llm": {**LOCAL["llm"], "num_ctx": 1}} if kind == "admission" else LOCAL)
    try:
        result = nd.run_all([graph], config, full=False, dry=False)
    finally:
        monkeypatch.undo()
    data = aggregate(capsys)
    assert data["legacy_attempts"] == result.attempts == 3
    assert data["topics_started"] == data["topics_completed"] == 3
    assert data["final_refusals"] == sum(g.отказы for g in result.graphs) == (3 if expected else 0)
    assert data["final_reasons"] == ({expected: 3} if expected else {})
    assert data["committed_topics"] == (0 if expected else 3)
    expected_requests = 6 if kind in ("empty", "format") else 3
    assert data["local_completion_attempts"] == expected_requests
    assert data["adapter_stream_entries"] == (0 if kind in ("preflight", "admission", "contract") else expected_requests)


def test_append_multiple_requests_and_recovered_retry_have_no_final_refusal(world, provider, monkeypatch, capsys):
    nd, graph, path = world
    _new(graph)
    actual = inc.local_complete
    _responses(monkeypatch)
    answer = inc.local_complete
    monkeypatch.setattr(inc, "local_complete", actual)
    generated = 0

    def reply(prompt, **kwargs):
        nonlocal generated
        if kwargs["system"] == "You extend factual dossiers conservatively.":
            generated += 1
            if generated == 1:
                return json.dumps({"body": "bad synthetic candidate", "changes": []})
        return answer(prompt, LOCAL, **kwargs)

    provider.reply = reply
    before = path.read_bytes()
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    data = aggregate(capsys)
    assert path.read_bytes() != before
    assert data["legacy_attempts"] == result.attempts == 1
    assert data["committed_topics"] == 1 and data["final_refusals"] == 0
    assert data["local_completion_attempts"] == data["adapter_stream_entries"] == 6
    assert data["request_outcomes"] == {"stream": {"returned": 6}}


@pytest.mark.parametrize("irrelevant", [False, True])
def test_append_audit_refusal_differs_from_no_contribution_success(world, provider, monkeypatch, capsys, irrelevant):
    nd, graph, path = world
    _new(graph)
    actual = inc.local_complete
    _responses(monkeypatch, irrelevant=("2099-01-02_100000",) if irrelevant else (),
               body="bad synthetic candidate")
    answer = inc.local_complete
    monkeypatch.setattr(inc, "local_complete", actual)
    provider.reply = lambda prompt, **kwargs: answer(prompt, LOCAL, **kwargs)
    before = path.read_bytes()
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    data = aggregate(capsys)
    assert data["legacy_attempts"] == result.attempts == 1
    assert data["committed_topics"] == int(irrelevant)
    assert data["final_refusals"] == int(not irrelevant)
    assert data["final_reasons"] == ({} if irrelevant else {"validation_refused": 1})
    assert data["local_completion_attempts"] == data["adapter_stream_entries"] == (1 if irrelevant else 3)
    if not irrelevant:
        assert path.read_bytes() == before


def test_io_refusal_after_good_text_is_not_a_model_failure(night, provider, monkeypatch, capsys):
    nd, graph, _, paths, _ = night
    before = {name: path.read_bytes() for name, path in paths.items()}
    # Keep the fixture's valid generation double: there is deliberately no request telemetry.
    def denied(*a):
        raise OSError(SENTINEL)
    monkeypatch.setattr(dossier, "backup", denied)
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    data = aggregate(capsys)
    assert result.attempts == data["legacy_attempts"] == 3
    assert data["final_refusals"] == 3 and data["final_reasons"] == {"io_refused": 3}
    assert data["adapter_stream_entries"] == 0
    assert before == {name: path.read_bytes() for name, path in paths.items()}


@pytest.mark.parametrize("index_failure", [False, True])
def test_interruption_keeps_completed_attempt_and_always_emits(night, monkeypatch, capsys, index_failure):
    nd, graph, _, _, _ = night
    builds = 0
    build = nd._build

    def interrupted(*a, **kw):
        nonlocal builds
        builds += 1
        if builds == 2:
            raise KeyboardInterrupt(SENTINEL)
        return build(*a, **kw)

    monkeypatch.setattr(nd, "_build", interrupted)
    if index_failure:
        def denied(*a):
            raise OSError(SENTINEL)
        monkeypatch.setattr(nd._GraphRun, "write_index", denied)
    with pytest.raises(KeyboardInterrupt):
        nd.run_all([graph], LOCAL, full=False, dry=False)
    data = aggregate(capsys)
    assert data["legacy_attempts"] == data["committed_topics"] == 1
    assert data["topics_started"] == 2 and data["topics_completed"] == 1
    assert data["topics_interrupted"] == 1 and data["interrupted"] is True
    assert data["index_failed"] == int(index_failure)
    assert telemetry.current() is None


def test_scan_time_unreadable_refusal_is_in_final_count_without_invented_request(night, monkeypatch, capsys):
    nd, graph, _, paths, _ = night
    _watch(monkeypatch, paths, unreadable=paths["Бета"])
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    data = aggregate(capsys)
    assert data["topics_scanned"] == 3 and data["topics_started"] == 2
    assert data["scan_refusals"] == data["final_refusals"] == 1
    assert data["final_reasons"] == {"input_refused": 1}
    assert data["final_refusals"] == sum(g.отказы for g in result.graphs)
    assert data["legacy_attempts"] == result.attempts == data["committed_topics"] == 2
    assert data["local_completion_attempts"] == data["adapter_stream_entries"] == 0


@pytest.mark.parametrize("broken_stream", [False, True])
def test_actual_response_decoder_distinguishes_200_stream_error_from_http_error(
        night, backend, monkeypatch, capsys, broken_stream):
    nd, graph, _, _, _ = night
    from test_dossier_ollama_contract import CONFIG
    backend.late_error = broken_stream
    backend.template_cost = 0 if broken_stream else 100000
    monkeypatch.setattr(nd, "generate", lambda theme, members, files, c, **kw:
                        inc.local_complete("synthetic", c, system="synthetic", **kw))
    result = nd.run_all([graph], CONFIG, full=False, dry=False)
    data = aggregate(capsys)
    reason = "stream_error" if broken_stream else "http_error"
    assert data["request_outcomes"] == {"stream": {reason: 3}}
    assert data["final_reasons"] == {reason: 3} and data["final_refusals"] == 3
    assert result.attempts == data["legacy_attempts"] == 3
    assert data["adapter_stream_entries"] == data["local_completion_attempts"] == 3
    assert len([call for call in backend.calls if call["method"] == "POST"]) == 3
    assert all(response.closed for _, response in backend.responses)


@pytest.mark.parametrize("original_error", [False, True])
@pytest.mark.parametrize("output_error", [BrokenPipeError, ValueError, RuntimeError, KeyboardInterrupt, SystemExit])
def test_aggregate_output_failure_preserves_an_original_exception(monkeypatch, original_error, output_error):
    import builtins
    original_print = builtins.print
    original = KeyboardInterrupt(SENTINEL + ":original")
    emitted = output_error(SENTINEL + ":emit")

    def broken_print(*args, **kwargs):
        if args and str(args[0]).startswith("dossier_telemetry "):
            raise emitted
        return original_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", broken_print)
    collector = telemetry.Collector()
    expected = KeyboardInterrupt if original_error else output_error
    with pytest.raises(expected, match=SENTINEL) as caught:
        with collector.activate():
            if original_error:
                raise original
    assert caught.value is (original if original_error else emitted)
    assert telemetry.current() is None
    assert collector.interrupted == original_error


def test_another_writer_finishing_after_scan_does_not_invent_an_attempt(night, monkeypatch, capsys):
    nd, graph, _, _, _ = night
    scan = nd._scan
    delivered = {}

    def concurrent_finish(*args, **kwargs):
        group, candidates = scan(*args, **kwargs)
        cand = candidates[0]
        text = dossier.render(cand.theme, GOOD, cand.members, group.files, cand.fp, group.today, group.gen)
        meetings = [name for name in cand.members if nd.CHAROITE.is_meeting_folder(group.files[name]["kind"])]
        text = dossier.with_composition(text, meetings)
        path = group.folder / f"{cand.theme}.md"
        path.write_text(text)
        delivered[path] = path.read_bytes()
        return group, candidates

    monkeypatch.setattr(nd, "_scan", concurrent_finish)
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    data = aggregate(capsys)
    assert data["topics_started"] == data["topics_completed"] == 3
    assert data["legacy_attempts"] == result.attempts == data["committed_topics"] == 2
    assert data["final_refusals"] == 0
    assert all(path.read_bytes() == content for path, content in delivered.items())


def test_prompt_preparation_error_before_a_request_is_not_unknown(world, monkeypatch, capsys):
    _, graph, _ = world
    monkeypatch.setattr(night_cli, "_root", lambda: graph.parent)
    monkeypatch.setattr(night_cli, "generator_label", lambda c: "synthetic")
    monkeypatch.setattr(night_cli.live_gate, "night_window_open", lambda *a, **k: True)

    def broken_prompt(*a, **k):
        raise ValueError(SENTINEL)

    monkeypatch.setattr(dossier, "build_prompt", broken_prompt)
    result = night_cli.run_all([graph], LOCAL, full=True, dry=False)
    data = aggregate(capsys)
    assert data["final_refusals"] == result.attempts == data["legacy_attempts"] == 1
    assert data["final_reasons"] == {"unexpected_error": 1}
    assert data["adapter_stream_entries"] == data["local_completion_attempts"] == 0


def test_an_empty_first_reply_does_not_mask_a_retry_prompt_error(world, provider, monkeypatch, capsys):
    nd, graph, _ = world
    original = dossier.build_prompt
    prepared = 0

    def prepare(*args, **kwargs):
        nonlocal prepared
        prepared += 1
        if prepared == 2:
            raise ValueError(SENTINEL)
        return original(*args, **kwargs)

    monkeypatch.setattr(dossier, "build_prompt", prepare)
    provider.reply = lambda *a, **k: ""
    result = nd.run_all([graph], LOCAL, full=True, dry=False)
    data = aggregate(capsys)
    assert prepared == 2
    assert data["legacy_attempts"] == result.attempts == data["final_refusals"] == 1
    assert data["local_completion_attempts"] == data["adapter_stream_entries"] == 1
    assert data["final_reasons"] == {"unexpected_error": 1}


def test_standalone_completion_keeps_its_result_without_an_active_collector(provider, capsys):
    provider.reply = lambda *a, **k: "synthetic standalone answer"
    assert telemetry.current() is None
    assert inc.local_complete("synthetic", LOCAL, system="synthetic") == "synthetic standalone answer"
    assert provider.calls == 1
    assert "dossier_telemetry " not in capsys.readouterr().out


def test_append_admission_refusal_keeps_its_request_reason(world, provider, capsys):
    nd, graph, path = world
    _new(graph)
    before = path.read_bytes()
    result = nd.run_all([graph], {"llm": {**LOCAL["llm"], "num_ctx": 1}}, full=False, dry=False)
    data = aggregate(capsys)
    assert data["legacy_attempts"] == result.attempts == data["final_refusals"] == 1
    assert data["final_reasons"] == {"context_budget": 1}
    assert data["local_completion_attempts"] == 1 and data["adapter_stream_entries"] == 0
    assert path.read_bytes() == before


def test_last_topic_index_publication_exception_counts_once_and_propagates(night, monkeypatch, capsys):
    nd, graph, _, _, _ = night
    original = RuntimeError(SENTINEL + ":index")
    publications = 0

    def broken_index(*args, **kwargs):
        nonlocal publications
        publications += 1
        raise original

    monkeypatch.setattr(dossier, "rebuild_index", broken_index)
    with pytest.raises(RuntimeError) as caught:
        nd.run_all([graph], LOCAL, full=False, dry=False)
    data = aggregate(capsys)
    assert caught.value is original
    assert publications == 1
    assert data["index_failed"] == 1 and data["interrupted"] is True
    assert data["legacy_attempts"] == data["committed_topics"] == 3
    assert data["final_refusals"] == 0


@pytest.mark.parametrize("caller_handling_error", [False, True])
def test_index_cleanup_error_without_an_original_error_still_propagates(night, monkeypatch, capsys, caller_handling_error):
    nd, graph, _, _, _ = night
    nd.run_all([graph], LOCAL, full=False, dry=False)
    aggregate(capsys)
    original = RuntimeError(SENTINEL + ":cleanup")
    publications = 0

    def broken_index(*args, **kwargs):
        nonlocal publications
        publications += 1
        raise original

    monkeypatch.setattr(dossier, "rebuild_index", broken_index)
    with pytest.raises(RuntimeError) as caught:
        if caller_handling_error:
            try:
                raise ValueError(SENTINEL + ":caller-handled")
            except ValueError:
                nd.run_all([graph], LOCAL, full=False, dry=False)
        else:
            nd.run_all([graph], LOCAL, full=False, dry=False)
    data = aggregate(capsys)
    assert caught.value is original and publications == 1
    assert data["index_failed"] == 1 and data["interrupted"] is True
    assert data["topics_started"] == data["legacy_attempts"] == 0


def test_cli_writes_private_content_free_telemetry_sidecar_and_skips_dry_and_find(night, monkeypatch, capsys):
    nd, graph, _, _, _ = night
    root = nd._root()
    sidecar = nd.charoite_paths.log_path(root, "dossier_telemetry")
    monkeypatch.setattr(nd, "cfg", lambda: LOCAL)

    # --dry and --find must not create or overwrite logs/dossier_telemetry.json.
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph), "--dry"])
    assert nd.main() == 0
    _ = aggregate(capsys)
    assert not sidecar.exists()

    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph), "--find", "Альфа"])
    assert nd.main() == 0
    capsys.readouterr()
    assert not sidecar.exists()

    # Normal CLI run writes 0600 content-free JSON matching stdout telemetry.
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph)])
    assert nd.main() == 0
    stdout_data = aggregate(capsys)
    assert sidecar.exists()
    assert (sidecar.stat().st_mode & 0o777) == 0o600
    assert (sidecar.parent.stat().st_mode & 0o777) == 0o700
    raw = sidecar.read_text(encoding="utf-8")
    assert SENTINEL not in raw and "Альфа" not in raw
    disk_data = json.loads(raw)
    assert isinstance(disk_data.pop("generated"), str)
    assert disk_data == stdout_data


def test_cli_sidecar_records_interrupted_on_sigterm_and_survives_broken_stdout(night, monkeypatch, capsys):
    import builtins
    nd, graph, _, _, _ = night
    root = nd._root()
    sidecar = nd.charoite_paths.log_path(root, "dossier_telemetry")
    monkeypatch.setattr(nd, "cfg", lambda: LOCAL)

    original_build = nd._build

    def sigterm_build(*a, **kw):
        raise SystemExit(143)

    monkeypatch.setattr(nd, "_build", sigterm_build)
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph)])
    with pytest.raises(SystemExit) as caught:
        nd.main()
    assert caught.value.code == 143
    stdout_data = aggregate(capsys)
    disk_data = json.loads(sidecar.read_text(encoding="utf-8"))
    disk_data.pop("generated")
    assert disk_data == stdout_data
    assert disk_data["interrupted"] is True and disk_data["topics_interrupted"] == 1

    # Even if stdout raises BrokenPipeError on telemetry line, on_emit still writes the sidecar.
    monkeypatch.setattr(nd, "_build", original_build)
    original_print = builtins.print

    def broken_print(*args, **kwargs):
        if args and str(args[0]).startswith("dossier_telemetry "):
            raise BrokenPipeError("synthetic closed pipe")
        return original_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", broken_print)
    with pytest.raises(BrokenPipeError):
        nd.run_all([graph], LOCAL, full=True, dry=False,
                   on_telemetry=lambda snap: nd._write_telemetry_sidecar(root, snap))
    disk_after = json.loads(sidecar.read_text(encoding="utf-8"))
    assert disk_after["interrupted"] is False


def test_sidecar_oserror_unlinks_stale_file_logs_stderr_and_preserves_run_result(night, monkeypatch, capsys):
    nd, graph, _, _, _ = night
    root = nd._root()
    sidecar = nd.charoite_paths.log_path(root, "dossier_telemetry")
    nd.charoite_paths.secure_dir(sidecar.parent)
    sidecar.write_text('{"stale": true, "interrupted": false}\n', encoding="utf-8")
    monkeypatch.setattr(nd, "cfg", lambda: LOCAL)

    real_write = nd.safe_write.write_text

    def broken_write(path, text, **kwargs):
        if Path(path) == sidecar:
            raise PermissionError(SENTINEL)
        return real_write(path, text, **kwargs)

    monkeypatch.setattr(nd.safe_write, "write_text", broken_write)
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph)])
    assert nd.main() == 0
    captured = capsys.readouterr()
    assert "итого собрано досье:" in captured.out
    assert "dossier_telemetry_sidecar_failed: PermissionError" in captured.err
    assert SENTINEL not in captured.err and SENTINEL not in captured.out
    assert not sidecar.exists()

    # On SIGTERM + sidecar OSError, exit code 143 and stderr diagnostic both survive cleanly.
    sidecar.write_text('{"stale": true}\n', encoding="utf-8")
    monkeypatch.setattr(nd, "_build", lambda *a, **kw: (_ for _ in ()).throw(SystemExit(143)))
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph), "--full"])
    with pytest.raises(SystemExit) as caught:
        nd.main()
    assert caught.value.code == 143
    captured_sig = capsys.readouterr()
    assert "dossier_telemetry_sidecar_failed: PermissionError" in captured_sig.err
    assert not sidecar.exists()

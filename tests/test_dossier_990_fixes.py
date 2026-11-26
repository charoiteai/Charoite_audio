"""Targeted tests for #990: inline emphasis quotes, [[source]] canonicalization,
context_budget sequential fallback, and closed stage_refusals telemetry."""
from __future__ import annotations

# Shared pytest fixtures are imported under their fixture names.
# ruff: noqa: F811, E402

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dossier_incremental as inc
import dossier_telemetry as telemetry
import llm
import nightly_dossier as night_cli
from charoite_graph import dossier, frontmatter
from test_dossier_incremental import BODY, LOCAL, _new, world  # noqa: F401

SENTINEL = "PRIVATE_DOSSIER_SENTINEL_990"


def _seq_replies(items: list[str]):
    it = iter(items)

    def complete(*args, **kwargs):
        return next(it)

    return complete


def _loaded_dossier(newline: str = "\n", bom: bool = False) -> dossier.DossierLoad:
    raw_text = (
        "---\n"
        "type: досье\n"
        "тема: Glasshouse\n"
        "собрано: 2099-01-01\n"
        "отпечаток: fp-old\n"
        "источников: 1\n"
        "состав:\n"
        "  - 2099-01-01_100000\n"
        "---\n"
        "# Досье: Glasshouse\n\n"
        "## Сейчас\n"
        "- Vent A is open. [[2099-01-01_100000]]\n\n"
        "## Как пришли\n"
        "—\n\n"
        "## Решено\n"
        "—\n\n"
        "## Открыто\n"
        "—\n\n"
        "## Кто в теме\n"
        "—\n\n"
        "## Источники\n"
        "- **Встречи:** [[2099-01-01_100000]]\n\n"
        "## Правки автора\n"
        "Keep exact author note.\n"
    ).replace("\n", newline)
    raw = (("\ufeff" if bom else "") + raw_text).encode("utf-8")
    return dossier.DossierLoad("ok", raw_text, raw=raw)


# ---------------------------------------------------------------------------
# 1. Inline markdown emphasis in _quote_hits and _quote_context
# ---------------------------------------------------------------------------


def test_quote_hits_accepts_inline_bold_italic_and_mixed_wikilink():
    source = (
        "- **Платформа Альфа**: перенос согласован на **25 ноября**, "
        "ответственный — [[Люди/Иван Петров|Иван Петров]].\n"
        "- __Срочно__: проверили *резервный* контур.\n"
        "- По **[[Ядра/Платформа Альфа|Платформа Альфа]]** запущен тест.\n"
    )
    # Stripped bold while keeping [[...]] wikilink verbatim
    assert inc._quote_hits(
        source,
        "Платформа Альфа: перенос согласован на 25 ноября, ответственный — [[Люди/Иван Петров|Иван Петров]].",
    ) == 1
    # Stripped bold + plain wikilink display
    assert inc._quote_hits(
        source,
        "Платформа Альфа: перенос согласован на 25 ноября, ответственный — Иван Петров.",
    ) == 1
    # Partially stripped bold in model quote
    assert inc._quote_hits(
        source,
        "**Платформа Альфа**: перенос согласован на 25 ноября, ответственный — Иван Петров.",
    ) == 1
    # __bold__ and *italic*
    assert inc._quote_hits(source, "Срочно: проверили резервный контур.") == 1
    # Wikilink nested inside **...** (both plain display and preserved [[...]] quote)
    assert inc._quote_hits(source, "По Платформа Альфа запущен тест.") == 1
    assert inc._quote_hits(source, "По [[Ядра/Платформа Альфа|Платформа Альфа]] запущен тест.") == 1
    # Inline bold spanning across a preserved wikilink
    spanned = "- **По [[Ядра/Альфа|Альфа]]**: готово."
    assert inc._quote_hits(spanned, "По [[Ядра/Альфа|Альфа]]: готово.") == 1
    assert inc._quote_hits(spanned, "По Альфа: готово.") == 1
    # Escaped table link with bold in same row
    table = "| [[Ядра/Альфа\\|Альфа]] | **500 RPS** |"
    assert inc._quote_hits(table, "| [[Ядра/Альфа|Альфа]] | 500 RPS |") == 1
    assert inc._quote_hits(table, "| Альфа | 500 RPS |") == 1


@pytest.mark.parametrize("source,bad_quote", [
    ("Использовать `**ключ**` буквально.", "Использовать `ключ` буквально."),
    ("Блок:\n```\n**ключ**\n```\nКонец.", "Блок:\n```\nключ\n```\nКонец."),
    ("Видно %%**скрыто**%% конец.", "Видно %%скрыто%% конец."),
    ("Формула a*b*c даёт результат.", "Формула abc даёт результат."),
    ("Идентификатор foo__bar__baz задан.", "Идентификатор foobarbaz задан."),
    ("Расчёт 2 * 3 * 4 готов.", "Расчёт 2  3  4 готов."),
    ("Строка **с пробелом ** внутри.", "Строка с пробелом  внутри."),
    ("Перенос **начало\nконец** строки.", "Перенос начало\nконец строки."),
    ("Ссылка [[Ядра/Альфа|Альфа]] ок.", "Ссылка [[Чужой/Альфа|Альфа]] ок."),
])
def test_quote_hits_preserves_hidden_zones_and_word_boundaries(source, bad_quote):
    assert inc._quote_hits(source, bad_quote) == 0


def test_quote_hits_avoids_false_ambiguity_for_exact_bold_and_keeps_real_duplicates():
    mixed = "- **Альфа** готова.\n- Альфа в плане."
    assert inc._quote_hits(mixed, "**Альфа**") == 1
    assert inc._quote_context(mixed, "**Альфа**") == mixed

    dup_bold = "- **Альфа** готова.\n- **Альфа** готова."
    assert inc._quote_hits(dup_bold, "Альфа готова.") == 2
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc._quote_context(dup_bold, "Альфа готова.")


def test_source_relevance_and_semantic_check_accept_bold_stripped_quotes(monkeypatch):
    loaded = _loaded_dossier()
    name = "2099-01-02_100000"
    source_text = (
        "- **Glasshouse**: latency dropped from **12s** to **4s**, "
        "owner [[People/Alex|Alex]]."
    )
    files = {name: {"text": source_text, "kind": "Встречи"}}
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({
            "verdict": "relevant",
            "reason": "concrete latency metric",
            "line_span": [1, 1],
        }),
    ]))
    assert inc.source_relevance("Glasshouse", loaded, name, files[name], LOCAL) is True

    body = loaded.parsed.body.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- Latency dropped to 4s. [[{name}]]",
    )
    audited = []
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: audited.append((a[2], kw["quote_context"])))
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({
            "verdict": "accept",
            "conserved": True,
            "supported": True,
            "corrections_valid": True,
            "reason": "supported addition",
            "claims": [{
                "text": "Latency dropped to 4s.",
                "source": name,
                "line_span": [1, 1],
                "relevant": True,
                "supported": True,
                "reason": "direct Glasshouse metric",
            }],
        }),
    ]))
    inc.semantic_check("Glasshouse", loaded, [name], files, LOCAL, body, [])
    assert len(audited) == 1
    assert audited[0][0]["quote"] == source_text


# ---------------------------------------------------------------------------
# 2. _resolve_source_name in deterministic_check, _check_claims, semantic_check
# ---------------------------------------------------------------------------


def test_resolve_source_name_accepts_exact_and_strict_wikilink_only():
    names = ["2099-01-02_100000"]
    assert inc._resolve_source_name("2099-01-02_100000", names) == "2099-01-02_100000"
    assert inc._resolve_source_name("[[2099-01-02_100000]]", names) == "2099-01-02_100000"
    for bad in (
        "[[2099-01-02_100000|Alias]]",
        "[[Folder/2099-01-02_100000]]",
        "[[2099-01-02_100000#sec]]",
        "[[2099-01-02_100000^blk]]",
        "[[ 2099-01-02_100000 ]]",
        "[[unknown]]",
        "[2099-01-02_100000]",
        "[[2099-01-02_100000]",
        "2099-01-02_100000]]",
        "[[]]",
    ):
        assert inc._resolve_source_name(bad, names) is None


def test_deterministic_check_accepts_wikilink_source_in_changes():
    loaded = _loaded_dossier()
    name = "2099-01-02_100000"
    files = {name: {"text": "**Vent A** is now closed.", "kind": "Встречи"}}
    body = (
        "## Сейчас\n"
        f"- Vent A is now closed. [[{name}]]\n\n"
        "## Как пришли\n"
        "- Vent A is open. [[2099-01-01_100000]]\n\n"
        "## Решено\n—\n\n## Открыто\n—\n\n## Кто в теме\n—"
    )
    changes = [{
        "before": "Vent A is open. [[2099-01-01_100000]]",
        "after": f"Vent A is now closed. [[{name}]]",
        "source": f"[[{name}]]",
        "quote": "Vent A is now closed.",
    }]
    inc.deterministic_check(loaded, body, [name], files, changes)
    # Canonicalized in place so semantic_check receives the plain key without [[ ]]
    assert changes[0]["source"] == name

    bad_changes = [{**changes[0], "source": f"[[{name}|Alias]]"}]
    with pytest.raises(inc.Refused, match="unsupported or unattributed correction ledger"):
        inc.deterministic_check(loaded, body, [name], files, bad_changes)


def test_semantic_check_canonicalizes_wikilink_claim_source_and_catches_duplicates(monkeypatch):
    loaded = _loaded_dossier()
    name = "2099-01-02_100000"
    files = {name: {"text": "Cover fitted on Monday.", "kind": "Встречи"}}
    body = loaded.parsed.body.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- Cover fitted on Monday. [[{name}]]",
    )
    audited = []
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: audited.append((a[2]["source"], kw["quote_context"])))
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({
            "verdict": "accept",
            "conserved": True,
            "supported": True,
            "corrections_valid": True,
            "reason": "supported",
            "claims": [{
                "text": "Cover fitted on Monday.",
                "source": f"[[{name}]]",
                "line_span": [1, 1],
                "relevant": True,
                "supported": True,
                "reason": "about Glasshouse",
            }],
        }),
    ]))
    inc.semantic_check("Glasshouse", loaded, [name], files, LOCAL, body, [])
    assert audited == [(name, "Cover fitted on Monday.")]

    dup_claims = [
        {"text": "Cover fitted on Monday.", "source": name, "quote": "Cover fitted on Monday.",
         "relevant": True, "supported": True, "reason": "r1"},
        {"text": "Cover fitted on Monday.", "source": f"[[{name}]]", "quote": "Cover fitted on Monday.",
         "relevant": True, "supported": True, "reason": "r2"},
    ]
    with pytest.raises(inc.Refused, match="duplicate candidate claim"):
        inc._check_claims(loaded, body, [name], files, dup_claims, [])


# ---------------------------------------------------------------------------
# 3. Variant B: sequential fallback on Reason.CONTEXT for len(accepted) > 1
# ---------------------------------------------------------------------------


def test_nightly_add_falls_back_sequentially_only_on_multi_source_context_budget(world, monkeypatch):
    nd, graph, old_path = world
    crlf_text = old_path.read_text(encoding="utf-8").replace("\n", "\r\n")
    old_path.write_bytes(("\ufeff" + crlf_text).encode("utf-8"))
    crlf_loaded = dossier.load_dossier(old_path)

    m1, m2, m3 = "2099-01-02_100000", "2099-01-03_100000", "2099-01-04_100000"
    _new(graph, m1, "Glasshouse update one.")
    _new(graph, m2, "Glasshouse update two.")
    _new(graph, m3, "Unrelated chatter mentioning Glasshouse.")

    relevance_calls = []
    append_calls = []

    def fake_relevance(theme, loaded, name, meta, c):
        relevance_calls.append(name)
        return name != m3

    def fake_append(theme, loaded, names, files, c):
        append_calls.append((list(names), loaded.parsed.body))
        if len(names) > 1:
            telemetry.refusal(telemetry.Reason.CONTEXT)
            raise inc.Refused("batch exceeds MAX_AUDIT_CONTEXT", reason=telemetry.Reason.CONTEXT)
        src = names[0]
        return loaded.parsed.body.replace(
            "## Как пришли",
            f"- Fact from {src}. [[{src}]]\n\n## Как пришли",
        )

    monkeypatch.setattr(inc, "source_relevance", fake_relevance)
    monkeypatch.setattr(inc, "append", fake_append)

    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert sum(g.built for g in result.graphs) == 1
    assert sum(g.отказы for g in result.graphs) == 0
    # source_relevance called once per new source (never repeated during fallback)
    assert sorted(relevance_calls) == sorted([m1, m2, m3])
    # 1 batch call + 2 sequential calls, step 2 sees step 1's addition
    assert len(append_calls) == 3
    assert len(append_calls[0][0]) == 2
    assert len(append_calls[1][0]) == 1 and len(append_calls[2][0]) == 1
    assert f"Fact from {append_calls[1][0][0]}" in append_calls[2][1]

    raw_bytes = old_path.read_bytes()
    assert b"\r\n" in raw_bytes
    final_text = raw_bytes.decode("utf-8-sig")
    final_body = dossier.DossierLoad("ok", final_text).parsed.body
    assert f"Fact from {m1}" in final_body and f"Fact from {m2}" in final_body
    assert f"[[{m1}]]" in final_text and f"[[{m2}]]" in final_text
    assert m3 in frontmatter.list_field(final_text, dossier.REJECTED_FIELD)
    # rejection_version computed against original loaded.text (before body updates)
    m3_text = (graph / "Встречи" / f"{m3}.md").read_text(encoding="utf-8")
    expected_rv = f"{dossier.rejection_version(crlf_loaded.text, m3, {'text': m3_text})}:{m3}"
    assert expected_rv in frontmatter.list_field(final_text, dossier.REJECTION_VERSIONS_FIELD)
    assert "Keep purple labels." in final_text

    snap = result.telemetry.snapshot()
    assert snap["committed_topics"] == 1
    assert snap["final_refusals"] == 0
    assert snap["stage_refusals"] == {}


def test_append_result_refuses_empty_body_when_sources_accepted():
    loaded = _loaded_dossier()
    with pytest.raises(inc.Refused, match="invalid rendered append"):
        night_cli._append_result(loaded, None, ["2099-01-02_100000"], [], None, "fp")
    with pytest.raises(inc.Refused, match="invalid rendered append"):
        night_cli._append_result(loaded, "   ", ["2099-01-02_100000"], [], None, "fp")


def test_stage_refusals_preserves_empty_reply_and_io_stage(world, monkeypatch):
    nd, graph, old_path = world
    _new(graph, "2099-01-02_100000", "Glasshouse vent sealed.")

    class EmptyLLM:
        cloud_ready = False
        engine = "ollama"
        base = "http://127.0.0.1:11434"
        _fell_back_local = False

        def __init__(self, c):
            pass

        def require_local_ollama_model(self, model):
            return model + ":local"

        def stream(self, prompt, **kwargs):
            yield ""

    monkeypatch.setattr(llm, "LLM", EmptyLLM)
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    snap = result.telemetry.snapshot()
    assert snap["final_reasons"] == {"empty_reply": 1}
    assert snap["stage_refusals"] == {"source_relevance": {"empty_reply": 1}}

    # IO error during backup on add path reports append_commit (not unknown)
    monkeypatch.setattr(inc, "source_relevance", lambda *a, **kw: False)
    monkeypatch.setattr(dossier, "backup", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    result_io = nd.run_all([graph], LOCAL, full=False, dry=False)
    snap_io = result_io.telemetry.snapshot()
    assert snap_io["final_reasons"] == {"io_refused": 1}
    assert snap_io["stage_refusals"] == {"append_commit": {"io_refused": 1}}


def test_semantic_check_runs_emphasis_fallback_once_per_claim(monkeypatch):
    loaded = _loaded_dossier()
    name = "2099-01-02_100000"
    files = {name: {"text": "**Glasshouse** vent is sealed.", "kind": "Встречи"}}
    body = loaded.parsed.body.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- Glasshouse vent is sealed. [[{name}]]",
    )
    orig_emphasis = inc._emphasis_free
    calls = []

    def counting_emphasis(text, **kw):
        calls.append(text)
        return orig_emphasis(text, **kw)

    monkeypatch.setattr(inc, "_emphasis_free", counting_emphasis)
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: None)
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Glasshouse vent is sealed.", "source": name,
                        "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
        }),
    ]))
    inc.semantic_check("Glasshouse", loaded, [name], files, LOCAL, body, [])
    # Verbatim line_span slices match raw text directly without invoking emphasis fallback
    assert calls == []
    # Direct _check_claims with stripped bold quote still runs emphasis fallback once per claim (text + quote)
    inc._check_claims(loaded, body, [name], files, [{
        "text": "Glasshouse vent is sealed.", "source": name,
        "quote": "Glasshouse vent is sealed.", "relevant": True, "supported": True, "reason": "ok",
    }], [])
    assert len(calls) == 2


def test_nightly_add_fallback_failure_records_actual_reason_not_stale_context(world, monkeypatch):
    nd, graph, old_path = world
    before = old_path.read_bytes()
    _new(graph, "2099-01-02_100000", "Glasshouse one.")
    _new(graph, "2099-01-03_100000", "Glasshouse two.")

    calls = {"n": 0}
    monkeypatch.setattr(inc, "source_relevance", lambda *a, **kw: True)

    def fake_append(theme, loaded, names, files, c):
        calls["n"] += 1
        if len(names) > 1:
            with telemetry.stage(telemetry.Stage.APPEND):
                telemetry.refusal(telemetry.Reason.CONTEXT)
                raise inc.Refused("batch context exceeded", reason=telemetry.Reason.CONTEXT)
        if calls["n"] == 2:
            return loaded.parsed.body.replace("## Как пришли", f"- Fact one. [[{names[0]}]]\n\n## Как пришли")
        with telemetry.stage(telemetry.Stage.SEMANTIC):
            telemetry.detail(telemetry.Detail.SEMANTIC_REJECTED)
            raise inc.Refused("candidate semantic conservation was not accepted")

    monkeypatch.setattr(inc, "append", fake_append)

    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert old_path.read_bytes() == before
    snap = result.telemetry.snapshot()
    assert snap["committed_topics"] == 0
    assert snap["final_refusals"] == 1
    assert snap["final_reasons"] == {"validation_refused": 1}
    assert snap["stage_refusals"] == {"semantic_check": {"semantic_rejected": 1}}


def test_nightly_add_no_fallback_for_single_source_or_non_context_error(world, monkeypatch):
    nd, graph, old_path = world
    _new(graph, "2099-01-02_100000", "Glasshouse huge single source.")

    calls = []
    monkeypatch.setattr(inc, "source_relevance", lambda *a, **kw: True)

    def single_context(theme, loaded, names, files, c):
        calls.append(list(names))
        with telemetry.stage(telemetry.Stage.APPEND):
            telemetry.refusal(telemetry.Reason.CONTEXT)
            raise inc.Refused("single exceeds context", reason=telemetry.Reason.CONTEXT)

    monkeypatch.setattr(inc, "append", single_context)
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert len(calls) == 1
    snap = result.telemetry.snapshot()
    assert snap["final_reasons"] == {"context_budget": 1}
    assert snap["stage_refusals"] == {"append": {"context_budget": 1}}

    # Two sources with non-context refusal must also not fall back sequentially
    _new(graph, "2099-01-03_100000", "Glasshouse second.")
    calls.clear()

    def batch_validation(theme, loaded, names, files, c):
        calls.append(list(names))
        with telemetry.stage(telemetry.Stage.DETERMINISTIC):
            telemetry.detail(telemetry.Detail.LINKS_LOST)
            raise inc.Refused("candidate lost a canonical link target")

    monkeypatch.setattr(inc, "append", batch_validation)
    result2 = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert len(calls) == 1 and len(calls[0]) == 2
    snap2 = result2.telemetry.snapshot()
    assert snap2["final_reasons"] == {"validation_refused": 1}
    assert snap2["stage_refusals"] == {"deterministic_check": {"links_lost": 1}}


# ---------------------------------------------------------------------------
# 4. Closed content-free stage_refusals telemetry
# ---------------------------------------------------------------------------


def test_stage_and_detail_reject_freeform_strings():
    collector = telemetry.Collector()
    with collector.activate():
        with pytest.raises(TypeError):
            with telemetry.stage(SENTINEL):
                pass
        with pytest.raises(TypeError):
            telemetry.detail(SENTINEL)
    snap = collector.snapshot()
    assert SENTINEL not in json.dumps(snap)
    assert snap["stage_refusals"] == {}


@pytest.mark.parametrize("replies,expected_detail", [
    (["not json at all " + SENTINEL], "json_malformed"),
    ([json.dumps({"verdict": "relevant", "reason": "ok"})], "json_schema"),
    ([json.dumps({"verdict": "relevant", "reason": "   ", "line_span": [2, 2]})], "relevance_incomplete"),
    ([json.dumps({"verdict": "relevant", "reason": "ok", "line_span": []})] * 2, "relevance_quote_empty"),
    ([json.dumps({"verdict": "relevant", "reason": "ok", "line_span": [99, 99]})] * 2, "relevance_quote_missing"),
    ([json.dumps({"verdict": "uncertain", "reason": "ambiguous", "line_span": []})], "relevance_uncertain"),
])
def test_stage_refusals_classifies_source_relevance_failures(world, monkeypatch, replies, expected_detail):
    nd, graph, _ = world
    _new(graph, "2099-01-02_100000", f"Glasshouse fact {SENTINEL}.")
    calls = []

    def fake_complete(*args, **kwargs):
        calls.append(args[0])
        return replies[len(calls) - 1]

    monkeypatch.setattr(inc, "local_complete", fake_complete)

    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    snap = result.telemetry.snapshot()
    assert len(calls) == len(replies)
    assert SENTINEL not in json.dumps(snap)
    assert snap["final_refusals"] == 1
    assert snap["final_reasons"] == {"validation_refused": 1}
    assert snap["stage_refusals"] == {"source_relevance": {expected_detail: 1}}


def test_stage_refusals_stays_empty_when_append_recovers_on_retry(world, monkeypatch):
    nd, graph, _ = world
    name = "2099-01-02_100000"
    _new(graph, name, "Glasshouse vent sealed.")
    good_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": name,
                       "line_span": [2, 2]}],
        "corrections": [],
    })
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({"verdict": "relevant", "reason": "fact", "line_span": [2, 2]}),
        "bad json attempt 0",
        good_patch,
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Glasshouse vent sealed.", "source": name,
                        "line_span": [2, 2], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
        json.dumps({"verdict": "accept", "relevant": True, "reason": "ok"}),
    ]))

    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    snap = result.telemetry.snapshot()
    assert snap["committed_topics"] == 1
    assert snap["final_refusals"] == 0
    assert snap["stage_refusals"] == {}


def test_stage_refusals_captures_audit_topic_rejection(world, monkeypatch):
    nd, graph, _ = world
    name = "2099-01-02_100000"
    _new(graph, name, "Glasshouse vent sealed.")
    good_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": name,
                       "line_span": [2, 2]}],
        "corrections": [],
    })
    one_attempt = [
        good_patch,
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Glasshouse vent sealed.", "source": name,
                        "line_span": [2, 2], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
        json.dumps({"verdict": "reject", "relevant": False, "reason": "off topic"}),
    ]
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({"verdict": "relevant", "reason": "fact", "line_span": [2, 2]}),
        *one_attempt,
        *one_attempt,
    ]))

    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    snap = result.telemetry.snapshot()
    assert snap["final_refusals"] == 1
    assert snap["stage_refusals"] == {"audit_topic": {"audit_topic_rejected": 1}}


def test_stage_refusals_covers_append_deterministic_semantic_and_audit_support(world, monkeypatch):
    nd, graph, _ = world
    name = "2099-01-02_100000"
    _new(graph, name, "Glasshouse vent sealed.")
    good_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": name,
                       "line_span": [2, 2]}],
        "corrections": [],
    })
    bad_section_patch = json.dumps({
        "additions": [{"section": "Источники", "text": "Glasshouse vent sealed.", "source": name,
                       "line_span": [2, 2]}],
        "corrections": [],
    })
    empty_patch = json.dumps({"additions": [], "corrections": []})
    rel_ok = json.dumps({"verdict": "relevant", "reason": "fact", "line_span": [2, 2]})
    sem_ok = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": "Glasshouse vent sealed.", "source": name,
                    "line_span": [2, 2], "relevant": True, "supported": True, "reason": "ok"}],
    })
    cases = [
        ([rel_ok, "not json", "still not json"], {"append": {"json_malformed": 1}}),
        ([rel_ok, bad_section_patch, bad_section_patch], {"append": {"sections_invalid": 1}}),
        ([rel_ok, empty_patch, empty_patch], {"deterministic_check": {"source_unattributed": 1}}),
        ([rel_ok, good_patch,
          json.dumps({"verdict": "reject", "conserved": True, "supported": False,
                      "corrections_valid": True, "reason": "no", "claims": []}),
          good_patch,
          json.dumps({"verdict": "reject", "conserved": True, "supported": False,
                      "corrections_valid": True, "reason": "no", "claims": []})],
         {"semantic_check": {"semantic_supported_false": 1}}),
        ([rel_ok, good_patch,
          json.dumps({"verdict": "reject", "conserved": True, "supported": True,
                      "corrections_valid": True, "reason": "no", "claims": []}),
          good_patch,
          json.dumps({"verdict": "reject", "conserved": True, "supported": True,
                      "corrections_valid": True, "reason": "no", "claims": []})],
         {"semantic_check": {"semantic_rejected": 1}}),
        ([rel_ok, good_patch, sem_ok,
          json.dumps({"verdict": "reject", "supported": False, "reason": "unsupported"}),
          good_patch, sem_ok,
          json.dumps({"verdict": "reject", "supported": False, "reason": "unsupported"})],
         {"audit_support": {"audit_support_rejected": 1}}),
    ]
    for replies, expected_stage in cases:
        monkeypatch.setattr(inc, "local_complete", _seq_replies(replies))
        result = nd.run_all([graph], LOCAL, full=False, dry=False)
        snap = result.telemetry.snapshot()
        assert snap["final_refusals"] == 1
        assert snap["stage_refusals"] == expected_stage

    # Direct validate_candidate still records deterministic_check/sections_invalid.
    collector = telemetry.Collector()
    loaded = _loaded_dossier()
    with collector.activate():
        with collector.topic():
            try:
                inc.validate_candidate("Glasshouse", loaded, [name], {name: {"text": "Glasshouse vent sealed."}},
                                       LOCAL, "## Сейчас\n—", [])
            except inc.Refused as exc:
                telemetry.record_refusal_message(str(exc))
                telemetry.request_refusal(telemetry.Reason.VALIDATION)
            collector.complete_topic(attempted=True, committed=0, refused=1)
    assert collector.snapshot()["stage_refusals"] == {"deterministic_check": {"sections_invalid": 1}}


# ---------------------------------------------------------------------------
# 5. Round 2 (#990): trivial wikilink alias folding, hidden-zone masking,
#    CRLF normalization, source_relevance bounded retry, dynamic _append_predict
# ---------------------------------------------------------------------------


def test_quote_hits_folds_trivial_wikilink_alias_and_normalizes_crlf():
    src = (
        "- Срок для [[Ядра/Платформа Альфа]] перенесён.\r\n"
        "- В таблице | [[Ядра/Платформа Альфа\\|Платформа Альфа]] | 28.11 |\r\n"
        "- Вложение ![[Файлы/Схема|Схема]] готово.\r\n"
        "- См. **Важно**: `api_v2` и [[Ядра/Альфа]].\r\n"
    )
    # Trivial alias added by model when source has bare [[Folder/Stem]]
    assert inc._quote_hits(src, "Срок для [[Ядра/Платформа Альфа|Платформа Альфа]] перенесён.") == 1
    # Bare [[Folder/Stem]] when source has escaped table [[Folder/Stem\|Stem]]
    assert inc._quote_hits(src, "| [[Ядра/Платформа Альфа]] | 28.11 |") == 1
    # Embed ![[Folder/Stem|Stem]] <-> ![[Folder/Stem]]
    assert inc._quote_hits(src, "Вложение ![[Файлы/Схема]] готово.") == 1
    # Combined stripped bold + preserved inline code + folded trivial alias
    assert inc._quote_hits(src, "Важно: `api_v2` и [[Ядра/Альфа|Альфа]].") == 1
    # Multiline CRLF in source vs LF in JSON quote (and vice versa)
    assert inc._quote_hits(
        src,
        "Срок для [[Ядра/Платформа Альфа|Платформа Альфа]] перенесён.\n"
        "- В таблице | [[Ядра/Платформа Альфа]] | 28.11 |",
    ) == 1
    assert inc._quote_hits("Line A\nLine B", "Line A\r\nLine B") == 1


@pytest.mark.parametrize("source,bad_quote", [
    # Hidden non-rendered zones must NEVER match folded trivial aliases from outside
    ("Пример `[[Ядра/Glasshouse]]` в коде.", "[[Ядра/Glasshouse|Glasshouse]]"),
    ("Комментарий %% [[Ядра/Glasshouse]] %% скрыт.", "[[Ядра/Glasshouse|Glasshouse]]"),
    ("```\n[[Ядра/Glasshouse]]\n```", "[[Ядра/Glasshouse|Glasshouse]]"),
    # Heading/block anchors and non-trivial or empty aliases must not fold
    ("Раздел [[Ядра/Альфа#Секция|Альфа]] важен.", "Раздел [[Ядра/Альфа]] важен."),
    ("Раздел [[Ядра/Альфа#Секция|Альфа]] важен.", "Раздел [[Ядра/Альфа#Секция]] важен."),
    ("Блок [[Ядра/Альфа^b1|Альфа]] важен.", "Блок [[Ядра/Альфа]] важен."),
    ("Блок [[Ядра/Альфа^b1|Альфа]] важен.", "Блок [[Ядра/Альфа^b1]] важен."),
    ("Ссылка [[Ядра/Альфа|Витрина]] важна.", "Ссылка [[Ядра/Альфа]] важна."),
    ("Пустой [[Ядра/Альфа| ]] алиас.", "Пустой [[Ядра/Альфа]] алиас."),
    # Different folder target or fabricated link must never match
    ("Ссылка [[Ядра/Альфа|Альфа]] ок.", "Ссылка [[Чужой/Альфа|Альфа]] ок."),
    ("A Glasshouse B", "A [[Выдуманная/Glasshouse|Glasshouse]] B"),
    # Embed vs non-embed mismatch
    ("Вложение ![[Файлы/Схема|Схема]] готово.", "Вложение [[Файлы/Схема]] готово."),
])
def test_quote_hits_blocks_hidden_zones_anchors_and_nontrivial_aliases(source, bad_quote):
    assert inc._quote_hits(source, bad_quote) == 0


def test_source_relevance_retries_once_on_bad_quote_and_recovers_cleanly(world, monkeypatch):
    nd, graph, _ = world
    name = "2099-01-02_100000"
    _new(graph, name, "| [[Ядра/Glasshouse\\|Glasshouse]] | Vent sealed on Monday. |")
    seen_prompts = []
    replies = iter([
        json.dumps({"verdict": "relevant", "reason": "fact", "line_span": [99, 99]}),
        json.dumps({"verdict": "relevant", "reason": "fact", "line_span": [2, 2]}),
        json.dumps({"additions": [{"section": "Сейчас", "text": "Vent sealed on Monday.", "source": name,
                                   "line_span": [2, 2]}],
                    "corrections": []}),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Vent sealed on Monday.", "source": name,
                        "line_span": [2, 2], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
        json.dumps({"verdict": "accept", "relevant": True, "reason": "ok"}),
    ])

    def fake_complete(prompt, config, *, system, **kwargs):
        if system == inc.SOURCE_RELEVANCE_SYSTEM:
            seen_prompts.append((prompt, config["llm"]["num_ctx"]))
        return next(replies)

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    snap = result.telemetry.snapshot()
    assert len(seen_prompts) == 2
    retry_payload, _ = json.JSONDecoder().raw_decode(seen_prompts[1][0])
    assert "span_feedback" in retry_payload
    assert retry_payload["span_feedback"]["problem"] == "span_out_of_range"
    assert "1 <= start <= end" in retry_payload["span_feedback"]["instruction"]
    assert seen_prompts[0][1] == seen_prompts[1][1]
    assert snap["committed_topics"] == 1
    assert snap["final_refusals"] == 0
    assert snap["stage_refusals"] == {}


def test_source_relevance_retry_preserves_stage_on_second_failure_and_skips_retry_at_ctx_edge(world, monkeypatch):
    nd, graph, _ = world
    name = "2099-01-02_100000"
    _new(graph, name, "Glasshouse vent sealed.")
    # Failure on attempt 1 (malformed JSON) must still be recorded under source_relevance, not unknown.
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({"verdict": "relevant", "reason": "fact", "line_span": [99, 99]}),
        "not json on retry",
    ]))
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    snap = result.telemetry.snapshot()
    assert snap["stage_refusals"] == {"source_relevance": {"json_malformed": 1}}

    # Exact ctx boundary for retry: needed == 8192 retries (2 calls), needed == 8193 skips retry (1 call)
    # and reports original relevance_quote_missing rather than turning it into context_budget or reloading Ollama.
    def _run_edge(pad_len: int):
        _new(graph, name, "Glasshouse fact. " + ("a" * pad_len))
        seen_ctx = []

        def bad_quote(prompt, config, **kwargs):
            seen_ctx.append((config["llm"]["num_ctx"], inc._stage_needed_tokens(len(inc.SOURCE_RELEVANCE_SYSTEM), prompt, inc.SOURCE_PREDICT)))
            return json.dumps({"verdict": "relevant", "reason": "fact", "line_span": [99, 99]})

        monkeypatch.setattr(inc, "local_complete", bad_quote)
        res = nd.run_all([graph], LOCAL, full=False, dry=False)
        return seen_ctx, res.telemetry.snapshot()

    # Probe retry prompt overhead dynamically so the test pins the exact 8192 vs 8193 token boundary.
    probe_ctx, _ = _run_edge(100)
    delta_chars = (inc.MIN_AUTO_CONTEXT - probe_ctx[1][1]) * inc.ADMISSION_CHARS_PER_TOKEN
    exact_fit_pad = 100 + delta_chars

    calls_fit, snap_fit = _run_edge(exact_fit_pad)
    assert len(calls_fit) == 2 and calls_fit[0][0] == inc.MIN_AUTO_CONTEXT and calls_fit[1][1] == inc.MIN_AUTO_CONTEXT
    assert snap_fit["stage_refusals"] == {"source_relevance": {"relevance_quote_missing": 1}}

    calls_over, snap_over = _run_edge(exact_fit_pad + inc.ADMISSION_CHARS_PER_TOKEN)
    assert len(calls_over) == 1 and calls_over[0][0] == inc.MIN_AUTO_CONTEXT
    assert snap_over["final_reasons"] == {"validation_refused": 1}
    assert snap_over["stage_refusals"] == {"source_relevance": {"relevance_quote_missing": 1}}


def test_append_uses_fixed_gen_predict_and_trims_retry_feedback(monkeypatch):
    # _trim_feedback_response preserves short responses and trims long ones head+tail
    short = "a" * inc.APPEND_FEEDBACK_RESPONSE_CHARS
    assert inc._trim_feedback_response(short) == short
    long_raw = "HEAD" + ("m" * 3000) + "TAIL"
    trimmed = inc._trim_feedback_response(long_raw)
    assert trimmed.startswith("HEAD") and trimmed.endswith("TAIL")
    assert "\n...\n" in trimmed and len(trimmed) == inc.APPEND_FEEDBACK_RESPONSE_CHARS + len("\n...\n")

    # End-to-end append on a large dossier keeps fixed GEN_PREDICT (delta patch only)
    # and trims previous_response on retry
    large_body = BODY.replace(
        "## Как пришли\n—",
        "## Как пришли\n" + "\n".join(f"- Line {i} " + ("я" * 160) + " [[2099-01-01_100000]]" for i in range(26)),
    )
    large_raw = dossier.render(
        "Glasshouse", large_body, ["2099-01-01_100000"],
        {"2099-01-01_100000": {"kind": "Встречи", "text": "ok"}}, "fp-old", "2099-01-01",
    )
    large_raw = dossier.with_composition(large_raw, ["2099-01-01_100000"])
    loaded = dossier.DossierLoad("ok", large_raw, raw=large_raw.encode("utf-8"))

    name = "2099-01-02_100000"
    files = {name: {"text": "Glasshouse vent sealed.", "kind": "Встречи"}}
    seen = []
    huge_bad = "{" + ("z" * 4500)
    good_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": name,
                       "line_span": [1, 1]}],
        "corrections": [],
    })
    replies = iter([huge_bad, good_patch])

    def fake_complete(prompt, config, *, system, num_predict, **kwargs):
        seen.append((prompt, config["llm"]["num_ctx"], num_predict))
        return next(replies)

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    monkeypatch.setattr(inc, "validate_candidate", lambda *a, **kw: None)
    out = inc.append("Glasshouse", loaded, [name], files, LOCAL)
    assert "Glasshouse vent sealed." in out and f"[[{name}]]" in out
    assert len(seen) == 2
    assert seen[0][2] == inc.APPEND_PREDICT and seen[1][2] == inc.APPEND_PREDICT
    retry_payload, _ = json.JSONDecoder().raw_decode(seen[1][0])
    prev = retry_payload["correction_feedback"]["previous_response"]
    assert "\n...\n" in prev and len(prev) == inc.APPEND_FEEDBACK_RESPONSE_CHARS + len("\n...\n")


# ---------------------------------------------------------------------------
# 6. Round 3 (#990): DOC_STREAM_TIMEOUT, append missing_citations feedback,
#    and retry context-budget guard before telemetry.begin_attempt()
# ---------------------------------------------------------------------------


def test_local_complete_uses_doc_stream_timeout(monkeypatch):
    seen_timeouts = []

    class DocLLM:
        cloud_ready = False
        engine = "ollama"
        base = "http://127.0.0.1:11434"
        _fell_back_local = False

        def __init__(self, c):
            pass

        def require_local_ollama_model(self, model):
            return model + ":local"

        def stream(self, prompt, **kwargs):
            seen_timeouts.append(kwargs.get("timeout"))
            yield '{"ok":true}'

    monkeypatch.setattr(llm, "LLM", DocLLM)
    assert inc.local_complete("hello", LOCAL, system="sys", num_predict=64) == '{"ok":true}'
    assert seen_timeouts == [llm.DOC_STREAM_TIMEOUT]


def test_append_retry_feedback_missing_citations_covers_all_four_outcomes(monkeypatch):
    raw_doc = dossier.render(
        "Glasshouse", BODY, ["2099-01-01_100000"],
        {"2099-01-01_100000": {"kind": "Встречи", "text": "ok"}}, "fp-old", "2099-01-01",
    )
    raw_doc = dossier.with_composition(raw_doc, ["2099-01-01_100000"])
    loaded = dossier.DossierLoad("ok", raw_doc, raw=raw_doc.encode("utf-8"))
    m1, m2, m3 = "2099-01-02_100000", "2099-01-03_100000", "2099-01-04_100000"
    files = {
        m1: {"text": "Glasshouse fact 1.", "kind": "Встречи"},
        m2: {"text": "Glasshouse fact 2.", "kind": "Встречи"},
        m3: {"text": "Glasshouse fact 3.", "kind": "Встречи"},
    }
    full_patch = json.dumps({
        "additions": [
            {"section": "Сейчас", "text": "F1", "source": m1, "line_span": [1, 1]},
            {"section": "Сейчас", "text": "F2", "source": m2, "line_span": [1, 1]},
            {"section": "Сейчас", "text": "F3", "source": m3, "line_span": [1, 1]},
        ],
        "corrections": [],
    })
    partial_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "F3", "source": m3, "line_span": [1, 1]}],
        "corrections": [],
    })

    def run_case(attempt0_raw: str, *, fail_validate_attempt0: bool = False):
        prompts = []
        replies = iter([attempt0_raw, full_patch])

        def fake_complete(prompt, config, **kwargs):
            prompts.append(prompt)
            return next(replies)

        def fake_validate(theme, ld, names, fls, cfg, body, changes, **kwargs):
            if len(prompts) == 1:
                if fail_validate_attempt0:
                    raise inc.Refused("independent claim audit was not accepted (supported)")
                inc.deterministic_check(ld, body, names, fls, changes)

        monkeypatch.setattr(inc, "local_complete", fake_complete)
        monkeypatch.setattr(inc, "validate_candidate", fake_validate)
        out = inc.append("Glasshouse", loaded, [m1, m2, m3], files, LOCAL)
        assert f"[[{m1}]]" in out
        assert len(prompts) == 2
        retry_payload, _ = json.JSONDecoder().raw_decode(prompts[1])
        return retry_payload["correction_feedback"]

    # (a) malformed JSON -> body is None -> no UnboundLocalError and no missing_citations key/hint
    fb_malformed = run_case("not json")
    assert "missing_citations" not in fb_malformed
    assert "missing_citations" not in fb_malformed["instruction"]

    # (b) malformed patch schema -> _apply_patch fails (body is None) -> no missing_citations key/hint
    fb_bad_patch = run_case(json.dumps({"additions": "not-a-list", "corrections": []}))
    assert "missing_citations" not in fb_bad_patch
    assert "missing_citations" not in fb_bad_patch["instruction"]

    # (c) partial citations -> exact missing_citations list in source order and explicit hint
    fb_partial = run_case(partial_patch)
    assert fb_partial["missing_citations"] == [f"[[{m1}]]", f"[[{m2}]]"]
    assert "missing_citations" in fb_partial["instruction"]

    # (d) all citations present, rejected downstream -> no missing_citations key/hint
    fb_complete = run_case(full_patch, fail_validate_attempt0=True)
    assert "missing_citations" not in fb_complete
    assert "missing_citations" not in fb_complete["instruction"]


@pytest.mark.parametrize("cfg_ctx,expected_cap", [
    (7680, 7680),
    (8192, inc.MAX_AUDIT_CONTEXT),
    (65536, 65536),
])
def test_append_retry_budget_guard_exact_boundary_preserves_attempt0_refusal(
    world, monkeypatch, cfg_ctx, expected_cap,
):
    nd, graph, _ = world
    name = "2099-01-02_100000"
    cfg = {**LOCAL, "llm": {**LOCAL["llm"], "num_ctx": cfg_ctx}}
    monkeypatch.setattr(inc, "source_relevance", lambda *a, **kw: True)

    def _run_with_pad(pad_len: int):
        _new(graph, name, "Glasshouse fact. " + ("a" * pad_len))
        seen = []

        def fake_complete(prompt, config, *, system, num_predict, **kwargs):
            seen.append((
                config["llm"]["num_ctx"],
                inc._stage_needed_tokens(len(inc.GENERATE_SYSTEM), prompt, num_predict),
            ))
            # Valid empty patch, missing required [[name]] -> deterministic_check/source_unattributed
            return json.dumps({"additions": [], "corrections": []})

        monkeypatch.setattr(inc, "local_complete", fake_complete)
        res = nd.run_all([graph], cfg, full=False, dry=False)
        return seen, res.telemetry.snapshot()

    probe_seen, _ = _run_with_pad(100)
    assert len(probe_seen) == 2
    delta_chars = (expected_cap - probe_seen[1][1]) * inc.ADMISSION_CHARS_PER_TOKEN
    exact_fit_pad = 100 + delta_chars

    # Exact fit (needed == cap): retry executes (2 calls)
    seen_fit, snap_fit = _run_with_pad(exact_fit_pad)
    assert len(seen_fit) == 2 and seen_fit[1][1] == expected_cap
    assert snap_fit["stage_refusals"] == {"deterministic_check": {"source_unattributed": 1}}

    # One token over cap (needed == cap + 1): retry skipped BEFORE begin_attempt(), preserving attempt-0 stage/detail
    seen_over, snap_over = _run_with_pad(exact_fit_pad + inc.ADMISSION_CHARS_PER_TOKEN)
    assert len(seen_over) == 1
    assert snap_over["final_reasons"] == {"validation_refused": 1}
    assert snap_over["stage_refusals"] == {"deterministic_check": {"source_unattributed": 1}}


def test_nightly_add_falls_back_sequentially_on_multi_source_unattributed_only(world, monkeypatch):
    nd, graph, old_path = world
    m1, m2 = "2099-01-02_100000", "2099-01-03_100000"
    _new(graph, m1, "Glasshouse update one.")
    _new(graph, m2, "Glasshouse update two.")

    batch_sizes = []
    monkeypatch.setattr(inc, "source_relevance", lambda *a, **kw: True)
    monkeypatch.setattr(inc, "semantic_check", lambda *a, **kw: None)

    def fake_complete(prompt, config, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        names = list(payload["new_sources"])
        batch_sizes.append(len(names))
        if len(names) > 1:
            # Drop one accepted source so real inc.append -> deterministic_check raises source_unattributed
            return json.dumps({
                "additions": [{"section": "Сейчас", "text": f"Fact {names[0]}.", "source": names[0],
                               "line_span": [2, 2]}],
                "corrections": [],
            })
        src = names[0]
        return json.dumps({
            "additions": [{"section": "Сейчас", "text": f"Fact {src}.", "source": src,
                           "line_span": [2, 2]}],
            "corrections": [],
        })

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert sum(g.built for g in result.graphs) == 1
    # 2 batch attempts (both rejected by real deterministic_check) + 2 single-source sequential appends
    assert batch_sizes == [2, 2, 1, 1]
    text = old_path.read_text(encoding="utf-8")
    assert f"[[{m1}]]" in text and f"[[{m2}]]" in text
    assert result.telemetry.snapshot()["stage_refusals"] == {}


# ---------------------------------------------------------------------------
# 7. Round 4 (#990): structured append patch (_normalize_patch_line, _apply_patch)
# ---------------------------------------------------------------------------


def _loaded_with_body(body: str) -> dossier.DossierLoad:
    raw = dossier.render(
        "Glasshouse", body, ["2099-01-01_100000"],
        {"2099-01-01_100000": {"kind": "Встречи", "text": "ok"}}, "fp-old", "2099-01-01",
    )
    raw = dossier.with_composition(raw, ["2099-01-01_100000"])
    return dossier.DossierLoad("ok", raw, raw=raw.encode("utf-8"))


def test_normalize_patch_line_handles_bullets_links_and_rejects_bad_inputs():
    m1 = "2099-01-02_100000"
    assert inc._normalize_patch_line("Fact A.", m1, [m1], bad_source_msg="bad src") == (f"- Fact A. [[{m1}]]", m1)
    assert inc._normalize_patch_line("  - Fact A.  ", f"[[{m1}]]", [m1], bad_source_msg="bad src") == (f"- Fact A. [[{m1}]]", m1)
    assert inc._normalize_patch_line(f"- Fact A. [[{m1}]]", m1, [m1], bad_source_msg="bad src") == (f"- Fact A. [[{m1}]]", m1)
    assert inc._normalize_patch_line(f"Fact A. [[{m1}]]", m1, [m1], bad_source_msg="bad src") == (f"- Fact A. [[{m1}]]", m1)

    for raw_text, raw_src in ((123, m1), ("Fact A.", 123)):
        with pytest.raises(inc.Refused, match="malformed patch entry"):
            inc._normalize_patch_line(raw_text, raw_src, [m1], bad_source_msg="bad src")
    for bad_nl in ("line1\nline2", "line1\rline2", "line1\x0bline2", "line1\x0cline2",
                   "line1\x1cline2", "line1\x85line2", "line1\u2028line2", "line1\u2029line2"):
        with pytest.raises(inc.Refused, match="multiline patch entry"):
            inc._normalize_patch_line(bad_nl, m1, [m1], bad_source_msg="bad src")
    for bad_text in ("", "   ", "-", "---", "—", "- — ", "## Правки автора", "  # Heading"):
        with pytest.raises(inc.Refused, match="empty or invalid patch entry"):
            inc._normalize_patch_line(bad_text, m1, [m1], bad_source_msg="bad src")
    with pytest.raises(inc.Refused, match="bad src"):
        inc._normalize_patch_line("Fact A.", "unknown", [m1], bad_source_msg="bad src")


def test_apply_patch_corrections_preserve_physical_order_and_never_dedup_moved_lines():
    a, b, c_src = "2099-01-01_100000", "2099-01-02_100000", "2099-01-03_100000"
    prior_body = (
        f"## Сейчас\n"
        f"- First line. [[{a}]]\n"
        f"- Second line. [[{a}]]\n\n"
        f"## Как пришли\n"
        f"- First line. [[{a}]]\n"
        f"- Older history line. [[{a}]]\n\n"
        f"## Решено\n—\n\n"
        f"## Открыто\n—\n\n"
        f"## Кто в теме\n- Alice [[{a}]]"
    )
    loaded = _loaded_with_body(prior_body)
    files = {
        b: {"text": "Second replaced.\nFirst replaced.\nDuplicate test.\nBob joined."},
        c_src: {"text": "Extra decision."},
    }
    # Reverse order in corrections + duplicate of correction.after in additions + last-section append
    patch = {
        "additions": [
            {"section": "  ##  Сейчас  ", "text": "  - First now updated.  ", "source": b, "line_span": [3, 3]},
            {"section": "Решено", "text": "Extra decision.", "source": c_src, "line_span": [1, 1]},
            {"section": "Кто в теме", "text": "Bob joined.", "source": b, "line_span": [4, 4]},
        ],
        "corrections": [
            {"before": "Second line.", "after": "Second now updated.", "source": f"[[{b}]]", "line_span": [1, 1]},
            {"before": "First line.", "after": "First now updated.", "source": b, "line_span": [2, 2]},
        ],
    }
    body, changes, anchors = inc._apply_patch(loaded, [b, c_src], files, patch)
    assert len(anchors) == 4
    # Full deterministic_check (including _new_content_positions order + count checks) must pass!
    inc.deterministic_check(loaded, body, [b, c_src], files, changes)
    # Last section Кто в теме must not glue lines and must not end with trailing newline
    assert f"- Alice [[{a}]]\n- Bob joined. [[{b}]]" in body
    assert not body.endswith("\n")
    # Moved lines in ## Как пришли must appear in ascending physical order AND keep the duplicate copy
    history = body.split("## Как пришли\n", 1)[1].split("\n\n## Решено", 1)[0]
    assert history == (
        f"- First line. [[{a}]]\n"
        f"- Older history line. [[{a}]]\n"
        f"- First line. [[{a}]]\n"
        f"- Second line. [[{a}]]"
    )
    # Deduplicated addition matching correction.after must appear only once in ## Сейчас
    current = body.split("## Сейчас\n", 1)[1].split("\n\n## Как пришли", 1)[0]
    assert current == f"- First now updated. [[{b}]]\n- Second now updated. [[{b}]]"
    # Placeholder "—" in ## Решено replaced cleanly, untouched ## Открыто kept verbatim with "—"
    assert "## Решено\n- Extra decision. [[2099-01-03_100000]]\n\n## Открыто\n—\n\n## Кто в теме" in body

    # Two corrections merging into one identical replacement line must deduplicate in ## Сейчас
    # while still moving both superseded lines to ## Как пришли.
    merge_patch = {
        "additions": [],
        "corrections": [
            {"before": "First line.", "after": "Unified state.", "source": b, "line_span": [2, 2]},
            {"before": "Second line.", "after": "Unified state.", "source": b, "line_span": [1, 1]},
        ],
    }
    merged_body, merged_changes, merged_anchors = inc._apply_patch(loaded, [b], files, merge_patch)
    assert len(merged_anchors) == 1
    inc.deterministic_check(loaded, merged_body, [b], files, merged_changes)
    merged_current = merged_body.split("## Сейчас\n", 1)[1].split("\n\n## Как пришли", 1)[0]
    assert merged_current == f"- Unified state. [[{b}]]"
    merged_history = merged_body.split("## Как пришли\n", 1)[1].split("\n\n## Решено", 1)[0]
    assert merged_history.endswith(f"- First line. [[{a}]]\n- Second line. [[{a}]]")


def test_apply_patch_preserves_untouched_sections_and_em_dash_and_rejects_malformed():
    m1 = "2099-01-02_100000"
    files = {m1: {"text": "History note.\nOpen question.\nVent A q."}}
    empty_current_body = (
        "## Сейчас\n—\n"
        "## Как пришли\n— \n"
        "## Решено\n—\n"
        "## Открыто\n—\n"
        "## Кто в теме\n—"
    )
    loaded_empty = _loaded_with_body(empty_current_body)
    # Addition to Открыто must leave untouched ## Сейчас with "—" intact, and clean "— " when Как пришли is edited
    body, changes, anchors = inc._apply_patch(loaded_empty, [m1], files, {
        "additions": [
            {"section": "Как пришли", "text": "History note.", "source": m1, "line_span": [1, 1]},
            {"section": "Открыто", "text": "Open question.", "source": m1, "line_span": [2, 2]},
        ],
        "corrections": [],
    })
    assert changes == []
    assert len(anchors) == 2
    assert body.startswith("## Сейчас\n—\n## Как пришли\n- History note. [[2099-01-02_100000]]\n## Решено\n—\n")
    assert "## Открыто\n- Open question. [[2099-01-02_100000]]\n## Кто в теме\n—" in body

    # Correction targeting placeholder "—" in ## Сейчас must be refused
    with pytest.raises(inc.Refused, match="must identify one current line"):
        inc._apply_patch(loaded_empty, [m1], files, {
            "additions": [],
            "corrections": [{"before": "—", "after": "New state.", "source": m1, "line_span": [1, 1]}],
        })

    loaded = _loaded_dossier()
    bad_cases = [
        ({"additions": "bad", "corrections": []}, "malformed patch entry"),
        ({"additions": [], "corrections": "bad"}, "malformed patch entry"),
        ({"additions": ["bad"], "corrections": []}, "malformed patch entry"),
        ({"additions": [{"section": "Сейчас", "text": "x"}], "corrections": []}, "malformed patch entry"),
        ({"additions": [{"section": "Сейчас", "text": "x", "source": m1}], "corrections": []}, "malformed patch entry"),
        ({"additions": [{"section": 123, "text": "x", "source": m1, "line_span": [1, 1]}], "corrections": []}, "invalid patch section"),
        ({"additions": [{"section": "Правки автора", "text": "x", "source": m1, "line_span": [1, 1]}], "corrections": []}, "invalid patch section"),
        ({"additions": [{"section": "Сейчас", "text": "x", "source": m1, "line_span": 123}], "corrections": []}, "lacks exact new-source evidence"),
        ({"additions": [{"section": "Сейчас", "text": "x", "source": m1, "line_span": []}], "corrections": []}, "lacks exact new-source evidence"),
        ({"additions": [{"section": "Сейчас", "text": "x", "source": "other", "line_span": [1, 1]}], "corrections": []}, "unsupported patch source"),
        ({"additions": [], "corrections": ["bad"]}, "malformed correction ledger"),
        ({"additions": [], "corrections": [{"before": "Vent A"}]}, "malformed correction ledger"),
        ({"additions": [], "corrections": [{"before": 123, "after": "x", "source": m1, "line_span": [1, 1]}]}, "empty correction ledger entry"),
        ({"additions": [], "corrections": [{"before": "   ", "after": "x", "source": m1, "line_span": [1, 1]}]}, "empty correction ledger entry"),
        ({"additions": [], "corrections": [{"before": "Vent A", "after": "x", "source": m1, "line_span": 123}]}, "lacks exact new-source evidence"),
        ({"additions": [], "corrections": [{"before": "Vent A", "after": "x", "source": m1, "line_span": []}]}, "lacks exact new-source evidence"),
        ({"additions": [], "corrections": [{"before": "Vent A", "after": "x", "source": "other", "line_span": [1, 1]}]}, "unsupported or unattributed correction ledger"),
        ({"additions": [], "corrections": [{"before": "Absent claim", "after": "x", "source": m1, "line_span": [1, 1]}]}, "must identify one current line"),
        ({"additions": [], "corrections": [
            {"before": "Vent A", "after": "Closed 1.", "source": m1, "line_span": [1, 1]},
            {"before": "Vent A", "after": "Closed 2.", "source": m1, "line_span": [1, 1]},
        ]}, "must identify one current line"),
    ]
    for bad_patch, pattern in bad_cases:
        with pytest.raises(inc.Refused, match=pattern):
            inc._apply_patch(loaded, [m1], files, bad_patch)

    # All new closed refusal messages map to non-unknown telemetry details
    for msg in (
        "malformed patch entry",
        "multiline patch entry",
        "empty or invalid patch entry",
        "invalid patch section",
        "unsupported patch source",
        "unsupported correction: before must identify one current line in ## Сейчас",
    ):
        assert msg in telemetry._MESSAGE_TO_DETAIL
        assert telemetry._MESSAGE_TO_DETAIL[msg] != telemetry.Detail.UNKNOWN


# ---------------------------------------------------------------------------
# 8. Round 5 (#990): speaker label normalization, Кто в теме guard, AUDIT_PREDICT
# ---------------------------------------------------------------------------


def test_normalize_patch_line_unlabels_speakers_and_validates_after_unlabel():
    from charoite_graph import graph_names

    m1 = "2099-01-02_100000"
    assert inc._normalize_patch_line("Собеседник 3 предложил релиз.", m1, [m1], bad_source_msg="bad") == (
        f"- Участник встречи предложил релиз. [[{m1}]]", m1
    )
    assert inc._normalize_patch_line("Слова Собеседника 2 учтены.", m1, [m1], bad_source_msg="bad") == (
        f"- Слова участника встречи учтены. [[{m1}]]", m1
    )
    assert inc._normalize_patch_line("[[Люди/Спикер 1|Спикер 1]] ответил.", m1, [m1], bad_source_msg="bad") == (
        f"- Участник встречи ответил. [[{m1}]]", m1
    )
    # Alias expanding to a heading or placeholder must still be refused after unlabel_speakers.
    for bad_alias in ("[[Люди/Спикер 1|#Заголовок]]", "[[Люди/Спикер 1|—]]", "[[Люди/Спикер 1|-]]"):
        with pytest.raises(inc.Refused, match="empty or invalid patch entry"):
            inc._normalize_patch_line(bad_alias, m1, [m1], bad_source_msg="bad")
    assert not graph_names.has_speaker_actor(
        inc._normalize_patch_line("Спикер 2 и Участник 1", m1, [m1], bad_source_msg="bad")[0]
    )


def test_apply_patch_unlabels_body_sections_and_omits_anonymous_who_in_topic():
    m1 = "2099-01-02_100000"
    loaded = _loaded_dossier()
    files = {m1: {"text": "Собеседник 1 сообщил: Vent A закрыт.\nСпикер 2 отметил риск.\nАнна Смирнова.\nИван Петров.\nСоучастник встречи Борис."}}
    patch = {
        "additions": [
            {"section": "Открыто", "text": "Спикер 2 отметил риск.", "source": m1, "line_span": [2, 2]},
            {"section": "Кто в теме", "text": "Собеседник 3 назначен ответственным.", "source": m1, "line_span": [99, 99]},
            {"section": "Кто в теме", "text": "[[Люди/Спикер 1]]", "source": m1, "line_span": [99, 99]},
            {"section": "Кто в теме", "text": "участника встречи", "source": m1, "line_span": [99, 99]},
            {"section": "Кто в теме", "text": "участнику встречи", "source": m1, "line_span": [99, 99]},
            {"section": "Кто в теме", "text": "с участником встречи", "source": m1, "line_span": [99, 99]},
            {"section": "Кто в теме", "text": "об участнике встречи", "source": m1, "line_span": [99, 99]},
            {"section": "Кто в теме", "text": "[[Люди/Спикер 1|Анна Смирнова]]", "source": m1, "line_span": [3, 3]},
            {"section": "Кто в теме", "text": "Иван Петров (Спикер 2)", "source": m1, "line_span": [4, 4]},
            {"section": "Кто в теме", "text": "Соучастник встречи Борис", "source": m1, "line_span": [5, 5]},
        ],
        "corrections": [
            {
                "before": "Vent A is open.",
                "after": "Собеседник 1 сообщил: Vent A закрыт.",
                "source": m1,
                "line_span": [1, 1],
            }
        ],
    }
    body, changes, anchors = inc._apply_patch(loaded, [m1], files, patch)
    assert len(anchors) == 5
    inc.deterministic_check(loaded, body, [m1], files, changes)
    assert f"- Участник встречи сообщил: Vent A закрыт. [[{m1}]]" in body
    assert changes[0]["after"] == f"- Участник встречи сообщил: Vent A закрыт. [[{m1}]]"
    assert f"- Участник встречи отметил риск. [[{m1}]]" in body
    who = body.split("## Кто в теме", 1)[1]
    assert not inc._UNLABELED_ACTOR_RE.search(who)
    assert f"- Анна Смирнова [[{m1}]]" in who
    assert f"- Иван Петров (Спикер 2) [[{m1}]]" in who
    assert f"- Соучастник встречи Борис [[{m1}]]" in who

    # Source filename containing the phrase must not drop a real named person in Кто в теме.
    weird_src = "2099-01-02_участник встречи"
    body_weird, _, _ = inc._apply_patch(loaded, [weird_src], {weird_src: {"text": "Анна Смирнова."}}, {
        "additions": [{"section": "Кто в теме", "text": "Анна Смирнова", "source": weird_src, "line_span": [1, 1]}],
        "corrections": [],
    })
    assert f"- Анна Смирнова [[{weird_src}]]" in body_weird.split("## Кто в теме", 1)[1]

    # Validation still runs BEFORE omitting anonymous Кто в теме entries (bad source/multiline refused).
    with pytest.raises(inc.Refused, match="unsupported patch source"):
        inc._apply_patch(loaded, [m1], files, {
            "additions": [{"section": "Кто в теме", "text": "Собеседник 3", "source": "unknown", "line_span": [1, 1]}],
            "corrections": [],
        })
    # When an anonymous Кто в теме entry is the ONLY entry for a source, deterministic_check refuses attribution.
    body_only_anon, changes_only_anon, anchors_only_anon = inc._apply_patch(loaded, [m1], files, {
        "additions": [{"section": "Кто в теме", "text": "Собеседник 3", "source": m1, "line_span": [99, 99]}],
        "corrections": [],
    })
    assert anchors_only_anon == []
    with pytest.raises(inc.Refused, match="does not attribute each accepted source"):
        inc.deterministic_check(loaded, body_only_anon, [m1], files, changes_only_anon)


def test_round5_unlabeled_rule_in_prompts_and_audit_predict(monkeypatch):
    from charoite_graph import graph_names

    assert inc.AUDIT_PREDICT == 4096
    m1 = "2099-01-02_100000"
    loaded = _loaded_with_body(BODY)
    files = {m1: {"text": "Glasshouse vent sealed."}}
    seen_prompts = []
    seen_audit = []

    replies = iter([
        json.dumps({
            "additions": [
                {"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": m1,
                 "line_span": [1, 1]},
                {"section": "Кто в теме", "text": "Собеседник 3", "source": m1, "line_span": [1, 1]},
            ],
            "corrections": [],
        }),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Glasshouse vent sealed.", "source": m1,
                        "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
        json.dumps({"verdict": "accept", "relevant": True, "reason": "ok"}),
    ])

    def fake_complete(prompt, config, *, system, **kwargs):
        seen_prompts.append((system, prompt))
        if system in (inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM):
            seen_audit.append((system, kwargs.get("think"), kwargs.get("num_predict")))
        return next(replies)

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    out = inc.append("Glasshouse", loaded, [m1], files, LOCAL)
    assert "Glasshouse vent sealed." in out
    assert "участник" not in out.split("## Кто в теме", 1)[1].lower()
    assert graph_names.UNLABELED_RULE in seen_prompts[0][1]
    assert "unnamed per-meeting voice" in seen_prompts[1][1]
    assert seen_audit == [
        (inc.SUPPORT_AUDIT_SYSTEM, True, inc.AUDIT_PREDICT),
        (inc.TOPIC_AUDIT_SYSTEM, False, 350),
    ]


def test_round6_main_judge_predict_quote_contract_and_source_telemetry(monkeypatch):
    assert inc.MAIN_JUDGE_PREDICT == 4096
    assert inc.MAIN_JUDGE_PREDICT >= inc.AUDIT_PREDICT

    # Stage context stepping for MAIN_JUDGE_PREDICT matches AUDIT_PREDICT boundaries.
    sys_len = len(inc.MAIN_JUDGE_SYSTEM)
    min_budget = (inc.MIN_AUTO_CONTEXT - inc.MAIN_JUDGE_PREDICT - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN
    mid_budget = (inc.MID_AUTO_CONTEXT - inc.MAIN_JUDGE_PREDICT - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN
    high_budget = (inc.HIGH_AUTO_CONTEXT - inc.MAIN_JUDGE_PREDICT - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN
    max_budget = (inc.MAX_AUDIT_CONTEXT - inc.MAIN_JUDGE_PREDICT - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN
    assert inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (min_budget - sys_len),
                             inc.MAIN_JUDGE_PREDICT)["llm"]["num_ctx"] == inc.MIN_AUTO_CONTEXT
    assert inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (min_budget + 1 - sys_len),
                             inc.MAIN_JUDGE_PREDICT)["llm"]["num_ctx"] == inc.MID_AUTO_CONTEXT
    assert inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (mid_budget + 1 - sys_len),
                             inc.MAIN_JUDGE_PREDICT)["llm"]["num_ctx"] == inc.HIGH_AUTO_CONTEXT
    assert inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (high_budget - sys_len),
                             inc.MAIN_JUDGE_PREDICT)["llm"]["num_ctx"] == inc.HIGH_AUTO_CONTEXT
    assert inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (high_budget + 1 - sys_len),
                             inc.MAIN_JUDGE_PREDICT)["llm"]["num_ctx"] == inc.MAX_AUDIT_CONTEXT
    assert inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (max_budget - sys_len),
                             inc.MAIN_JUDGE_PREDICT)["llm"]["num_ctx"] == inc.MAX_AUDIT_CONTEXT
    with pytest.raises(inc.Refused, match="maximum context budget") as over:
        inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (max_budget + 1 - sys_len),
                          inc.MAIN_JUDGE_PREDICT)
    assert over.value.reason == telemetry.Reason.CONTEXT

    # Prompt preserves multiline quotes while forbidding line joining, and uses MAIN_JUDGE_PREDICT.
    m1 = "2099-01-02_100000"
    loaded = _loaded_with_body(BODY)
    files = {m1: {"text": "- Header\n  - Glasshouse vent sealed."}}
    body = BODY.replace("## Как пришли", f"- Glasshouse vent sealed. [[{m1}]]\n## Как пришли")
    seen_calls = []

    def fake_complete(prompt, config, *, system, **kwargs):
        seen_calls.append((system, prompt, kwargs.get("num_predict")))
        return json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Glasshouse vent sealed.", "source": m1,
                        "line_span": [1, 2],
                        "relevant": True, "supported": True, "reason": "ok"}],
        })

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: None)
    inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
    assert len(seen_calls) == 1
    _, prompt, num_predict = seen_calls[0]
    assert num_predict == inc.MAIN_JUDGE_PREDICT
    assert "line_span=[start_line, end_line]" in prompt
    assert "NEVER stretch a span across separate sections" in prompt

    # Unsupported claim source maps to claim_source_missing, distinct from claim_quote_missing.
    for bad_claim, expected_msg, expected_detail in (
        (
            {"text": "Glasshouse vent sealed.", "source": f"[[Встречи/{m1}]]",
             "quote": "Glasshouse vent sealed.", "relevant": True, "supported": True, "reason": "ok"},
            "names an unsupported new source",
            "claim_source_missing",
        ),
        (
            {"text": "Glasshouse vent sealed.", "source": m1,
             "quote": "Header Glasshouse vent sealed.", "relevant": True, "supported": True, "reason": "ok"},
            "lacks exact new-source evidence",
            "claim_quote_missing",
        ),
    ):
        col = telemetry.Collector()
        with col.activate():
            with col.topic():
                with pytest.raises(inc.Refused, match=expected_msg) as exc_info:
                    with telemetry.stage(telemetry.Stage.SEMANTIC):
                        inc._check_claims(loaded, body, [m1], files, [bad_claim], [])
                telemetry.record_refusal_message(str(exc_info.value))
                telemetry.request_refusal(telemetry.Reason.VALIDATION)
                col.complete_topic(attempted=True, committed=0, refused=1)
        assert col.snapshot()["stage_refusals"] == {"semantic_check": {expected_detail: 1}}


def test_round7_canonical_quote_hits_typography_nfd_wikilinks_and_hidden_symmetry():
    import unicodedata

    # All _QUOTE_CHAR_MAP entries + casefold + horizontal spaces + trailing spaces before newline
    for src_ch, ascii_ch in (
        ("ё", "е"), ("Ё", "е"), ("—", "-"), ("–", "-"), ("−", "-"),
        ("‑", "-"), ("‐", "-"), ("‒", "-"), ("―", "-"),
        ("«", '"'), ("»", '"'), ("“", '"'), ("”", '"'), ("„", '"'),
        ("‘", "'"), ("’", "'"),
    ):
        assert inc._quote_hits(f"Факт {src_ch} проверка", f"факт {ascii_ch} проверка") == 1

    # NBSP, tabs, multiple horizontal spaces, and trailing space before newline
    source = (
        "- **Релиз «Орбита\u00a02.4»** — перенесён на 15–18 февраля.\u00a0 \n"
        "  - Лимит очереди\t 500  сообщений.\n"
    )
    assert inc._quote_hits(source, 'релиз "орбита 2.4" - перенесен на 15-18 февраля.') == 1
    assert inc._quote_hits(
        source,
        "- Релиз «Орбита 2.4» — перенесён на 15–18 февраля.\n - Лимит очереди 500 сообщений.",
    ) == 1
    # Single-line quote carrying trailing NBSP/space still matches multi-line source line whose trailing space was stripped
    assert inc._quote_hits(source, "- Релиз «Орбита 2.4» — перенесён на 15–18 февраля.\u00a0 ") == 1
    # Newlines are NEVER collapsed into spaces (no cross-line splicing)
    assert inc._quote_hits(
        "Собеседник 1: Нет, отменяем.\nСобеседник 2: Запускаем завтра.",
        "Нет, отменяем. Запускаем завтра.",
    ) == 0
    # Whitespace-only quote never matches single space via canonicalization
    assert inc._quote_hits("a b", "  ") == 0
    assert inc._quote_hits("a  b", "  ") == 1

    # NFD vs NFC: normal matching AND NFC before _strip_inline_emphasis (word boundary \w on NFD й)
    nfd_source = unicodedata.normalize("NFD", "- **Расчёт задержек**: модуль «Маяк» переведён на 03:00.")
    assert inc._quote_hits(nfd_source, "Расчёт задержек: модуль «Маяк» переведён на 03:00.") == 1
    assert inc._quote_hits("пои\u0306*ти*", "пои\u0306ти") == 0
    assert inc._quote_hits("пои\u0306*ти*", "пойти") == 0

    # Non-canonical emphasis-free branch keeps exact-case disambiguation before canonical folding
    assert inc._quote_hits("- **Альфа** готова.\n- альфа готова.", "Альфа готова.") == 1

    # Wikilinks with [[ in quote: canonicalize alias + NFD target (with й) while keeping exact target
    nfd_link_src = unicodedata.normalize("NFD", "- [[Ядра/Устойчивый маяк|Проект\u00a0—\u00a0Маяк]]: готово.")
    assert inc._quote_hits(nfd_link_src, "- [[Ядра/Устойчивый маяк|Проект - Маяк]]: готово.") == 1
    assert inc._quote_hits(nfd_link_src, "- [[Чужой/Устойчивый маяк|Проект - Маяк]]: готово.") == 0
    assert inc._quote_hits("- [[Ядра/Орбита|орбита]]: ok.", "- [[Ядра/Орбита]]: ok.") == 1
    assert inc._quote_hits("- [[Ядра/Орбита| ]]: ok.", "- [[Ядра/Орбита| ]]: ok.") == 1
    assert inc._quote_hits("- [[Ядра/Орбита#sec|Орбита]]: ok.", "- [[Ядра/Орбита#sec]]: ok.") == 0

    # Sentinel characters \ufdd0/\ufdd1/\ufdd2 cannot forge hidden/link matches
    for bad_q in ("s\ufdd2e\ufdd2c\ufdd2r\ufdd2e\ufdd2t", "a\ufdd0b", "a\ufdd1b"):
        assert inc._quote_hits("Код `secret` ок. a\ufdd0b a\ufdd1b", bad_q) == 0
    assert inc._quote_hits("x \ufdd0:0\ufdd1 y [[Ядра/А]]", "x [[Ядра/А]] y") == 0
    assert inc._quote_hits("x [[Ядра/А|\ufdd0:0\ufdd1]] y [[Ядра/Б]]", "x [[Ядра/Б]] y") == 0

    # Hidden-zone symmetry & duplicate detection (including single literal inline-code quote != duplicate!)
    assert inc._quote_hits("Запуск `pytest -q` прошёл штатно.", "`pytest -q`") == 1
    assert inc._quote_hits("Код `[[Ядра/Glasshouse]]` и текст.", "`[[Ядра/Glasshouse]]`") == 1
    assert inc._quote_hits("Запуск `pytest -q` и ещё `PYTEST -Q`.", "`pytest -q`") == 2
    assert inc._quote_hits("Код `[[Ядра/Glasshouse]]` и [[Ядра/Glasshouse|glasshouse]].", "[[Ядра/Glasshouse]]") == 2
    assert inc._quote_hits("Glasshouse\u00a0fitted.\n```\nGlasshouse fitted.\n```\n", "Glasshouse fitted.") == 2
    assert inc._quote_hits("Glasshouse fitted.\n```\nGlasshouse\u00a0fitted.\n```\n", "Glasshouse fitted.") == 2
    assert inc._quote_hits("Glasshouse\u00a0fitted.\n```\nGlasshouse\u00a0fitted.\n```\n", "glasshouse fitted.") == 2
    assert inc._quote_hits("Расче\u0308т готов.\n```\nРасче\u0308т готов.\n```\n", "расчет готов.") == 2
    assert inc._quote_hits("Код `Орбита` и текст Орбита.", "орбита") == 2
    assert inc._quote_hits("Переход на `API v2` намечен на 1\u00a0марта.", "переход на `API v2` намечен на 1 марта.") == 1
    assert inc._quote_hits("Переход на `API v2` намечен на 1\u00a0марта.", "переход на `api v2` намечен на 1 марта.") == 0
    assert inc._quote_hits("Код `[[Ядра/Glasshouse]]` и текст.", "[[Ядра/Glasshouse]]") == 1
    assert inc._quote_hits('- Релиз «Орбита».\n- Релиз "Орбита".', 'релиз "орбита".') == 2


def test_round7_semantic_check_quote_feedback_retry_and_invariants(monkeypatch):
    assert inc.QUOTE_FEEDBACK_LIMIT == 5
    assert inc.QUOTE_FEEDBACK_CHARS == 200

    m1 = "2099-01-02_100000"
    m2 = "2099-01-03_100000"
    loaded = _loaded_with_body(BODY)
    fact1 = "Valve pressure stabilized at 4 bar."
    fact2 = "Backup sensor calibration completed."
    files = {
        m1: {"text": f"Inspection log: {fact1}"},
        m2: {"text": f"Calibration log: {fact2}"},
    }
    body = BODY.replace("## Как пришли", f"- {fact1} [[{m1}]]\n## Как пришли")

    # 1) Pure claim_quote_missing triggers exactly 1 bounded retry with span_feedback and succeeds
    seen_prompts: list[str] = []
    audited: list[dict] = []
    replies = iter([
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": fact1, "source": f"[[{m1}]]",
                        "line_span": [5, 2], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "fixed span",
            "claims": [{"text": fact1, "source": m1,
                        "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
        }),
    ])

    def fake_complete(prompt, config, *, system, **kwargs):
        seen_prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: audited.append(a[2]))
    inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
    assert len(seen_prompts) == 2
    payload_retry = json.loads(seen_prompts[1].split("\n", 1)[0])
    assert "correction_feedback" not in payload_retry
    fb = payload_retry["span_feedback"]
    assert fb["failure"] == "your previous judge output had invalid or ambiguous claim line_span(s)"
    assert fb["invalid_spans"] == [{"text": fact1, "source": m1, "problem": "span_inverted"}]
    assert "supported=false and verdict=reject" in fb["instruction"]
    assert len(audited) == 1 and audited[0]["quote"] == f"Inspection log: {fact1}"

    # 2) Bounded retry: 2 consecutive misses stop after 2 calls and record claim_quote_missing
    calls_twice: list[str] = []
    bad_verdict = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": fact1, "source": m1,
                    "line_span": [99, 99], "relevant": True, "supported": True, "reason": "ok"}],
    })
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (calls_twice.append(p), bad_verdict)[1])
    col = telemetry.Collector()
    with col.activate():
        with col.topic():
            with pytest.raises(inc.Refused, match="lacks exact new-source evidence") as exc_info:
                with telemetry.stage(telemetry.Stage.SEMANTIC):
                    inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
            telemetry.record_refusal_message(str(exc_info.value))
            telemetry.request_refusal(telemetry.Reason.VALIDATION)
            col.complete_topic(attempted=True, committed=0, refused=1)
    assert len(calls_twice) == 2
    assert col.snapshot()["stage_refusals"] == {"semantic_check": {"claim_quote_missing": 1}}

    # 3) Truncation limits: at most QUOTE_FEEDBACK_LIMIT items and QUOTE_FEEDBACK_CHARS per text
    long_facts = [f"Fact{i} " + ("x" * 230) + "." for i in range(6)]
    long_body = BODY.replace("## Как пришли", "".join(f"- {f} [[{m1}]]\n" for f in long_facts) + "## Как пришли")
    long_files = {m1: {"text": "completely different text"}}
    long_prompts: list[str] = []
    long_verdict = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [
            {"text": f, "source": m1, "line_span": [99, 99],
             "relevant": True, "supported": True, "reason": "ok"}
            for i, f in enumerate(long_facts)
        ],
    })
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (long_prompts.append(p), long_verdict)[1])
    with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
        inc.semantic_check(
            "Glasshouse", loaded, [m1], long_files,
            {"llm": {**LOCAL["llm"], "num_ctx": inc.MID_AUTO_CONTEXT}}, long_body, [],
        )
    assert len(long_prompts) == 2
    fb_long = json.loads(long_prompts[1].split("\n", 1)[0])["span_feedback"]
    assert len(fb_long["invalid_spans"]) == inc.QUOTE_FEEDBACK_LIMIT
    assert all(len(item["text"]) == inc.QUOTE_FEEDBACK_CHARS for item in fb_long["invalid_spans"])
    assert all(item["problem"] == "span_out_of_range" for item in fb_long["invalid_spans"])

    # 4) Retry turning into verdict=reject refuses immediately as semantic conservation rejected
    reject_replies = iter([
        bad_verdict,
        json.dumps({
            "verdict": "reject", "conserved": True, "supported": False, "corrections_valid": True,
            "reason": "unsupported", "claims": [],
        }),
    ])
    monkeypatch.setattr(inc, "local_complete", lambda *a, **kw: next(reject_replies))
    with pytest.raises(inc.Refused, match="semantic conservation was not accepted"):
        inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])

    # 5) Critical invariant: invalid span on claim[0] NEVER triggers retry when ANY structural/semantic check fails
    two_body = BODY.replace("## Как пришли", f"- {fact1} [[{m1}]]\n- {fact2} [[{m2}]]\n## Как пришли")
    missing_c1 = {"text": fact1, "source": m1, "line_span": [99, 99], "relevant": True, "supported": True, "reason": "ok"}
    for bad_claims, err_re, use_names, use_files, use_body in (
        ([missing_c1, {"text": "broken"}], "malformed candidate claim", [m1, m2], files, two_body),
        ([missing_c1, {"text": fact2, "source": m2, "line_span": [], "relevant": True, "supported": False, "reason": "no"}],
         "not relevant and supported", [m1, m2], files, two_body),
        ([missing_c1, {"text": fact2, "source": "unknown", "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
         "unsupported new source", [m1, m2], files, two_body),
        ([missing_c1, missing_c1], "duplicate candidate claim", [m1], files, body),
        ([missing_c1, {"text": "not in body", "source": m2, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
         "not an exact candidate span", [m1, m2], files, two_body),
    ):
        one_shot: list[int] = []
        v = json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok", "claims": bad_claims,
        })
        monkeypatch.setattr(inc, "local_complete", lambda *a, v=v, **kw: (one_shot.append(1), v)[1])
        with pytest.raises(inc.Refused, match=err_re):
            inc.semantic_check("Glasshouse", loaded, use_names, use_files, LOCAL, use_body, [])
        assert len(one_shot) == 1

    # 6) Budget guard on retry: exact == ctx allows retry, ctx + 1 refuses without 2nd call; non-ollama doesn't KeyError
    sys_len = len(inc.MAIN_JUDGE_SYSTEM)
    min_budget = (inc.MIN_AUTO_CONTEXT - inc.MAIN_JUDGE_PREDICT - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN
    base_calls: list[str] = []
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (base_calls.append(p), bad_verdict)[1])
    with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
        inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
    assert len(base_calls) == 2
    exact_pad = min_budget - sys_len - len(base_calls[1])
    assert exact_pad >= 0
    exact_files = {m1: {"text": f"Inspection log: {fact1}" + ("z" * exact_pad)}}
    exact_calls: list[str] = []
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (exact_calls.append(p), bad_verdict)[1])
    with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
        inc.semantic_check("Glasshouse", loaded, [m1], exact_files, LOCAL, body, [])
    assert len(exact_calls) == 2
    assert inc._stage_needed_tokens(sys_len, exact_calls[1], inc.MAIN_JUDGE_PREDICT) == inc.MIN_AUTO_CONTEXT

    tight_files = {m1: {"text": f"Inspection log: {fact1}" + ("z" * (exact_pad + 1))}}
    tight_calls: list[int] = []
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (tight_calls.append(len(p)), bad_verdict)[1])
    with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
        inc.semantic_check("Glasshouse", loaded, [m1], tight_files, LOCAL, body, [])
    assert len(tight_calls) == 1

    # Non-ollama config (no num_ctx) retries without KeyError
    non_ollama_calls: list[int] = []
    monkeypatch.setattr(inc, "local_complete", lambda *a, **kw: (non_ollama_calls.append(1), bad_verdict)[1])
    with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
        inc.semantic_check("Glasshouse", loaded, [m1], files, {"llm": {"engine": "cloud"}}, body, [])
    assert len(non_ollama_calls) == 2


def test_round8_semantic_detail_classification_and_quote_retry_override(monkeypatch):
    # 1) Pure table classifier: structural validity -> quote_retried -> conserved -> corrections_valid -> uncertain -> supported -> fallback
    base = {"verdict": "reject", "conserved": True, "corrections_valid": True, "supported": True, "reason": "r"}
    assert telemetry.classify_semantic_detail({**base, "reason": ""}, quote_retried=True) is telemetry.Detail.SEMANTIC_REJECTED
    assert telemetry.classify_semantic_detail({**base, "reason": "   "}, quote_retried=True) is telemetry.Detail.SEMANTIC_REJECTED
    assert telemetry.classify_semantic_detail({**base, "reason": None}, quote_retried=True) is telemetry.Detail.SEMANTIC_REJECTED
    assert telemetry.classify_semantic_detail({**base, "supported": 0}, quote_retried=True) is telemetry.Detail.SEMANTIC_REJECTED
    assert telemetry.classify_semantic_detail({**base, "conserved": None}, quote_retried=False) is telemetry.Detail.SEMANTIC_REJECTED
    assert telemetry.classify_semantic_detail({**base, "corrections_valid": "false"}, quote_retried=False) is telemetry.Detail.SEMANTIC_REJECTED
    assert telemetry.classify_semantic_detail({**base, "supported": False}, quote_retried=True) is telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED
    assert telemetry.classify_semantic_detail({**base, "conserved": False, "corrections_valid": False, "supported": False}) is telemetry.Detail.SEMANTIC_CONSERVED_FALSE
    assert telemetry.classify_semantic_detail({**base, "corrections_valid": False, "verdict": "uncertain", "supported": False}) is telemetry.Detail.SEMANTIC_CORRECTIONS_INVALID
    assert telemetry.classify_semantic_detail({**base, "verdict": "uncertain", "supported": False}) is telemetry.Detail.SEMANTIC_UNCERTAIN
    assert telemetry.classify_semantic_detail({**base, "supported": False}) is telemetry.Detail.SEMANTIC_SUPPORTED_FALSE
    assert telemetry.classify_semantic_detail(base) is telemetry.Detail.SEMANTIC_REJECTED
    assert telemetry.detail_for_message("candidate semantic conservation was not accepted") is telemetry.Detail.SEMANTIC_REJECTED
    assert telemetry.detail_for_message("unknown freeform text") is None

    # 2) semantic_check attempt=0 records exact Detail for verdict flags and claim flags
    m1 = "2099-01-02_100000"
    loaded = _loaded_with_body(BODY)
    fact = "Valve pressure stabilized at 4 bar."
    files = {m1: {"text": f"Inspection log: {fact}"}}
    body = BODY.replace("## Как пришли", f"- {fact} [[{m1}]]\n## Как пришли")
    claim_ok = {"text": fact, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}

    for reply_obj, expected_detail in (
        ({**base, "conserved": False, "claims": []}, telemetry.Detail.SEMANTIC_CONSERVED_FALSE),
        ({**base, "corrections_valid": False, "claims": []}, telemetry.Detail.SEMANTIC_CORRECTIONS_INVALID),
        ({**base, "verdict": "uncertain", "supported": False, "claims": []}, telemetry.Detail.SEMANTIC_UNCERTAIN),
        ({**base, "supported": False, "claims": []}, telemetry.Detail.SEMANTIC_SUPPORTED_FALSE),
        ({**base, "claims": []}, telemetry.Detail.SEMANTIC_REJECTED),
        ({**base, "verdict": "accept", "claims": [{**claim_ok, "relevant": False}]}, telemetry.Detail.CLAIM_NOT_RELEVANT),
        ({**base, "verdict": "accept", "claims": [{**claim_ok, "supported": False}]}, telemetry.Detail.CLAIM_NOT_SUPPORTED),
    ):
        monkeypatch.setattr(inc, "local_complete", lambda *a, r=json.dumps(reply_obj), **kw: r)
        col = telemetry.Collector()
        with col.activate():
            with col.topic():
                with pytest.raises(inc.Refused) as exc_info:
                    with telemetry.stage(telemetry.Stage.SEMANTIC):
                        inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
                assert exc_info.value.detail is expected_detail
                telemetry.record_refusal_message(str(exc_info.value))
                telemetry.request_refusal(telemetry.Reason.VALIDATION)
                col.complete_topic(attempted=True, committed=0, refused=1)
        assert col.snapshot()["stage_refusals"] == {"semantic_check": {expected_detail.value: 1}}

    # 3) span_feedback retry (attempt=1) overrides verdict reject AND claim relevant/supported reject to SEMANTIC_QUOTE_RETRY_REJECTED,
    # while non-semantic claim failures (uncovered / ambiguous / quote_missing) keep their own Detail.
    miss_reply = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok", "claims": [{**claim_ok, "line_span": [99, 99]}],
    })
    for retry_reply_obj, expected_detail, expected_exc_detail in (
        ({**base, "supported": False, "claims": []}, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED),
        ({**base, "verdict": "accept", "claims": [{**claim_ok, "relevant": False}]}, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED),
        ({**base, "verdict": "accept", "claims": [{**claim_ok, "supported": False}]}, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED),
        ({**base, "verdict": "accept", "claims": []}, telemetry.Detail.CLAIM_UNCOVERED, None),
        ({**base, "verdict": "accept", "claims": [{**claim_ok, "line_span": [99, 99]}]}, telemetry.Detail.CLAIM_QUOTE_MISSING, telemetry.Detail.CLAIM_QUOTE_MISSING),
    ):
        replies = iter([miss_reply, json.dumps(retry_reply_obj)])
        monkeypatch.setattr(inc, "local_complete", lambda *a, **kw: next(replies))
        col = telemetry.Collector()
        with col.activate():
            with col.topic():
                with pytest.raises(inc.Refused) as exc_info:
                    with telemetry.stage(telemetry.Stage.SEMANTIC):
                        inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
                assert exc_info.value.detail is expected_exc_detail
                telemetry.record_refusal_message(str(exc_info.value))
                telemetry.request_refusal(telemetry.Reason.VALIDATION)
                col.complete_topic(attempted=True, committed=0, refused=1)
        assert col.snapshot()["stage_refusals"] == {"semantic_check": {expected_detail.value: 1}}


def test_round8_rejected_claims_filtering_and_append_structured_feedback(monkeypatch):
    m1 = "2099-01-02_100000"
    loaded = _loaded_with_body(BODY)
    fact_a = "Valve pressure stabilized at 4 bar."
    fact_b = "Backup pump may run after Friday if load stays high."
    files = {m1: {"text": f"{fact_a} {fact_b}"}}
    body = BODY.replace("## Как пришли", f"- {fact_a} [[{m1}]]\n- {fact_b} [[{m1}]]\n## Как пришли")

    # 1) _check_claims filters rejected_claims strictly to new spans overlapping required, deduplicated, resilient to non-dict/bad text
    claims_mixed = [
        {"text": fact_a, "source": m1, "quote": fact_a, "relevant": False, "supported": True, "reason": "offtopic"},
        {"text": fact_b, "source": m1, "quote": fact_b, "relevant": True, "supported": False, "reason": "unsup"},
        {"text": fact_a, "source": m1, "quote": fact_a, "relevant": False, "supported": False, "reason": "dup"},
        {"text": "Vent A is open.", "source": m1, "quote": fact_a, "relevant": False, "supported": True, "reason": "old line"},
        {"text": ".", "source": m1, "quote": fact_a, "relevant": True, "supported": False, "reason": "punct only"},
        {"text": "   ", "source": m1, "quote": fact_a, "relevant": False, "supported": True, "reason": "spaces"},
        {"text": 123, "source": m1, "quote": fact_a, "relevant": False, "supported": True, "reason": "non-str"},
        {"text": fact_b, "source": m1, "quote": fact_b, "relevant": True, "supported": True, "reason": "good"},
        "not-a-dict",
    ]
    with pytest.raises(inc.Refused, match="not relevant and supported") as exc_claims:
        inc._check_claims(loaded, body, [m1], files, claims_mixed, [])
    assert exc_claims.value.detail is telemetry.Detail.CLAIM_NOT_RELEVANT
    assert exc_claims.value.rejected_claims == (fact_a, fact_b)

    # Also verify valid claim is NOT collected when only second claim fails supported=False
    claims_sup_only = [
        {"text": fact_a, "source": m1, "quote": fact_a, "relevant": True, "supported": True, "reason": "good"},
        {"text": fact_b, "source": m1, "quote": fact_b, "relevant": True, "supported": False, "reason": "unsup"},
    ]
    with pytest.raises(inc.Refused, match="not relevant and supported") as exc_sup:
        inc._check_claims(loaded, body, [m1], files, claims_sup_only, [])
    assert exc_sup.value.detail is telemetry.Detail.CLAIM_NOT_SUPPORTED
    assert exc_sup.value.rejected_claims == (fact_b,)

    # 2) audit_claim attaches closed Detail + rejected_claims=(claim["text"],) for both support and topic stages
    claim = {"text": fact_b, "source": m1, "quote": fact_b}
    for replies, expected_detail in (
        ([json.dumps({"verdict": "reject", "supported": False, "reason": "SECRET_REASON_1"})],
         telemetry.Detail.AUDIT_SUPPORT_REJECTED),
        ([json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
          json.dumps({"verdict": "reject", "relevant": False, "reason": "SECRET_REASON_2"})],
         telemetry.Detail.AUDIT_TOPIC_REJECTED),
    ):
        monkeypatch.setattr(inc, "local_complete", _seq_replies(replies))
        col = telemetry.Collector()
        with col.activate():
            with col.topic():
                with pytest.raises(inc.Refused) as exc_audit:
                    inc.audit_claim("Glasshouse", loaded, claim, LOCAL, quote_context=files[m1]["text"])
                assert exc_audit.value.detail is expected_detail
                assert exc_audit.value.rejected_claims == (fact_b,)
                telemetry.request_refusal(telemetry.Reason.VALIDATION)
                col.complete_topic(attempted=True, committed=0, refused=1)
        expected_stage = "audit_support" if expected_detail is telemetry.Detail.AUDIT_SUPPORT_REJECTED else "audit_topic"
        assert col.snapshot()["stage_refusals"] == {expected_stage: {expected_detail.value: 1}}

    # 3) Standalone append (NO Collector active!) receives Detail hint + filtered rejected_claims without leaking judge reason
    orig_validate = inc.validate_candidate
    patch_bad = json.dumps({"additions": [{"section": "Сейчас", "text": fact_b, "source": m1, "line_span": [1, 1]}], "corrections": []})
    patch_good = json.dumps({"additions": [{"section": "Сейчас", "text": fact_a, "source": m1, "line_span": [1, 1]}], "corrections": []})
    prompts: list[str] = []
    call_idx = {"n": 0}

    def fake_append_complete(prompt, c, *, system, **kwargs):
        prompts.append(prompt)
        call_idx["n"] += 1
        return patch_bad if call_idx["n"] == 1 else patch_good

    val_idx = {"n": 0}

    def fake_validate(theme, ld, names, fl, c, cand_body, changes, **kwargs):
        val_idx["n"] += 1
        if val_idx["n"] == 1:
            raise inc.Refused(
                "independent claim audit was not accepted (supported)",
                detail=telemetry.Detail.AUDIT_SUPPORT_REJECTED,
                rejected_claims=("", 123, "NOT_IN_BODY", fact_b, fact_b),
            )

    monkeypatch.setattr(inc, "local_complete", fake_append_complete)
    monkeypatch.setattr(inc, "validate_candidate", fake_validate)
    assert telemetry.current() is None
    res = inc.append("Glasshouse", loaded, [m1], files, LOCAL)
    assert fact_a in res
    assert len(prompts) == 2
    assert "Preserve source certainty, conditions, uncertainty, and time" in prompts[0]
    fb_payload = json.loads(prompts[1].split("\n", 1)[0])["correction_feedback"]
    assert fb_payload["rejected_claims"] == [fact_b]
    assert inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.AUDIT_SUPPORT_REJECTED] in fb_payload["instruction"]

    # 4) Fallback via detail_for_message when external Refused has detail=None + truncation/limit on rejected_claims
    long_items = [f"Item{i} " + ("z" * 230) for i in range(6)]
    patch_long = json.dumps({
        "additions": [{"section": "Сейчас", "text": t, "source": m1, "line_span": [1, 1]} for t in long_items],
        "corrections": [],
    })
    prompts_fb: list[str] = []
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (prompts_fb.append(p), patch_long)[1])
    monkeypatch.setattr(
        inc,
        "validate_candidate",
        lambda *a, **kw: (_ for _ in ()).throw(
            inc.Refused("candidate semantic conservation was not accepted", rejected_claims=tuple(long_items))
        ),
    )
    with pytest.raises(inc.Refused, match="semantic conservation was not accepted"):
        inc.append("Glasshouse", loaded, [m1], files, LOCAL)
    assert len(prompts_fb) == 2
    fb2 = json.loads(prompts_fb[1].split("\n", 1)[0])["correction_feedback"]
    assert len(fb2["rejected_claims"]) == inc.QUOTE_FEEDBACK_LIMIT
    assert all(len(t) == inc.QUOTE_FEEDBACK_CHARS for t in fb2["rejected_claims"])
    assert inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.SEMANTIC_REJECTED] in fb2["instruction"]

    # 5) body is None (malformed JSON) does not crash on rejected_claims and appends no trailing space when no hint
    prompts_json: list[str] = []
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (prompts_json.append(p), "{bad json")[1])
    with pytest.raises(inc.Refused, match="malformed model JSON"):
        inc.append("Glasshouse", loaded, [m1], files, LOCAL)
    fb3 = json.loads(prompts_json[1].split("\n", 1)[0])["correction_feedback"]
    assert "rejected_claims" not in fb3
    assert not fb3["instruction"].startswith(" ")
    assert fb3["instruction"].endswith("and do not duplicate after in additions.")

    # 6) Judge freeform reason NEVER leaks into append correction_feedback
    monkeypatch.setattr(inc, "validate_candidate", orig_validate)
    secret_prompts: list[str] = []
    seq = iter([
        patch_bad,
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "SECRET_MAIN_REASON",
            "claims": [{"text": fact_b, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({"verdict": "reject", "supported": False, "reason": "SECRET_AUDIT_REASON"}),
        patch_good,
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": fact_a, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
        json.dumps({"verdict": "accept", "relevant": True, "reason": "ok"}),
    ])

    def leak_check_complete(prompt, c, *, system, **kwargs):
        if system == inc.GENERATE_SYSTEM:
            secret_prompts.append(prompt)
        return next(seq)

    monkeypatch.setattr(inc, "local_complete", leak_check_complete)
    out_body = inc.append("Glasshouse", loaded, [m1], files, LOCAL)
    assert fact_a in out_body
    assert len(secret_prompts) == 2
    assert "SECRET_MAIN_REASON" not in secret_prompts[1]
    assert "SECRET_AUDIT_REASON" not in secret_prompts[1]
    assert fact_b in secret_prompts[1]


def test_round9_quote_hits_extended_markdown_and_invariants():
    # 1) U+2026 ellipsis <-> ASCII "..." without allowing fabricated ellipsis
    assert inc._quote_hits("Задержка 40 мс… но пики есть.", "Задержка 40 мс... но пики есть.") == 1
    assert inc._quote_hits("Задержка 40 мс... но пики есть.", "Задержка 40 мс… но пики есть.") == 1
    assert inc._quote_hits("Задержка 40 мс, но пики есть.", "Задержка ... пики есть.") == 0

    # 2) ***bold italic*** and ~~strikethrough~~ outside hidden blocks only
    assert inc._quote_hits("- ***Критично***: схема active-passive.", "Критично: схема active-passive.") == 1
    assert inc._quote_hits("- Убрали ~~ручной перезапуск~~ из регламента.", "Убрали ручной перезапуск из регламента.") == 1
    assert inc._quote_hits("Код `***bold***` и `~~strike~~`.", "Код `bold` и `strike`.") == 0
    assert inc._quote_hits("Текст *** пробел *** тут.", "Текст  пробел  тут.") == 0
    assert inc._quote_hits("Текст ~~ пробел ~~ тут.", "Текст  пробел  тут.") == 0

    # 3) Markdown links [text](url): stripped before */** when quote has no "](",
    #    never allows URL spoofing when "](" is present, and preserves image syntax "![alt](url)".
    assert inc._quote_hits(
        "- Выложили в [спецификации v2](https://example.com/spec) сегодня.",
        "Выложили в спецификации v2 сегодня.",
    ) == 1
    assert inc._quote_hits("[a](https://x/1**2) и **b**.", "a и b.") == 1
    assert inc._quote_hits("[Спека](https://right.example)", "[Спека](https://wrong.example)") == 0
    assert inc._quote_hits("Фото ![alt](img.png) готово.", "Фото alt готово.") == 0
    assert inc._quote_hits("Код `[a](https://x)` ок.", "Код `a` ок.") == 0

    # 4) Blank lines (including whitespace-only blank lines), indentation, line prefixes, table pipes
    assert inc._quote_hits("Реплика 1.\n\nРеплика 2.", "Реплика 1.\nРеплика 2.") == 1
    assert inc._quote_hits("Реплика 1.\n \t \nРеплика 2.", "Реплика 1.\nРеплика 2.") == 1
    assert inc._quote_hits("Реплика 1.\nРеплика 2.", "Реплика 1.\n\nРеплика 2.") == 1
    assert inc._quote_hits("- Задачи:\n  - шаг 1\n  - шаг 2", "Задачи:\nшаг 1\nшаг 2") == 1
    assert inc._quote_hits("- Header\n  - Alpha", "  - Alpha") == 1
    assert inc._quote_hits("### Риски\n> Если сдвинется.\n> Шлюз работает.", "Риски\nЕсли сдвинется.\nШлюз работает.") == 1
    assert inc._quote_hits("| **Шлюз** | Готов | Петров |", "|Шлюз|Готов|Петров|") == 1
    assert inc._quote_hits("| **Шлюз** | Готов | Петров |", "Шлюз | Готов | Петров") == 1
    assert inc._quote_hits("| A | B |", "- | A | B |") == 1
    assert inc._quote_hits("Строка текста.", "  Строка текста.  ") == 1

    # 5) Negative invariants: no line-joining, no prefix-only match, no negative numbers, hidden-zone dup symmetry
    assert inc._quote_hits("Собеседник 1: Нет.\nСобеседник 2: Да.", "Нет. Да.") == 0
    assert inc._quote_hits("Температура\n-5 градусов.", "Температура\n5 градусов.") == 0
    assert inc._quote_hits("Просто текст.", "- ") == 0
    assert inc._quote_hits("Просто текст.", "### ") == 0
    assert inc._quote_hits("X\n```\n- \n```", "- ") == 1
    assert inc._quote_hits("Код `|a|b|` и текст | a | b |.", "|a|b|") == 2
    assert inc._quote_hits("Блок:\n```\n- Альфа\n- Бета\n```\n- Альфа\n- Бета", "Альфа\nБета") == 2


def test_round9_verdict_rejected_claims_and_open_item_contract(monkeypatch):
    assert inc.HIGH_AUTO_CONTEXT == 32_768
    assert inc.MAX_AUDIT_CONTEXT == 65_536

    m1 = "2099-01-02_100000"
    loaded = _loaded_with_body(BODY)
    fact_a = "Valve pressure stabilized at 4 bar."
    fact_b = "Backup pump may run after Friday."
    files = {m1: {"text": f"{fact_a} {fact_b}"}}
    body = BODY.replace("## Как пришли", f"- {fact_a} [[{m1}]]\n- {fact_b} [[{m1}]]\n## Как пришли")
    base_rej = {
        "verdict": "reject", "conserved": True, "supported": False,
        "corrections_valid": True, "reason": "SECRET_REJECT_REASON",
    }

    # 1) Non-list claims (null / string) on top-level reject must raise Refused (never TypeError) with empty rejected_claims
    for bad_claims in (None, "not-a-list", 42):
        reply = json.dumps({**base_rej, "claims": bad_claims})
        monkeypatch.setattr(inc, "local_complete", lambda *a, r=reply, **kw: r)
        with pytest.raises(inc.Refused, match="semantic conservation was not accepted") as exc_bad:
            inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
        assert exc_bad.value.detail is telemetry.Detail.SEMANTIC_SUPPORTED_FALSE
        assert exc_bad.value.rejected_claims == ()

    # 2) Standalone semantic_check on body failing _new_content_positions keeps original Refused message and ()
    broken_body = body.replace("- Vent A is open. [[2099-01-01_100000]]", "")
    reply_with_claim = json.dumps({
        **base_rej,
        "claims": [{"text": fact_a, "source": m1, "quote": fact_a, "relevant": True, "supported": False, "reason": "no"}],
    })
    monkeypatch.setattr(inc, "local_complete", lambda *a, **kw: reply_with_claim)
    with pytest.raises(inc.Refused, match="semantic conservation was not accepted") as exc_broken:
        inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, broken_body, [])
    assert exc_broken.value.rejected_claims == ()

    # 3) Top-level reject on attempt=0 extracts only rejected new spans (ignores valid and old lines)
    reply_mixed = json.dumps({
        **base_rej,
        "claims": [
            {"text": fact_a, "source": m1, "quote": fact_a, "relevant": True, "supported": True, "reason": "ok"},
            {"text": fact_b, "source": m1, "quote": fact_b, "relevant": True, "supported": False, "reason": "no"},
            {"text": "Vent A is open.", "source": m1, "quote": fact_a, "relevant": False, "supported": False, "reason": "old"},
        ],
    })
    monkeypatch.setattr(inc, "local_complete", lambda *a, **kw: reply_mixed)
    with pytest.raises(inc.Refused, match="semantic conservation was not accepted") as exc_mix:
        inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
    assert exc_mix.value.rejected_claims == (fact_b,)

    # 4) span_feedback retry (attempt=1) fallback to last_missing ONLY for genuine support/relevance reject
    miss_reply = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [
            {"text": fact_a, "source": m1, "line_span": [99, 99], "relevant": True, "supported": True, "reason": "ok"},
            {"text": fact_b, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"},
        ],
    })
    for retry_obj, exp_detail, exp_rejected in (
        ({**base_rej, "claims": []}, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED, (fact_a,)),
        (
            {**base_rej, "claims": [{"text": fact_b, "source": m1, "line_span": [1, 1], "relevant": True, "supported": False, "reason": "no"}]},
            telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED,
            (fact_b,),
        ),
        ({**base_rej, "conserved": False, "supported": True, "claims": []}, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED, ()),
        ({**base_rej, "corrections_valid": False, "supported": True, "claims": []}, telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED, ()),
        ({**base_rej, "reason": "", "claims": []}, telemetry.Detail.SEMANTIC_REJECTED, ()),
    ):
        seq_it = iter([miss_reply, json.dumps(retry_obj)])
        monkeypatch.setattr(inc, "local_complete", lambda *a, **kw: next(seq_it))
        with pytest.raises(inc.Refused, match="semantic conservation was not accepted") as exc_r:
            inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
        assert exc_r.value.detail is exp_detail
        assert exc_r.value.rejected_claims == exp_rejected

    # 5) End-to-end append retry receives last_missing as rejected_claims without leaking judge reason,
    #    and MAIN_JUDGE prompt aligns ## Открыто -> ## Решено contract while protecting ⚠ warnings.
    patch_bad = json.dumps({
        "additions": [
            {"section": "Сейчас", "text": fact_a, "source": m1, "line_span": [1, 1]},
            {"section": "Сейчас", "text": fact_b, "source": m1, "line_span": [1, 1]},
        ],
        "corrections": [],
    })
    patch_good = json.dumps({
        "additions": [{"section": "Сейчас", "text": fact_b, "source": m1, "line_span": [1, 1]}],
        "corrections": [],
    })
    append_prompts: list[str] = []
    judge_prompts: list[str] = []
    e2e_seq = iter([
        patch_bad,
        miss_reply,
        json.dumps({**base_rej, "claims": []}),
        patch_good,
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": fact_b, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
        json.dumps({"verdict": "accept", "relevant": True, "reason": "ok"}),
    ])

    def e2e_complete(prompt, c, *, system, **kwargs):
        if system == inc.GENERATE_SYSTEM:
            append_prompts.append(prompt)
        elif system == inc.MAIN_JUDGE_SYSTEM:
            judge_prompts.append(prompt)
        return next(e2e_seq)

    monkeypatch.setattr(inc, "local_complete", e2e_complete)
    out_body = inc.append("Glasshouse", loaded, [m1], files, LOCAL)
    assert fact_b in out_body and fact_a not in out_body
    assert len(append_prompts) == 2
    fb_append = json.loads(append_prompts[1].split("\n", 1)[0])["correction_feedback"]
    assert fb_append["rejected_claims"] == [fact_a]
    assert inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.SEMANTIC_QUOTE_RETRY_REJECTED] in fb_append["instruction"]
    assert "SECRET_REJECT_REASON" not in append_prompts[1]
    assert "non-⚠ ## Открыто item" in judge_prompts[0]
    assert "never silently resolve or contradict a ⚠ warning" in judge_prompts[0]


def test_round10_quote_hits_mixed_links_prefixes_speaker_nl_and_invariants():
    # 1) Mixed wikilinks + plain text in one quote, canonical alias case/NFD, and path protection
    mixed_src = "- [[Люди/Иванов|Иванов]] передал [[Ядра/Орбита|Орбита]] и [[Ядра/Йод|Йод]] в [[Системы/Прод|Прод]]."
    assert inc._quote_hits(mixed_src, "Иванов передал [[Ядра/Орбита|Орбита]] и Йод в Прод.") == 1
    assert inc._quote_hits(mixed_src, "Иванов передал [[Ядра/Орбита]] и Йод в Прод.") == 1
    assert inc._quote_hits(mixed_src, "иванов передал [[Ядра/Орбита|орбита]] и йод в прод.") == 1
    assert inc._quote_hits(
        "- [[Люди/Иванов|Иванов]] передал [[Ядра/Йод|И\u0306од]] в [[Системы/Прод|Прод]].",
        "иванов передал [[Ядра/Йод|йод]] в прод.",
    ) == 1
    # Wrong path in kept wikilink must NEVER match
    assert inc._quote_hits(
        "- [[Люди/Иванов|Иванов]] передал [[Чужой/Орбита|Орбита]] в Прод.",
        "Иванов передал [[Ядра/Орбита]] в Прод.",
    ) == 0
    # Non-link hidden comment %%A|B%% in quote must NOT poison keep_links
    assert inc._quote_hits(
        "%%Ядра/Орбита|Орбита%% и [[Ядра/Орбита]]",
        "%%Ядра/Орбита|Орбита%% и Орбита",
    ) == 1
    # Duplicate asymmetry: plain + wikilink copies of a mixed quote must report ambiguous (2)
    assert inc._quote_hits(
        "[[Люди/Иванов|Иванов]] с [[Ядра/Орион|Орион]]. Иванов с [[Ядра/Орион|Орион]].",
        "Иванов с [[Ядра/Орион|Орион]].",
    ) == 2
    # Tier 2 keep_raw canonical=False must preserve non-trivial case alias for emphasis+plain duplicates
    assert inc._quote_hits(
        "**Иванов** с [[Ядра/Орион|орион]]. Иванов с [[Ядра/Орион|орион]].",
        "Иванов с [[Ядра/Орион|орион]]",
    ) == 2
    # Hidden-block duplicate for a mixed quote matched on tier 2 (including lowercase alias for keep_canon canonical=True)
    assert inc._quote_hits(
        "Код `Иванов с [[Ядра/Орион]]` и [[Люди/Иванов|Иванов]] с [[Ядра/Орион|Орион]].",
        "Иванов с [[Ядра/Орион]]",
    ) == 2
    assert inc._quote_hits(
        "Код `Иванов с [[Ядра/Орион|орион]]` и [[Люди/Иванов|Иванов]] с [[Ядра/Орион|Орион]].",
        "Иванов с [[Ядра/Орион|орион]]",
    ) == 2

    # 2) Numbered lists and 📌 are decorative; checkboxes [ ]/[x], ✅, ⚠ preserve semantic polarity
    assert inc._quote_hits("1. Первый шаг.\n2. Второй шаг.", "Первый шаг.\nВторой шаг.") == 1
    assert inc._quote_hits("1) Первый шаг.\n2) Второй шаг.", "Первый шаг.\nВторой шаг.") == 1
    assert inc._quote_hits("- 📌 Решение один.\n- 📌 Решение два.", "Решение один.\nРешение два.") == 1
    assert inc._quote_hits("- [ ] Переключить шлюз.", "- [x] Переключить шлюз.") == 0
    assert inc._quote_hits("- ✅ Иванов\n- ✅ Петров", "Иванов\nПетров") == 0
    assert inc._quote_hits("- ⚠ Риск один.\n- ⚠ Риск два.", "Риск один.\nРиск два.") == 0
    assert inc._quote_hits("План:\n2026. Год запуска.", "План:\nГод запуска.") == 0
    assert inc._quote_hits("План:\n1.5 мс задержка.", "План:\n5 мс задержка.") == 0
    for ponly in ("1. ", "2) ", "- 📌 "):
        assert inc._quote_hits("Просто текст.", ponly) == 0

    # 3) Speaker-header + newline in Charoite transcripts (with negative invariants & prefix order)
    assert inc._quote_hits(
        "**Соколов** [00:03:15]:\nКоллеги, мы проверили миграцию.",
        "Соколов [00:03:15]: Коллеги, мы проверили миграцию.",
    ) == 1
    assert inc._quote_hits(
        "**Соколов** [00:03:15 – 00:03:40]:\nКоллеги, мы проверили миграцию.",
        "Соколов [00:03:15 - 00:03:40]: Коллеги, мы проверили миграцию.",
    ) == 1
    assert inc._quote_hits(
        "**Иван** [12:34]:\n- Шлюз готов.",
        "Иван [12:34]: Шлюз готов.",
    ) == 1
    assert inc._quote_hits(
        "**Соколов** [00:03:15]:\nСбой в [12:00] устранили.",
        "Соколов [00:03:15]: Сбой в [12:00] устранили.",
    ) == 1
    assert inc._quote_hits(
        "Собеседник 1: Сбой в [12:00]:\nСобеседник 2: Да.",
        "Сбой в [12:00]: Собеседник 2: Да.",
    ) == 0
    assert inc._quote_hits(
        "**Спикер 1** [00:01]:\n**Спикер 2** [00:02]:\nДа.",
        "Спикер 1 [00:01]: Спикер 2 [00:02]: Да.",
    ) == 0
    assert inc._quote_hits(
        "**Соколов** [00:03:15]:\nНет.\n**Морозов** [00:03:20]:\nДа.",
        "Нет. Да.",
    ) == 0

    # 4) Arrow → <-> ->, invisible characters, and empty canonical quote guard
    assert inc._quote_hits("- [[Люди/Иванов|Иванов]] → [[Ядра/Орион|Орион]]: курирует", "Иванов -> Орион: курирует") == 1
    assert inc._quote_hits("Код `A -> B` и текст A → B.", "A -> B") == 2
    assert inc._quote_hits("Текст с\u200bневидимым\ufeffи\u00adдефисом.", "Текст сневидимымидефисом.") == 1
    assert inc._quote_hits("Просто текст.", "\u200b\ufeff\u00ad") == 0


def test_round10_append_predict_judge_json_skip_can_split_and_missing_quote_feedback(world, monkeypatch):
    assert inc.APPEND_PREDICT == 4096
    assert dossier.GEN_PREDICT == 1800

    nd, graph, old_path = world
    m1, m2 = "2099-01-02_100000", "2099-01-03_100000"
    _new(graph, m1, "Glasshouse update one.")
    _new(graph, m2, "Glasshouse update two.")

    # 1) When validate_candidate raises "malformed model JSON" (body is not None),
    #    append() must NOT waste a second generator retry, and nightly_dossier splits multi-source batch.
    monkeypatch.setattr(inc, "source_relevance", lambda *a, **kw: True)
    gen_batches: list[list[str]] = []
    judge_batches: list[list[str]] = []

    def fake_complete(prompt, config, *, system, num_predict, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        if system == inc.GENERATE_SYSTEM:
            assert num_predict == inc.APPEND_PREDICT
            names = list(payload["new_sources"])
            gen_batches.append(names)
            return json.dumps({
                "additions": [{
                    "section": "Сейчас", "text": f"Fact {n}.", "source": n,
                    "line_span": [2, 2],
                } for n in names],
                "corrections": [],
            })
        if system == inc.MAIN_JUDGE_SYSTEM:
            names = list(payload["new_sources"])
            judge_batches.append(names)
            if len(names) > 1:
                return '{"verdict": "accept", "claims": [{"text": "truncated'
            src = names[0]
            return json.dumps({
                "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
                "reason": "ok",
                "claims": [{
                    "text": f"- Fact {src}. [[{src}]]", "source": src,
                    "line_span": [2, 2],
                    "relevant": True, "supported": True, "reason": "ok",
                }],
            })
        raise AssertionError(f"unexpected system: {system}")

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: None)
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert sum(g.built for g in result.graphs) == 1
    assert gen_batches == [[m1, m2], [m1], [m2]]
    assert judge_batches == [[m1, m2], [m1], [m2]]
    assert result.telemetry.snapshot()["stage_refusals"] == {}
    final_text = old_path.read_text(encoding="utf-8")
    assert f"[[{m1}]]" in final_text and f"[[{m2}]]" in final_text

    # 1b) Multi-source malformed JSON from append itself (body is None) MUST still retry append once
    bad_gen_calls: list[int] = []
    loaded_multi = _loaded_with_body(BODY)
    multi_files = {m1: {"text": "Glasshouse one."}, m2: {"text": "Glasshouse two."}}
    monkeypatch.setattr(inc, "local_complete", lambda *a, **kw: (bad_gen_calls.append(1), "{bad json")[1])
    with pytest.raises(inc.Refused, match="malformed model JSON"):
        inc.append("Glasshouse", loaded_multi, [m1, m2], multi_files, LOCAL)
    assert len(bad_gen_calls) == 2

    # 2) Double _MissingClaimQuotes in semantic_check propagates rejected_claims + CLAIM_QUOTE_MISSING
    #    at the start of append retry feedback["instruction"] (and prompts carry new contracts).
    loaded = _loaded_with_body(BODY)
    fact_a = "Valve pressure stabilized at 4 bar."
    fact_b = "Backup pump may run after Friday."
    files = {m1: {"text": f"{fact_a} {fact_b}"}}
    patch_bad = json.dumps({
        "additions": [
            {"section": "Сейчас", "text": fact_a, "source": m1, "line_span": [1, 1]},
            {"section": "Сейчас", "text": fact_b, "source": m1, "line_span": [1, 1]},
        ],
        "corrections": [],
    })
    patch_good = json.dumps({
        "additions": [{"section": "Сейчас", "text": fact_b, "source": m1, "line_span": [1, 1]}],
        "corrections": [],
    })
    miss_reply = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [
            {"text": fact_a, "source": m1, "line_span": [99, 99], "relevant": True, "supported": True, "reason": "ok"},
            {"text": fact_b, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"},
        ],
    })
    good_judge = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": fact_b, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
    })
    seq = iter([patch_bad, miss_reply, miss_reply, patch_good, good_judge])
    append_prompts: list[str] = []
    judge_prompts: list[str] = []

    def seq_complete(prompt, config, *, system, **kwargs):
        if system == inc.GENERATE_SYSTEM:
            append_prompts.append(prompt)
        elif system == inc.MAIN_JUDGE_SYSTEM:
            judge_prompts.append(prompt)
        return next(seq)

    monkeypatch.setattr(inc, "local_complete", seq_complete)
    out_body = inc.append("Glasshouse", loaded, [m1], files, LOCAL)
    assert fact_b in out_body and fact_a not in out_body
    assert len(append_prompts) == 2
    fb = json.loads(append_prompts[1].split("\n", 1)[0])["correction_feedback"]
    assert "missing_quotes" not in fb
    assert fb["rejected_claims"] == [fact_a]
    assert fb["instruction"].startswith(inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.CLAIM_QUOTE_MISSING] + " Return ONLY valid patch JSON")
    assert "explicit source-stated role or connection to the dossier theme" in append_prompts[0]
    assert "distinct non-identical exact text spans" in judge_prompts[0]
    assert "explicitly covers EVERY clause and event in that item's text" in judge_prompts[0]


def test_round11_anchor_quotes_dedup_feedback_and_semantic_split(world, monkeypatch):
    m1, m2 = "2099-01-02_100000", "2099-01-03_100000"
    loaded = _loaded_with_body(BODY)

    # 1) _apply_patch addition line_span validation: invalid/out-of-range, ambiguous (2), unique (1), existing kept skipped
    files_q = {m1: {"text": "Alpha unique quote.\nRepeat phrase.\nRepeat phrase."}}
    with pytest.raises(inc._MissingClaimQuotes) as exc_miss:
        inc._apply_patch(loaded, [m1], files_q, {
            "additions": [
                {"section": "Сейчас", "text": "Bad quote fact.", "source": m1, "line_span": [9, 9]},
                {"section": "Открыто", "text": "Ambiguous fact.", "source": m1, "line_span": [2, 2]},
            ],
            "corrections": [],
        })
    assert exc_miss.value.detail is telemetry.Detail.CLAIM_QUOTE_MISSING
    assert exc_miss.value.rejected_claims == (f"- Bad quote fact. [[{m1}]]",)
    assert exc_miss.value.missing_quotes == [
        {"text": f"- Bad quote fact. [[{m1}]]", "source": m1, "problem": "span_out_of_range"}
    ]

    with pytest.raises(inc.Refused, match="ambiguous claim evidence") as exc_amb:
        inc._apply_patch(loaded, [m1], files_q, {
            "additions": [
                {"section": "Сейчас", "text": "Ambiguous fact.", "source": m1, "line_span": [2, 2]},
                {"section": "Открыто", "text": "Ambiguous fact.", "source": m1, "line_span": [2, 2]},
            ],
            "corrections": [],
        })
    assert exc_amb.value.detail is telemetry.Detail.CLAIM_QUOTE_AMBIGUOUS
    assert exc_amb.value.rejected_claims == (f"- Ambiguous fact. [[{m1}]]",)

    # Duplicate exact line already in kept must be skipped BEFORE _quote_hits and NOT added to anchors
    body_dup_old, ch_dup_old, anc_dup_old = inc._apply_patch(loaded, [m1], files_q, {
        "additions": [
            {"section": "Сейчас", "text": "Alpha unique fact.", "source": m1, "line_span": [1, 1]},
            {"section": "Сейчас", "text": "Alpha unique fact.", "source": m1, "line_span": [99, 99]},
        ],
        "corrections": [],
    })
    assert len(anc_dup_old) == 1
    assert anc_dup_old[0] == {
        "text": f"- Alpha unique fact. [[{m1}]]", "source": m1, "line_span": [1, 1],
    }
    assert ch_dup_old == [] and "Alpha unique fact." in body_dup_old

    # 2) Corrections also enforce hits == 1 and valid line_span in _apply_patch (hits == 2 -> ambiguous, invalid -> missing, hits == 1 -> anchor).
    with pytest.raises(inc.Refused, match="ambiguous claim evidence") as exc_c2:
        inc._apply_patch(loaded, [m1], files_q, {
            "additions": [],
            "corrections": [{"before": "Vent A is open.", "after": "Vent A closed.", "source": m1, "line_span": [2, 2]}],
        })
    assert exc_c2.value.detail is telemetry.Detail.CLAIM_QUOTE_AMBIGUOUS

    with pytest.raises(inc._MissingClaimQuotes) as exc_c0:
        inc._apply_patch(loaded, [m1], files_q, {
            "additions": [],
            "corrections": [{"before": "Vent A is open.", "after": "Vent A closed.", "source": m1, "line_span": [9, 9]}],
        })
    assert exc_c0.value.missing_quotes == [
        {"text": f"- Vent A closed. [[{m1}]]", "source": m1, "problem": "span_out_of_range"}
    ]

    body_c1, ch_c1, anc_c1 = inc._apply_patch(loaded, [m1], files_q, {
        "additions": [],
        "corrections": [{"before": "Vent A is open.", "after": "Vent A closed.", "source": m1, "line_span": [1, 1]}],
    })
    assert anc_c1 == [{"text": f"- Vent A closed. [[{m1}]]", "source": m1, "line_span": [1, 1]}]
    assert ch_c1[0]["quote"] == "Alpha unique quote."
    inc.deterministic_check(loaded, body_c1, [m1], files_q, ch_c1)

    # 3) Safe deduplication of addition against correction.after in ## Сейчас (wikilink target & source protection)
    dedup_files = {
        m1: {"text": "Q1 unique.\nQ2 unique.\nQ3 unique."},
        m2: {"text": "M2 unique."},
    }
    # (a) Same source, identical plain text, addition links subset of after links -> safely dropped
    body_da, _, anc_da = inc._apply_patch(loaded, [m1], dedup_files, {
        "additions": [
            {"section": "Сейчас", "text": "Пилот Дельта перенесён на 25 мая.", "source": m1, "line_span": [99, 99]},
        ],
        "corrections": [{
            "before": "Vent A is open.",
            "after": "Пилот [[Ядра/Дельта|Дельта]] перенесён на 25 мая.",
            "source": m1,
            "line_span": [1, 1],
        }],
    })
    cur_da = body_da.split("## Сейчас\n", 1)[1].split("\n## Как пришли", 1)[0]
    assert cur_da == f"- Пилот [[Ядра/Дельта|Дельта]] перенесён на 25 мая. [[{m1}]]"
    assert len(anc_da) == 1

    # (b) Same plain alias but DIFFERENT wikilink target -> MUST NOT drop
    body_db, _, anc_db = inc._apply_patch(loaded, [m1], dedup_files, {
        "additions": [
            {"section": "Сейчас", "text": "Срок [[Ядра/Бета|Проект]] — 5 дек.", "source": m1, "line_span": [2, 2]},
        ],
        "corrections": [{
            "before": "Vent A is open.",
            "after": "Срок [[Ядра/Альфа|Проект]] — 5 дек.",
            "source": m1,
            "line_span": [1, 1],
        }],
    })
    cur_db = body_db.split("## Сейчас\n", 1)[1].split("\n## Как пришли", 1)[0]
    assert "[[Ядра/Альфа|Проект]]" in cur_db and "[[Ядра/Бета|Проект]]" in cur_db
    assert len(anc_db) == 2

    # (c) after lacks wikilink while addition carries one -> MUST NOT drop
    body_dc, _, anc_dc = inc._apply_patch(loaded, [m1], dedup_files, {
        "additions": [
            {"section": "Сейчас", "text": "Срок [[Ядра/Альфа|Альфа]] — 5 дек.", "source": m1, "line_span": [2, 2]},
        ],
        "corrections": [{
            "before": "Vent A is open.",
            "after": "Срок Альфа — 5 дек.",
            "source": m1,
            "line_span": [1, 1],
        }],
    })
    cur_dc = body_dc.split("## Сейчас\n", 1)[1].split("\n## Как пришли", 1)[0]
    assert cur_dc.count("5 дек.") == 2 and len(anc_dc) == 2

    # (d) Different source with identical plain text -> MUST NOT drop
    body_dd, _, anc_dd = inc._apply_patch(loaded, [m1, m2], dedup_files, {
        "additions": [
            {"section": "Сейчас", "text": "Пилот Дельта перенесён на 25 мая.", "source": m2, "line_span": [1, 1]},
        ],
        "corrections": [{
            "before": "Vent A is open.",
            "after": "Пилот [[Ядра/Дельта|Дельта]] перенесён на 25 мая.",
            "source": m1,
            "line_span": [1, 1],
        }],
    })
    cur_dd = body_dd.split("## Сейчас\n", 1)[1].split("\n## Как пришли", 1)[0]
    assert f"[[{m1}]]" in cur_dd and f"[[{m2}]]" in cur_dd and len(anc_dd) == 2

    # (e) Same source, DIFFERENT text in ## Сейчас alongside a correction -> MUST NOT drop (kills plain_line==r_plain -> True)
    body_de, _, anc_de = inc._apply_patch(loaded, [m1], dedup_files, {
        "additions": [
            {"section": "Сейчас", "text": "Отдельный новый факт.", "source": m1, "line_span": [2, 2]},
        ],
        "corrections": [{
            "before": "Vent A is open.",
            "after": "Пилот [[Ядра/Дельта|Дельта]] перенесён на 25 мая.",
            "source": m1,
            "line_span": [1, 1],
        }],
    })
    cur_de = body_de.split("## Сейчас\n", 1)[1].split("\n## Как пришли", 1)[0]
    assert "Отдельный новый факт." in cur_de and "перенесён на 25 мая." in cur_de and len(anc_de) == 2

    # (f) Mixed-case non-stem alias shared between after and addition -> kills canonical=False -> True on either side
    body_df, _, anc_df = inc._apply_patch(loaded, [m1], dedup_files, {
        "additions": [
            {"section": "Сейчас", "text": "Пилот Дельта по [[Ядра/Альфа|Проект]] — 5 дек.", "source": m1, "line_span": [99, 99]},
        ],
        "corrections": [{
            "before": "Vent A is open.",
            "after": "Пилот [[Ядра/Дельта|Дельта]] по [[Ядра/Альфа|Проект]] — 5 дек.",
            "source": m1,
            "line_span": [1, 1],
        }],
    })
    cur_df = body_df.split("## Сейчас\n", 1)[1].split("\n## Как пришли", 1)[0]
    assert cur_df == f"- Пилот [[Ядра/Дельта|Дельта]] по [[Ядра/Альфа|Проект]] — 5 дек. [[{m1}]]"
    assert len(anc_df) == 1

    # (g) Equal canonical link sets with different raw syntax ([[X|X]] vs [[X]]) -> kills line_links <= r_links -> <
    body_dg, _, anc_dg = inc._apply_patch(loaded, [m1], dedup_files, {
        "additions": [
            {"section": "Сейчас", "text": "Пилот [[Ядра/Дельта]] перенесён на 25 мая.", "source": m1, "line_span": [99, 99]},
        ],
        "corrections": [{
            "before": "Vent A is open.",
            "after": "Пилот [[Ядра/Дельта|Дельта]] перенесён на 25 мая.",
            "source": m1,
            "line_span": [1, 1],
        }],
    })
    cur_dg = body_dg.split("## Сейчас\n", 1)[1].split("\n## Как пришли", 1)[0]
    assert cur_dg == f"- Пилот [[Ядра/Дельта|Дельта]] перенесён на 25 мая. [[{m1}]]"
    assert len(anc_dg) == 1

    # 4) append retry on _apply_patch missing_quotes: skips MAIN_JUDGE on attempt 0,
    #    uses _PATCH_QUOTE_MISSING_HINT + missing_quotes + rejected_claims, passes compact candidate_anchors to MAIN_JUDGE only.
    fact_ok = "Pressure sensor calibrated at 4 bar."
    files_e2e = {m1: {"text": f"{fact_ok}\nRepeat tag.\nRepeat tag."}}
    patch_miss = json.dumps({
        "additions": [
            {"section": "Сейчас", "text": f"LongClaim{i} " + ("y" * 230), "source": m1, "line_span": [99, 99]}
            for i in range(6)
        ],
        "corrections": [],
    })
    patch_ok = json.dumps({
        "additions": [{"section": "Сейчас", "text": fact_ok, "source": m1, "line_span": [1, 1]}],
        "corrections": [],
    })
    judge_ok = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": fact_ok, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
    })
    seen_calls: list[tuple[str, str]] = []
    seq = iter([
        patch_miss,
        patch_ok,
        judge_ok,
        json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
        json.dumps({"verdict": "accept", "relevant": True, "reason": "ok"}),
    ])

    def rec_complete(prompt, cfg, *, system, **kw):
        seen_calls.append((system, prompt))
        return next(seq)

    monkeypatch.setattr(inc, "local_complete", rec_complete)
    out = inc.append("Glasshouse", loaded, [m1], files_e2e, LOCAL)
    assert fact_ok in out
    assert [s for s, _ in seen_calls] == [
        inc.GENERATE_SYSTEM, inc.GENERATE_SYSTEM, inc.MAIN_JUDGE_SYSTEM,
        inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM,
    ]
    assert "existing ⚠ warnings are preserved automatically" in seen_calls[0][1]
    assert "do NOT duplicate them" in seen_calls[0][1]
    assert "never add a contradicting ## Сейчас line via additions" in seen_calls[0][1]
    assert "line_span" in seen_calls[0][1]
    fb_miss = json.loads(seen_calls[1][1].split("\n", 1)[0])["correction_feedback"]
    assert fb_miss["instruction"].startswith(inc._PATCH_QUOTE_MISSING_HINT + " Return ONLY valid patch JSON")
    assert len(fb_miss["rejected_claims"]) == inc.QUOTE_FEEDBACK_LIMIT
    assert all(len(t) == inc.QUOTE_FEEDBACK_CHARS for t in fb_miss["rejected_claims"])
    assert len(fb_miss["missing_quotes"]) == inc.QUOTE_FEEDBACK_LIMIT
    assert all(len(item["text"]) == inc.QUOTE_FEEDBACK_CHARS for item in fb_miss["missing_quotes"])
    assert all(item["problem"] == "span_out_of_range" for item in fb_miss["missing_quotes"])
    judge_payload = json.loads(seen_calls[2][1].split("\n", 1)[0])
    assert judge_payload["candidate_anchors"] == [
        {"text": f"- {fact_ok} [[{m1}]]", "source": m1, "line_span": [1, 1]}
    ]
    assert "candidate_anchors are UNTRUSTED" in seen_calls[2][1]
    assert "candidate_anchors" not in seen_calls[3][1]
    assert "candidate_anchors" not in seen_calls[4][1]

    # Standalone semantic_check without anchors must NOT include candidate_anchors or anchor_note
    plain_judge_prompts: list[str] = []
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (plain_judge_prompts.append(p), judge_ok)[1])
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: None)
    inc.semantic_check("Glasshouse", loaded, [m1], files_e2e, LOCAL, out, [])
    assert "candidate_anchors" not in plain_judge_prompts[0]

    # 5) _apply_patch CLAIM_QUOTE_AMBIGUOUS retry feedback + telemetry recording on double refusal
    patch_amb = json.dumps({
        "additions": [{"section": "Сейчас", "text": fact_ok, "source": m1, "line_span": [2, 2]}],
        "corrections": [],
    })
    amb_prompts: list[str] = []
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (amb_prompts.append(p), patch_amb)[1])
    col_amb = telemetry.Collector()
    with col_amb.activate():
        with col_amb.topic():
            with pytest.raises(inc.Refused, match="ambiguous claim evidence") as exc_a2:
                inc.append("Glasshouse", loaded, [m1], files_e2e, LOCAL)
            if exc_a2.value.detail is not None:
                telemetry.detail(exc_a2.value.detail)
            telemetry.record_refusal_message(str(exc_a2.value))
            telemetry.request_refusal(telemetry.Reason.VALIDATION)
            col_amb.complete_topic(attempted=True, committed=0, refused=1)
    assert len(amb_prompts) == 2
    fb_amb = json.loads(amb_prompts[1].split("\n", 1)[0])["correction_feedback"]
    assert "missing_quotes" not in fb_amb
    assert fb_amb["rejected_claims"] == [f"- {fact_ok} [[{m1}]]"]
    assert fb_amb["instruction"].startswith(inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.CLAIM_QUOTE_AMBIGUOUS] + " Return ONLY valid patch JSON")
    assert col_amb.snapshot()["stage_refusals"] == {"append": {"claim_quote_ambiguous": 1}}

    # 6) nightly_dossier can_split on multi-source semantic_conserved_false -> _append_sequential succeeds cleanly
    nd, graph, old_path = world
    _new(graph, m1, "Glasshouse update alpha.")
    _new(graph, m2, "Glasshouse update beta.")
    monkeypatch.setattr(inc, "source_relevance", lambda *a, **kw: True)
    split_batches: list[list[str]] = []

    def split_complete(prompt, cfg, *, system, **kw):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        names = list(payload["new_sources"])
        if system == inc.GENERATE_SYSTEM:
            split_batches.append(names)
            return json.dumps({
                "additions": [{
                    "section": "Сейчас", "text": f"Update {n}.", "source": n,
                    "line_span": [2, 2],
                } for n in names],
                "corrections": [],
            })
        if system == inc.MAIN_JUDGE_SYSTEM:
            if len(names) > 1:
                return json.dumps({
                    "verdict": "reject", "conserved": False, "supported": True,
                    "corrections_valid": True, "reason": "batch conflict", "claims": [],
                })
            src = names[0]
            return json.dumps({
                "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
                "reason": "ok",
                "claims": [{
                    "text": f"- Update {src}. [[{src}]]", "source": src,
                    "line_span": [2, 2],
                    "relevant": True, "supported": True, "reason": "ok",
                }],
            })
        raise AssertionError(f"unexpected system: {system}")

    monkeypatch.setattr(inc, "local_complete", split_complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: None)
    res_split = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert sum(g.built for g in res_split.graphs) == 1
    assert split_batches == [[m1, m2], [m1, m2], [m1], [m2]]
    assert res_split.telemetry.snapshot()["stage_refusals"] == {}
    final_txt = old_path.read_text(encoding="utf-8")
    assert f"[[{m1}]]" in final_txt and f"[[{m2}]]" in final_txt
    assert telemetry.Detail.CLAIM_SOURCE_MISSING in night_cli._SPLITTABLE_DETAILS
    assert telemetry.Detail.JSON_SCHEMA in night_cli._SPLITTABLE_DETAILS


def test_round12_predict_ceilings_schema_feedback_and_quote_contracts(world, monkeypatch):
    m1, m2 = "2099-01-02_100000", "2099-01-03_100000"
    loaded = _loaded_with_body(BODY)
    fact1 = "Valve pressure stabilized at 4 bar."
    files1 = {m1: {"text": fact1}}
    valid_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": fact1, "source": m1, "line_span": [1, 1]}],
        "corrections": [],
    })
    valid_judge = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": fact1, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
    })

    # 1) Predict ceilings, adaptive MAIN_JUDGE_LARGE_PREDICT threshold, and prompt contracts
    assert inc.APPEND_PREDICT == 4096
    assert inc.MAIN_JUDGE_PREDICT == 4096
    assert inc.MAIN_JUDGE_LARGE_PREDICT == 6144
    assert inc.MAIN_JUDGE_LARGE_THRESHOLD == 8
    # Boundary check for adaptive judge_predict: <= 8 uses MAIN_JUDGE_PREDICT (4096), == 9 uses MAIN_JUDGE_LARGE_PREDICT (6144)
    for count, expected_pred in ((inc.MAIN_JUDGE_LARGE_THRESHOLD, inc.MAIN_JUDGE_PREDICT),
                                 (inc.MAIN_JUDGE_LARGE_THRESHOLD + 1, inc.MAIN_JUDGE_LARGE_PREDICT)):
        lines = [f"Claim {i} verified." for i in range(count)]
        many_src = "\n".join(lines)
        many_body = BODY.replace("## Как пришли", "\n".join(f"- {ln} [[{m1}]]" for ln in lines) + "\n## Как пришли")
        seen_pred: list[int] = []
        many_verdict = json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": ln, "source": m1, "line_span": [idx, idx], "relevant": True, "supported": True, "reason": "ok"}
                       for idx, ln in enumerate(lines, 1)],
        })
        with monkeypatch.context() as mp:
            mp.setattr(inc, "local_complete", lambda p, cfg, *, num_predict, **kw: (seen_pred.append(num_predict), many_verdict)[1])
            mp.setattr(inc, "audit_claim", lambda *a, **kw: None)
            inc.semantic_check("Glasshouse", loaded, [m1], {m1: {"text": many_src}}, LOCAL, many_body, [])
        assert seen_pred == [expected_pred]
    # Also verify anchors-driven escalation boundary (8 -> 4096, 9 -> 6144) and _stage_config/_stage_needed_tokens use judge_predict
    small_body = BODY.replace("## Как пришли", f"- {fact1} [[{m1}]]\n## Как пришли")
    for anc_count, expected_pred in ((inc.MAIN_JUDGE_LARGE_THRESHOLD, inc.MAIN_JUDGE_PREDICT),
                                     (inc.MAIN_JUDGE_LARGE_THRESHOLD + 1, inc.MAIN_JUDGE_LARGE_PREDICT)):
        seen_anc: list[tuple[int, int]] = []
        anc_list = [{"text": fact1, "source": m1, "line_span": [1, 1]} for _ in range(anc_count)]
        with monkeypatch.context() as mp:
            mp.setattr(inc, "local_complete", lambda p, cfg, *, num_predict, **kw: (seen_anc.append((cfg["llm"]["num_ctx"], num_predict)), valid_judge)[1])
            mp.setattr(inc, "audit_claim", lambda *a, **kw: None)
            inc.semantic_check("Glasshouse", loaded, [m1], files1, LOCAL, small_body, [], anchors=anc_list)
        assert seen_anc[0][1] == expected_pred
        if expected_pred == inc.MAIN_JUDGE_LARGE_PREDICT:
            assert seen_anc[0][0] == inc.MID_AUTO_CONTEXT
    # Retry guard on attempt 1 must also account for judge_predict=6144 (MID_AUTO_CONTEXT edge)
    large_bad_verdict = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": fact1, "source": m1, "line_span": [99, 99], "relevant": True, "supported": True, "reason": "ok"}],
    })
    large_anchors = [{"text": fact1, "source": m1, "line_span": [1, 1]} for _ in range(inc.MAIN_JUDGE_LARGE_THRESHOLD + 1)]
    sys_len = len(inc.MAIN_JUDGE_SYSTEM)
    mid_large_budget = (inc.MID_AUTO_CONTEXT - inc.MAIN_JUDGE_LARGE_PREDICT - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN
    probe_large: list[str] = []
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (probe_large.append(p), large_bad_verdict)[1])
        with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
            inc.semantic_check("Glasshouse", loaded, [m1], files1, LOCAL, small_body, [], anchors=large_anchors)
    assert len(probe_large) == 2
    large_pad = mid_large_budget - sys_len - len(probe_large[1])
    assert large_pad >= 0
    edge_calls: list[int] = []
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (edge_calls.append(len(p)), large_bad_verdict)[1])
        with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
            inc.semantic_check("Glasshouse", loaded, [m1], {m1: {"text": fact1 + ("z" * (large_pad + 1))}}, LOCAL, small_body, [], anchors=large_anchors)
    assert len(edge_calls) == 1

    rel_prompts: list[str] = []
    rel_seq = iter([
        json.dumps({"verdict": "relevant", "reason": "r", "line_span": [99, 99]}),
        json.dumps({"verdict": "relevant", "reason": "r", "line_span": [1, 1]}),
    ])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (rel_prompts.append(p), next(rel_seq))[1])
        assert inc.source_relevance("Glasshouse", loaded, m1, files1[m1], LOCAL) is True
    assert len(rel_prompts) == 2
    for rp in rel_prompts:
        assert "single-line" not in rp
        assert "line_span" in rp
    rel_fb = json.loads(rel_prompts[1].split("\n", 1)[0])["span_feedback"]
    assert rel_fb["problem"] == "span_out_of_range"
    assert rel_fb["line_count"] == 1

    # 2) append retry on body is None + JSON_SCHEMA (missing line_span key / extra key) sends 4-key hint + previous_response
    for bad_patch in (
        json.dumps({"additions": [{"section": "Сейчас", "text": fact1, "source": m1}], "corrections": []}),
        json.dumps({"additions": [{"section": "Сейчас", "text": fact1, "source": m1, "line_span": [1, 1], "reason": "extra"}], "corrections": []}),
    ):
        prompts: list[str] = []
        seq = iter([bad_patch, valid_patch])
        with monkeypatch.context() as mp:
            mp.setattr(inc, "local_complete", lambda p, *a, **kw: (prompts.append(p), next(seq))[1])
            mp.setattr(inc, "validate_candidate", lambda *a, **kw: None)
            out = inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
        assert fact1 in out and len(prompts) == 2
        fb = json.loads(prompts[1].split("\n", 1)[0])["correction_feedback"]
        assert fb["failure"] == "malformed patch entry"
        assert fb["previous_response"] == bad_patch
        assert fb["instruction"].startswith(inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.JSON_SCHEMA] + " Return ONLY valid patch JSON")
        for key in ('"section"', '"text"', '"source"', '"line_span"', '"before"', '"after"'):
            assert key in fb["instruction"]

    # 3) append retry on body is None + JSON_MALFORMED sends JSON_MALFORMED hint + previous_response
    mal_prompts: list[str] = []
    mal_seq = iter(["{truncated", valid_patch])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (mal_prompts.append(p), next(mal_seq))[1])
        mp.setattr(inc, "validate_candidate", lambda *a, **kw: None)
        assert fact1 in inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
    fb_mal = json.loads(mal_prompts[1].split("\n", 1)[0])["correction_feedback"]
    assert fb_mal["failure"] == "malformed model JSON"
    assert fb_mal["previous_response"] == "{truncated"
    assert fb_mal["instruction"].startswith(inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.JSON_MALFORMED] + " Return ONLY valid patch JSON")

    # 4) append retry on patch_quote_missing omits previous_response while preserving missing_quotes + rejected_claims
    miss_prompts: list[str] = []
    miss_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": fact1, "source": m1, "line_span": [99, 99]}],
        "corrections": [],
    })
    miss_seq = iter([miss_patch, valid_patch])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (miss_prompts.append(p), next(miss_seq))[1])
        mp.setattr(inc, "validate_candidate", lambda *a, **kw: None)
        assert fact1 in inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
    fb_pq = json.loads(miss_prompts[1].split("\n", 1)[0])["correction_feedback"]
    assert "previous_response" not in fb_pq
    assert fb_pq["missing_quotes"] == [{"text": f"- {fact1} [[{m1}]]", "source": m1, "problem": "span_out_of_range"}]
    assert fb_pq["instruction"].startswith(inc._PATCH_QUOTE_MISSING_HINT + " Return ONLY valid patch JSON")

    # 5) Single-source downstream judge JSON_MALFORMED / JSON_SCHEMA (body is not None):
    #    rewrites failure, omits previous_response, and uses _JUDGE_JSON_OVERFLOW_HINT (never 4-key patch hint).
    for judge_bad, orig_msg in (
        ('{"verdict":"accept","claims":[{"text":"cut', "malformed model JSON"),
        (json.dumps({"verdict": "accept"}), "unexpected model JSON fields"),
    ):
        j_gen_prompts: list[str] = []
        j_seq = iter([valid_patch, judge_bad, valid_patch, valid_judge])

        def j_complete(prompt, cfg, *, system, **kw):
            if system == inc.GENERATE_SYSTEM:
                j_gen_prompts.append(prompt)
            return next(j_seq)

        with monkeypatch.context() as mp:
            mp.setattr(inc, "local_complete", j_complete)
            mp.setattr(inc, "audit_claim", lambda *a, **kw: None)
            assert fact1 in inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
        assert len(j_gen_prompts) == 2
        fb_j = json.loads(j_gen_prompts[1].split("\n", 1)[0])["correction_feedback"]
        assert fb_j["failure"] == "downstream validator JSON overflow or schema error on a valid patch"
        assert fb_j["failure"] != orig_msg
        assert "previous_response" not in fb_j
        assert fb_j["instruction"].startswith(inc._JUDGE_JSON_OVERFLOW_HINT + " Return ONLY valid patch JSON")
        assert inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.JSON_SCHEMA] not in fb_j["instruction"]

    # 6) Multi-source downstream judge JSON_SCHEMA (body is not None) skips generator retry immediately
    #    and nightly_dossier splits via _SPLITTABLE_DETAILS[JSON_SCHEMA] into _append_sequential.
    nd, graph, old_path = world
    _new(graph, m1, "Glasshouse update alpha.")
    _new(graph, m2, "Glasshouse update beta.")
    monkeypatch.setattr(inc, "source_relevance", lambda *a, **kw: True)
    gen_batches: list[list[str]] = []
    judge_batches: list[list[str]] = []

    def split_schema_complete(prompt, cfg, *, system, **kw):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        names = list(payload["new_sources"])
        if system == inc.GENERATE_SYSTEM:
            gen_batches.append(names)
            return json.dumps({
                "additions": [{
                    "section": "Сейчас", "text": f"Fact {n}.", "source": n,
                    "line_span": [2, 2],
                } for n in names],
                "corrections": [],
            })
        if system == inc.MAIN_JUDGE_SYSTEM:
            judge_batches.append(names)
            if len(names) > 1:
                return json.dumps({"verdict": "accept"})
            src = names[0]
            return json.dumps({
                "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
                "reason": "ok",
                "claims": [{
                    "text": f"- Fact {src}. [[{src}]]", "source": src,
                    "line_span": [2, 2],
                    "relevant": True, "supported": True, "reason": "ok",
                }],
            })
        raise AssertionError(f"unexpected system: {system}")

    monkeypatch.setattr(inc, "local_complete", split_schema_complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: None)
    res = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert sum(g.built for g in res.graphs) == 1
    assert gen_batches == [[m1, m2], [m1], [m2]]
    assert judge_batches == [[m1, m2], [m1], [m2]]
    assert res.telemetry.snapshot()["stage_refusals"] == {}
    final_body = old_path.read_text(encoding="utf-8")
    assert f"[[{m1}]]" in final_body and f"[[{m2}]]" in final_body

    # 7) Multi-source patch JSON_SCHEMA from append itself (body is None) MUST still retry append once
    bad_schema_calls: list[int] = []
    loaded_m = _loaded_with_body(BODY)
    files_m = {m1: {"text": "Glasshouse one."}, m2: {"text": "Glasshouse two."}}
    bad_schema_reply = json.dumps({"additions": [{"section": "Сейчас", "text": "x", "source": m1}], "corrections": []})
    monkeypatch.setattr(inc, "local_complete", lambda *a, **kw: (bad_schema_calls.append(1), bad_schema_reply)[1])
    with pytest.raises(inc.Refused, match="malformed patch entry"):
        inc.append("Glasshouse", loaded_m, [m1, m2], files_m, LOCAL)
    assert len(bad_schema_calls) == 2


def test_round13_quote_echo_diarization_and_stage_progress_retries(monkeypatch):
    m1, m2 = "2099-01-02_100000", "2099-01-03_100000"
    loaded = _loaded_with_body(BODY)
    fact1 = "Valve pressure stabilized at 4 bar."
    files1 = {m1: {"text": fact1}}
    files2 = {m1: {"text": fact1}, m2: {"text": "Second source update."}}
    valid_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": fact1, "source": m1, "line_span": [1, 1]}],
        "corrections": [],
    })
    bad_quote_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": fact1, "source": m1, "line_span": [9, 9]}],
        "corrections": [],
    })
    valid_judge = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": fact1, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
    })
    reject_judge = json.dumps({
        "verdict": "reject", "conserved": True, "supported": False, "corrections_valid": True,
        "reason": "unsupported", "claims": [],
    })

    # 1) _append_failure_stage exact rank values (deterministic_check -> 0) and shared diarization / hint contracts
    assert inc.MAX_APPEND_ATTEMPTS == 3
    assert inc._append_failure_stage(None, telemetry.Detail.CLAIM_QUOTE_MISSING) == 0
    assert inc._append_failure_stage(None, telemetry.Detail.AUDIT_SUPPORT_REJECTED) == 0
    for det_detail in inc._DETERMINISTIC_DETAILS:
        assert inc._append_failure_stage(BODY, det_detail) == 0
    assert inc._append_failure_stage(BODY, telemetry.Detail.SEMANTIC_SUPPORTED_FALSE) == 1
    assert inc._append_failure_stage(BODY, None) == 1
    assert inc._append_failure_stage(BODY, telemetry.Detail.AUDIT_SUPPORT_REJECTED) == 2
    assert inc._append_failure_stage(BODY, telemetry.Detail.AUDIT_TOPIC_REJECTED) == 2
    assert inc._DIARIZATION_ANONYMIZATION_RULE in inc.SUPPORT_AUDIT_INSTRUCTION
    assert "widening line_span" in inc._APPEND_FEEDBACK_HINTS[telemetry.Detail.AUDIT_SUPPORT_REJECTED]

    # 2) source_relevance uses span_feedback with closed problem tag and recovers on retry
    sr_prompts: list[str] = []
    sr_seq = iter([
        json.dumps({"verdict": "relevant", "reason": "r", "line_span": [9, 9]}),
        json.dumps({"verdict": "relevant", "reason": "r", "line_span": [1, 1]}),
    ])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (sr_prompts.append(p), next(sr_seq))[1])
        assert inc.source_relevance("Glasshouse", loaded, m1, files1[m1], LOCAL) is True
    sr_fb = json.loads(sr_prompts[1].split("\n", 1)[0])["span_feedback"]
    assert sr_fb["problem"] == "span_out_of_range"
    assert sr_fb["line_count"] == 1

    # 3) semantic_check includes shared diarization rule, anchor copy note, and span_feedback problem tag
    sc_prompts: list[str] = []
    sc_seq = iter([
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": fact1, "source": m1, "line_span": [9, 9], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        valid_judge,
    ])
    small_body = BODY.replace("## Как пришли", f"- {fact1} [[{m1}]]\n## Как пришли")
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (sc_prompts.append(p), next(sc_seq))[1])
        mp.setattr(inc, "audit_claim", lambda *a, **kw: None)
        inc.semantic_check("Glasshouse", loaded, [m1], files1, LOCAL, small_body, [],
                           anchors=[{"text": fact1, "source": m1, "line_span": [1, 1]}])
    assert inc._DIARIZATION_ANONYMIZATION_RULE in sc_prompts[0]
    assert "reuse an accurate single-block line_span" in sc_prompts[0]
    sc_fb = json.loads(sc_prompts[1].split("\n", 1)[0])["span_feedback"]
    assert sc_fb["invalid_spans"] == [{"text": fact1, "source": m1, "problem": "span_out_of_range"}]
    assert "do NOT reject a supported claim solely for a prior judge line_span error" in sc_fb["instruction"]

    # 4) Stage-progress retry (0 -> 1 -> PASS and 1 -> 2 -> PASS) recovers on attempt 2 for single source,
    #    and includes unconditional 4-key schema reminder on retries
    p01_prompts: list[str] = []
    p01_seq = iter([bad_quote_patch, valid_patch, reject_judge, valid_patch, valid_judge])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (p01_prompts.append(p), next(p01_seq))[1])
        mp.setattr(inc, "audit_claim", lambda *a, **kw: None)
        assert fact1 in inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
    assert len(p01_prompts) == 5
    fb_patch = json.loads(p01_prompts[1].split("\n", 1)[0])["correction_feedback"]
    assert fb_patch["missing_quotes"] == [{"text": f"- {fact1} [[{m1}]]", "source": m1, "problem": "span_out_of_range"}]
    assert '{"section","text","source","line_span"}' in fb_patch["instruction"]
    fb_sem = json.loads(p01_prompts[3].split("\n", 1)[0])["correction_feedback"]
    assert '{"section","text","source","line_span"}' in fb_sem["instruction"]

    # Empty patch after bad_quote_patch (0 -> deterministic 0) is NOT progress and stops after 2 calls!
    empty_patch = json.dumps({"additions": [], "corrections": []})
    gen_det: list[int] = []
    det_seq = iter([bad_quote_patch, empty_patch])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, cfg, *, system, **kw: (gen_det.append(1) if system == inc.GENERATE_SYSTEM else None, next(det_seq))[1])
        with pytest.raises(inc.Refused, match="does not attribute each accepted source"):
            inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
    assert len(gen_det) == 2

    # 1 -> 2 -> PASS for both AUDIT_SUPPORT_REJECTED and AUDIT_TOPIC_REJECTED
    for audit_detail, audit_msg in (
        (telemetry.Detail.AUDIT_SUPPORT_REJECTED, "independent claim audit was not accepted (supported)"),
        (telemetry.Detail.AUDIT_TOPIC_REJECTED, "independent claim audit was not accepted (relevant)"),
    ):
        gen_n: list[int] = []
        audit_n = [0]
        seq12 = iter([valid_patch, reject_judge, valid_patch, valid_judge, valid_patch, valid_judge])

        def fake_audit(*a, **kw):
            audit_n[0] += 1
            if audit_n[0] == 1:
                raise inc.Refused(audit_msg, detail=audit_detail, rejected_claims=(fact1,))

        with monkeypatch.context() as mp:
            mp.setattr(inc, "local_complete", lambda p, cfg, *, system, **kw: (gen_n.append(1) if system == inc.GENERATE_SYSTEM else None, next(seq12))[1])
            mp.setattr(inc, "audit_claim", fake_audit)
            assert fact1 in inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
        assert len(gen_n) == 3

    # 5) Exhaustion at MAX_APPEND_ATTEMPTS=3 on strict 0 -> 1 -> 2 stops after exactly 3 generator calls
    gen_012: list[int] = []
    seq012 = iter([bad_quote_patch, valid_patch, reject_judge, valid_patch, valid_judge])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, cfg, *, system, **kw: (gen_012.append(1) if system == inc.GENERATE_SYSTEM else None, next(seq012))[1])
        mp.setattr(inc, "audit_claim", lambda *a, **kw: (_ for _ in ()).throw(
            inc.Refused("independent claim audit was not accepted (supported)", detail=telemetry.Detail.AUDIT_SUPPORT_REJECTED)
        ))
        with pytest.raises(inc.Refused, match="independent claim audit was not accepted"):
            inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
    assert len(gen_012) == 3

    # 6) Stage regression (1 -> 0) and same-stage repeat (2 -> 2) both stop after 2 generator calls
    gen_reg: list[int] = []
    seq_reg = iter([valid_patch, reject_judge, bad_quote_patch])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, cfg, *, system, **kw: (gen_reg.append(1) if system == inc.GENERATE_SYSTEM else None, next(seq_reg))[1])
        with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
            inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
    assert len(gen_reg) == 2

    gen_22: list[int] = []
    seq_22 = iter([valid_patch, valid_judge, valid_patch, valid_judge])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, cfg, *, system, **kw: (gen_22.append(1) if system == inc.GENERATE_SYSTEM else None, next(seq_22))[1])
        mp.setattr(inc, "audit_claim", lambda *a, **kw: (_ for _ in ()).throw(
            inc.Refused("independent claim audit was not accepted (supported)", detail=telemetry.Detail.AUDIT_SUPPORT_REJECTED)
        ))
        with pytest.raises(inc.Refused, match="independent claim audit was not accepted"):
            inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
    assert len(gen_22) == 2

    # 7) Multi-source (len(names) > 1) stops after 2 calls even when stage progresses 0 -> 1
    valid_patch_2 = json.dumps({
        "additions": [
            {"section": "Сейчас", "text": fact1, "source": m1, "line_span": [1, 1]},
            {"section": "Сейчас", "text": "Second source update.", "source": m2, "line_span": [1, 1]},
        ],
        "corrections": [],
    })
    gen_multi: list[int] = []
    seq_multi = iter([bad_quote_patch, valid_patch_2, reject_judge])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, cfg, *, system, **kw: (gen_multi.append(1) if system == inc.GENERATE_SYSTEM else None, next(seq_multi))[1])
        with pytest.raises(inc.Refused, match="semantic conservation was not accepted"):
            inc.append("Glasshouse", loaded, [m1, m2], files2, LOCAL)
    assert len(gen_multi) == 2

    # 8) Non-validation Refused (Reason.PREFLIGHT / Reason.CONTRACT) stops immediately without retrying
    for non_val_reason in (telemetry.Reason.PREFLIGHT, telemetry.Reason.CONTRACT):
        gen_nv: list[int] = []
        with monkeypatch.context() as mp:
            mp.setattr(inc, "local_complete", lambda *a, **kw: (gen_nv.append(1), valid_patch)[1])
            mp.setattr(inc, "validate_candidate", lambda *a, **kw: (_ for _ in ()).throw(
                inc.Refused("preflight or contract error", reason=non_val_reason)
            ))
            with pytest.raises(inc.Refused, match="preflight or contract error"):
                inc.append("Glasshouse", loaded, [m1], files1, LOCAL)
        assert len(gen_nv) == 1


def test_round14_ollama_schema_line_span_and_invariants(monkeypatch):
    # 1) _split_source_lines, _format_indexed_source, _quote_hits CR normalization, and _resolve_line_span boundaries
    cr_text = "First line.\rSecond line.\r\nThird line.\n   \nFifth line."
    assert inc._split_source_lines(cr_text) == ["First line.", "Second line.", "Third line.", "   ", "Fifth line."]
    assert inc._format_indexed_source("Alpha\nBeta") == "L1: Alpha\nL2: Beta"
    assert inc._quote_hits(cr_text, "First line.\nSecond line.") == 1
    assert inc._quote_hits("First line.\nSecond line.", "First line.\rSecond line.") == 1

    assert inc._resolve_line_span(cr_text, [1, 2], max_lines=2) == ("First line.\nSecond line.", "")
    assert inc._resolve_line_span(cr_text, [1, 3], max_lines=2) == (None, "span_too_wide")
    assert inc._resolve_line_span(cr_text, [2, 1], max_lines=5) == (None, "span_inverted")
    assert inc._resolve_line_span(cr_text, [0, 1], max_lines=5) == (None, "span_out_of_range")
    assert inc._resolve_line_span(cr_text, [1, 6], max_lines=10) == (None, "span_out_of_range")
    assert inc._resolve_line_span(cr_text, [4, 4], max_lines=5) == (None, "span_empty")
    for bad_span in ([], [1], [1, 2, 3], [True, 1], [1, False], ["1", "2"], None, "1-2"):
        assert inc._resolve_line_span(cr_text, bad_span, max_lines=5) == (None, "span_invalid")
    assert inc._span_schema(0)["items"]["maximum"] == 1

    # 2) Schema builders: source_relevance anyOf, append enum, semantic_check empty vs non-empty names
    sr_schema = inc._source_relevance_schema(25)
    assert "anyOf" in sr_schema and len(sr_schema["anyOf"]) == 2
    assert sr_schema["anyOf"][0]["additionalProperties"] is False
    assert sr_schema["anyOf"][1]["additionalProperties"] is False
    assert sr_schema["anyOf"][0]["properties"]["verdict"] == {"const": "relevant"}
    assert sr_schema["anyOf"][0]["properties"]["line_span"]["minItems"] == 2
    assert sr_schema["anyOf"][0]["properties"]["line_span"]["maxItems"] == 2
    assert sr_schema["anyOf"][0]["properties"]["line_span"]["items"]["minimum"] == 1
    assert sr_schema["anyOf"][0]["properties"]["line_span"]["items"]["maximum"] == 25
    assert sr_schema["anyOf"][1]["properties"]["line_span"] == {"type": "array", "maxItems": 0}

    m1, m2 = "2099-01-02_100000", "2099-01-03_100000"
    app_schema = inc._append_schema([m1, m2], 40)
    assert app_schema["additionalProperties"] is False
    assert app_schema["properties"]["additions"]["items"]["additionalProperties"] is False
    assert app_schema["properties"]["corrections"]["items"]["additionalProperties"] is False
    assert app_schema["properties"]["additions"]["items"]["properties"]["source"] == {"type": "string", "enum": [m1, m2]}
    assert app_schema["properties"]["corrections"]["items"]["properties"]["source"] == {"type": "string", "enum": [m1, m2]}
    assert app_schema["properties"]["additions"]["items"]["properties"]["line_span"]["items"]["maximum"] == 40

    sem_empty = inc._semantic_check_schema([], {})
    assert sem_empty["additionalProperties"] is False
    assert sem_empty["properties"]["claims"] == {"type": "array", "maxItems": 0}
    sem_full = inc._semantic_check_schema([m1], {m1: 30})
    assert sem_full["additionalProperties"] is False
    assert sem_full["properties"]["claims"]["items"]["additionalProperties"] is False
    assert sem_full["properties"]["claims"]["items"]["properties"]["source"] == {"type": "string", "enum": [m1]}
    assert "anyOf" in sem_full["properties"]["claims"]["items"]["properties"]["line_span"]

    # 3) _normalize_patch_line strips leaked 'L<n>: ' prefixes (with or without leading '- ') and rejects empty result
    assert inc._normalize_patch_line("L7: Valve sealed.", m1, [m1], bad_source_msg="bad") == (f"- Valve sealed. [[{m1}]]", m1)
    assert inc._normalize_patch_line("- L12: Valve sealed.", m1, [m1], bad_source_msg="bad") == (f"- Valve sealed. [[{m1}]]", m1)
    assert inc._normalize_patch_line("L3:port open.", m1, [m1], bad_source_msg="bad") == (f"- L3:port open. [[{m1}]]", m1)
    with pytest.raises(inc.Refused, match="empty or invalid patch entry"):
        inc._normalize_patch_line("- L3:   ", m1, [m1], bad_source_msg="bad")
    with pytest.raises(inc.Refused, match="empty or invalid patch entry"):
        inc._normalize_patch_line("L3: # Heading", m1, [m1], bad_source_msg="bad")

    # 4) append refuses empty names before building schema; source_relevance accepts irrelevant with non-empty line_span
    loaded = _loaded_with_body(BODY)
    with pytest.raises(inc.Refused, match="at least one new source is required"):
        inc.append("Glasshouse", loaded, [], {}, LOCAL)

    monkeypatch.setattr(
        inc, "local_complete",
        lambda *a, **kw: json.dumps({"verdict": "irrelevant", "reason": "off topic", "line_span": [1, 1]}),
    )
    assert inc.source_relevance("Glasshouse", loaded, m1, {"text": "Off-topic line."}, LOCAL) is False

    # 5) _apply_patch enforces valid unique line_span on corrections too (span_out_of_range & span_ambiguous)
    corr_files = {m1: {"text": "Vent A closed.\nOther line.\nVent A closed."}}
    with pytest.raises(inc._MissingClaimQuotes) as corr_miss:
        inc._apply_patch(loaded, [m1], corr_files, {
            "additions": [],
            "corrections": [{"before": "Vent A is open.", "after": "Vent A closed.", "source": m1, "line_span": [9, 9]}],
        })
    assert corr_miss.value.missing_quotes[0]["problem"] == "span_out_of_range"
    with pytest.raises(inc.Refused, match="ambiguous claim evidence") as corr_amb:
        inc._apply_patch(loaded, [m1], corr_files, {
            "additions": [],
            "corrections": [{"before": "Vent A is open.", "after": "Vent A closed.", "source": m1, "line_span": [1, 1]}],
        })
    assert corr_amb.value.detail is telemetry.Detail.CLAIM_QUOTE_AMBIGUOUS

    # 6) semantic_check retries once on span_ambiguous and succeeds when judge widens line_span on attempt 1
    amb_src = "Да.\nКонтекст теплицы.\nДа.\n"
    fact_amb = "Подтверждён контекст теплицы."
    amb_body = BODY.replace("## Как пришли", f"- {fact_amb} [[{m1}]]\n## Как пришли")
    amb_prompts: list[str] = []
    amb_audited: list[dict] = []
    amb_seq = iter([
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": fact_amb, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "widened",
            "claims": [{"text": fact_amb, "source": m1, "line_span": [1, 2], "relevant": True, "supported": True, "reason": "ok"}],
        }),
    ])
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (amb_prompts.append(p), next(amb_seq))[1])
        mp.setattr(inc, "audit_claim", lambda *a, **kw: amb_audited.append(a[2]))
        inc.semantic_check("Glasshouse", loaded, [m1], {m1: {"text": amb_src}}, LOCAL, amb_body, [])
    assert len(amb_prompts) == 2
    amb_fb = json.loads(amb_prompts[1].split("\n", 1)[0])["span_feedback"]
    assert amb_fb["invalid_spans"] == [{"text": fact_amb, "source": m1, "problem": "span_ambiguous"}]
    assert amb_audited[0]["quote"] == "Да.\nКонтекст теплицы."

    # 7) Sentinel char (\ufdd0) produces span_unmatchable (never KeyError: 'problem') in semantic_check & _apply_patch,
    #    and duplicate corrections targeting the same ## Сейчас line are rejected even when the first span is invalid.
    sent_src = "Vent A closed \ufdd0.\nVent A verified."
    assert inc._resolve_unique_span(sent_src, [1, 1], max_lines=5) == (None, "span_unmatchable")
    sent_prompts: list[str] = []
    sent_reply = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": fact_amb, "source": m1, "line_span": [1, 1], "relevant": True, "supported": True, "reason": "ok"}],
    })
    with monkeypatch.context() as mp:
        mp.setattr(inc, "local_complete", lambda p, *a, **kw: (sent_prompts.append(p), sent_reply)[1])
        with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
            inc.semantic_check("Glasshouse", loaded, [m1], {m1: {"text": sent_src}}, LOCAL, amb_body, [])
    assert len(sent_prompts) == 2
    assert json.loads(sent_prompts[1].split("\n", 1)[0])["span_feedback"]["invalid_spans"] == [
        {"text": fact_amb, "source": m1, "problem": "span_unmatchable"}
    ]

    with pytest.raises(inc._MissingClaimQuotes) as sent_corr:
        inc._apply_patch(loaded, [m1], {m1: {"text": sent_src}}, {
            "additions": [],
            "corrections": [{"before": "Vent A is open.", "after": "Vent A closed.", "source": m1, "line_span": [1, 1]}],
        })
    assert sent_corr.value.missing_quotes[0]["problem"] == "span_unmatchable"

    with pytest.raises(inc.Refused, match="before must identify one current line in ## Сейчас"):
        inc._apply_patch(loaded, [m1], {m1: {"text": "Vent A closed.\nVent A verified."}}, {
            "additions": [],
            "corrections": [
                {"before": "Vent A is open.", "after": "Vent A closed.", "source": m1, "line_span": [99, 99]},
                {"before": "Vent A is open.", "after": "Vent A verified.", "source": m1, "line_span": [2, 2]},
            ],
        })






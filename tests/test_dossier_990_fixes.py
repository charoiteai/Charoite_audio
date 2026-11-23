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
            "quote": "Glasshouse: latency dropped from 12s to 4s, owner [[People/Alex|Alex]].",
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
                "quote": "Glasshouse: latency dropped from 12s to 4s, owner Alex.",
                "relevant": True,
                "supported": True,
                "reason": "direct Glasshouse metric",
            }],
        }),
    ]))
    inc.semantic_check("Glasshouse", loaded, [name], files, LOCAL, body, [])
    assert len(audited) == 1


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
                "quote": "Cover fitted on Monday.",
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
                        "quote": "Glasshouse vent is sealed.", "relevant": True, "supported": True, "reason": "ok"}],
        }),
    ]))
    inc.semantic_check("Glasshouse", loaded, [name], files, LOCAL, body, [])
    # 1 call for text + 1 call for quote (not doubled in semantic_check)
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
    ([json.dumps({"verdict": "relevant", "reason": "   ", "quote": "x"})], "relevance_incomplete"),
    ([json.dumps({"verdict": "relevant", "reason": "ok", "quote": "   "})] * 2, "relevance_quote_empty"),
    ([json.dumps({"verdict": "relevant", "reason": "ok", "quote": "absent quote"})] * 2, "relevance_quote_missing"),
    ([json.dumps({"verdict": "uncertain", "reason": "ambiguous", "quote": ""})], "relevance_uncertain"),
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
    good_body = BODY.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- Glasshouse vent sealed. [[{name}]]",
    )
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({"verdict": "relevant", "reason": "fact", "quote": "Glasshouse vent sealed."}),
        "bad json attempt 0",
        json.dumps({"body": good_body, "changes": []}),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Glasshouse vent sealed.", "source": name,
                        "quote": "Glasshouse vent sealed.", "relevant": True, "supported": True, "reason": "ok"}],
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
    good_body = BODY.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- Glasshouse vent sealed. [[{name}]]",
    )
    one_attempt = [
        json.dumps({"body": good_body, "changes": []}),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Glasshouse vent sealed.", "source": name,
                        "quote": "Glasshouse vent sealed.", "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({"verdict": "accept", "supported": True, "reason": "ok"}),
        json.dumps({"verdict": "reject", "relevant": False, "reason": "off topic"}),
    ]
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({"verdict": "relevant", "reason": "fact", "quote": "Glasshouse vent sealed."}),
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
    good_body = BODY.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- Glasshouse vent sealed. [[{name}]]",
    )
    rel_ok = json.dumps({"verdict": "relevant", "reason": "fact", "quote": "Glasshouse vent sealed."})
    sem_ok = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": "Glasshouse vent sealed.", "source": name,
                    "quote": "Glasshouse vent sealed.", "relevant": True, "supported": True, "reason": "ok"}],
    })
    cases = [
        ([rel_ok, "not json", "still not json"], {"append": {"json_malformed": 1}}),
        ([rel_ok, json.dumps({"body": "## Сейчас\n—", "changes": []}),
          json.dumps({"body": "## Сейчас\n—", "changes": []})],
         {"deterministic_check": {"sections_invalid": 1}}),
        ([rel_ok, json.dumps({"body": good_body, "changes": []}),
          json.dumps({"verdict": "reject", "conserved": True, "supported": False,
                      "corrections_valid": True, "reason": "no", "claims": []}),
          json.dumps({"body": good_body, "changes": []}),
          json.dumps({"verdict": "reject", "conserved": True, "supported": False,
                      "corrections_valid": True, "reason": "no", "claims": []})],
         {"semantic_check": {"semantic_rejected": 1}}),
        ([rel_ok, json.dumps({"body": good_body, "changes": []}), sem_ok,
          json.dumps({"verdict": "reject", "supported": False, "reason": "unsupported"}),
          json.dumps({"body": good_body, "changes": []}), sem_ok,
          json.dumps({"verdict": "reject", "supported": False, "reason": "unsupported"})],
         {"audit_support": {"audit_support_rejected": 1}}),
    ]
    for replies, expected_stage in cases:
        monkeypatch.setattr(inc, "local_complete", _seq_replies(replies))
        result = nd.run_all([graph], LOCAL, full=False, dry=False)
        snap = result.telemetry.snapshot()
        assert snap["final_refusals"] == 1
        assert snap["stage_refusals"] == expected_stage


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
    good_body = BODY.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- Vent sealed on Monday. [[{name}]]",
    )
    seen_prompts = []
    long_bad_quote = "missing " + ("x" * 260)
    replies = iter([
        json.dumps({"verdict": "relevant", "reason": "fact", "quote": long_bad_quote}),
        json.dumps({"verdict": "relevant", "reason": "fact", "quote": "Vent sealed on Monday."}),
        json.dumps({"body": good_body, "changes": []}),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Vent sealed on Monday.", "source": name,
                        "quote": "Vent sealed on Monday.", "relevant": True, "supported": True, "reason": "ok"}],
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
    assert "correction_feedback" in retry_payload
    assert len(retry_payload["correction_feedback"]["previous_quote"]) == 200
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
        json.dumps({"verdict": "relevant", "reason": "fact", "quote": "wrong quote"}),
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
            return json.dumps({"verdict": "relevant", "reason": "fact", "quote": "absent quote"})

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


def test_append_predict_scales_for_large_dossier_respects_small_ctx_and_trims_retry_feedback(monkeypatch):
    # Small body stays at GEN_PREDICT (1800)
    assert inc._append_predict("x" * 500, LOCAL) == dossier.GEN_PREDICT
    # Exact boundary (1200 chars -> 600 + 1200 = 1800; 1201 chars -> 601 + 1200 = 1801)
    assert inc._append_predict("x" * 1200, LOCAL) == dossier.GEN_PREDICT
    assert inc._append_predict("x" * 1201, LOCAL) == 1801
    # Large odd body (4373 chars -> ceil(4373/2)=2187 + 1200 = 3387 tokens) kills floor/ceil mutants
    assert inc._append_predict("x" * 4373, LOCAL) == 3387
    # Very large body clamps at APPEND_MAX_PREDICT (8192)
    assert inc._append_predict("x" * 20_000, LOCAL) == inc.APPEND_MAX_PREDICT
    # Explicit small num_ctx (0 < num_ctx < MIN_AUTO_CONTEXT) keeps GEN_PREDICT; 0, -1, 8192 scale normally
    assert inc._append_predict("x" * 5000, {"llm": {"engine": "ollama", "num_ctx": 4096}}) == dossier.GEN_PREDICT
    assert inc._append_predict("x" * 5000, {"llm": {"engine": "ollama", "num_ctx": "4096"}}) == dossier.GEN_PREDICT
    assert inc._append_predict("x" * 5000, {"llm": {"engine": "ollama", "num_ctx": 0}}) == 3700
    assert inc._append_predict("x" * 5000, {"llm": {"engine": "ollama", "num_ctx": -1}}) == 3700
    assert inc._append_predict("x" * 5000, {"llm": {"engine": "ollama", "num_ctx": inc.MIN_AUTO_CONTEXT}}) == 3700
    # Invalid/bool num_ctx does not crash _append_predict (deferred to _stage_config PREFLIGHT)
    assert inc._append_predict("x" * 5000, {"llm": {"engine": "ollama", "num_ctx": True}}) == 3700
    assert inc._append_predict("x" * 5000, {"llm": {"engine": "ollama", "num_ctx": "bad"}}) == 3700

    # _trim_feedback_response preserves short responses and trims long ones head+tail
    short = "a" * inc.APPEND_FEEDBACK_RESPONSE_CHARS
    assert inc._trim_feedback_response(short) == short
    long_raw = "HEAD" + ("m" * 3000) + "TAIL"
    trimmed = inc._trim_feedback_response(long_raw)
    assert trimmed.startswith("HEAD") and trimmed.endswith("TAIL")
    assert "\n...\n" in trimmed and len(trimmed) == inc.APPEND_FEEDBACK_RESPONSE_CHARS + len("\n...\n")

    # End-to-end append passes scaled num_predict and trimmed previous_response on retry
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
    expected_predict = inc._append_predict(loaded.parsed.body, LOCAL)
    assert expected_predict > dossier.GEN_PREDICT

    name = "2099-01-02_100000"
    files = {name: {"text": "Glasshouse vent sealed.", "kind": "Встречи"}}
    good_body = loaded.parsed.body.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- Glasshouse vent sealed. [[{name}]]",
    )
    seen = []
    huge_bad = "{" + ("z" * 4500)
    replies = iter([huge_bad, json.dumps({"body": good_body, "changes": []})])

    def fake_complete(prompt, config, *, system, num_predict, **kwargs):
        seen.append((prompt, config["llm"]["num_ctx"], num_predict))
        return next(replies)

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    monkeypatch.setattr(inc, "validate_candidate", lambda *a, **kw: None)
    out = inc.append("Glasshouse", loaded, [name], files, LOCAL)
    assert "Glasshouse vent sealed." in out
    assert len(seen) == 2
    assert seen[0][2] == expected_predict and seen[1][2] == expected_predict
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
    full_body = loaded.parsed.body.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- F1 [[{m1}]]\n- F2 [[{m2}]]\n- F3 [[{m3}]]",
    )
    partial_body = loaded.parsed.body.replace(
        "- Vent A is open. [[2099-01-01_100000]]",
        f"- Vent A is open. [[2099-01-01_100000]]\n- F3 [[{m3}]]",
    )

    def run_case(attempt0_raw: str, *, fail_validate_attempt0: bool = False):
        prompts = []
        replies = iter([attempt0_raw, json.dumps({"body": full_body, "changes": []})])

        def fake_complete(prompt, config, **kwargs):
            prompts.append(prompt)
            return next(replies)

        def fake_validate(theme, ld, names, fls, cfg, body, changes):
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

    # (a) malformed JSON -> result is None -> no UnboundLocalError and no missing_citations key/hint
    fb_malformed = run_case("not json")
    assert "missing_citations" not in fb_malformed
    assert "missing_citations" not in fb_malformed["instruction"]

    # (b) non-string result["body"] -> no TypeError and no missing_citations key/hint
    fb_non_str = run_case(json.dumps({"body": 123, "changes": []}))
    assert "missing_citations" not in fb_non_str
    assert "missing_citations" not in fb_non_str["instruction"]

    # (c) partial citations -> exact missing_citations list in source order and explicit hint
    fb_partial = run_case(json.dumps({"body": partial_body, "changes": []}))
    assert fb_partial["missing_citations"] == [f"[[{m1}]]", f"[[{m2}]]"]
    assert "missing_citations" in fb_partial["instruction"]

    # (d) all citations present, rejected downstream -> no missing_citations key/hint
    fb_complete = run_case(json.dumps({"body": full_body, "changes": []}), fail_validate_attempt0=True)
    assert "missing_citations" not in fb_complete
    assert "missing_citations" not in fb_complete["instruction"]


@pytest.mark.parametrize("cfg_ctx,expected_cap", [
    (4096, 4096),
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
            # Valid JSON, missing required [[name]] -> deterministic_check/source_unattributed
            return json.dumps({"body": BODY, "changes": []})

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
            partial = payload["existing_dossier"].replace(
                "## Как пришли", f"- Fact {names[0]}. [[{names[0]}]]\n\n## Как пришли",
            )
            return json.dumps({"body": partial, "changes": []})
        src = names[0]
        updated = payload["existing_dossier"].replace(
            "## Как пришли", f"- Fact {src}. [[{src}]]\n\n## Как пришли",
        )
        return json.dumps({"body": updated, "changes": []})

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    assert sum(g.built for g in result.graphs) == 1
    # 2 batch attempts (both rejected by real deterministic_check) + 2 single-source sequential appends
    assert batch_sizes == [2, 2, 1, 1]
    text = old_path.read_text(encoding="utf-8")
    assert f"[[{m1}]]" in text and f"[[{m2}]]" in text
    assert result.telemetry.snapshot()["stage_refusals"] == {}

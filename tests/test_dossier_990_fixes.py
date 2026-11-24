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
    good_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": name}],
        "corrections": [],
    })
    monkeypatch.setattr(inc, "local_complete", _seq_replies([
        json.dumps({"verdict": "relevant", "reason": "fact", "quote": "Glasshouse vent sealed."}),
        "bad json attempt 0",
        good_patch,
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
    good_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": name}],
        "corrections": [],
    })
    one_attempt = [
        good_patch,
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
    good_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": name}],
        "corrections": [],
    })
    bad_section_patch = json.dumps({
        "additions": [{"section": "Источники", "text": "Glasshouse vent sealed.", "source": name}],
        "corrections": [],
    })
    empty_patch = json.dumps({"additions": [], "corrections": []})
    rel_ok = json.dumps({"verdict": "relevant", "reason": "fact", "quote": "Glasshouse vent sealed."})
    sem_ok = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": "Glasshouse vent sealed.", "source": name,
                    "quote": "Glasshouse vent sealed.", "relevant": True, "supported": True, "reason": "ok"}],
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
    long_bad_quote = "missing " + ("x" * 260)
    replies = iter([
        json.dumps({"verdict": "relevant", "reason": "fact", "quote": long_bad_quote}),
        json.dumps({"verdict": "relevant", "reason": "fact", "quote": "Vent sealed on Monday."}),
        json.dumps({"additions": [{"section": "Сейчас", "text": "Vent sealed on Monday.", "source": name}],
                    "corrections": []}),
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
        "additions": [{"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": name}],
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
    assert seen[0][2] == dossier.GEN_PREDICT and seen[1][2] == dossier.GEN_PREDICT
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
            {"section": "Сейчас", "text": "F1", "source": m1},
            {"section": "Сейчас", "text": "F2", "source": m2},
            {"section": "Сейчас", "text": "F3", "source": m3},
        ],
        "corrections": [],
    })
    partial_patch = json.dumps({
        "additions": [{"section": "Сейчас", "text": "F3", "source": m3}],
        "corrections": [],
    })

    def run_case(attempt0_raw: str, *, fail_validate_attempt0: bool = False):
        prompts = []
        replies = iter([attempt0_raw, full_patch])

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
                "additions": [{"section": "Сейчас", "text": f"Fact {names[0]}.", "source": names[0]}],
                "corrections": [],
            })
        src = names[0]
        return json.dumps({
            "additions": [{"section": "Сейчас", "text": f"Fact {src}.", "source": src}],
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
        b: {"text": "Second replaced. First replaced. Duplicate test. Bob joined."},
        c_src: {"text": "Extra decision."},
    }
    # Reverse order in corrections + duplicate of correction.after in additions + last-section append
    patch = {
        "additions": [
            {"section": "  ##  Сейчас  ", "text": "  - First now updated.  ", "source": b},
            {"section": "Решено", "text": "Extra decision.", "source": c_src},
            {"section": "Кто в теме", "text": "Bob joined.", "source": b},
        ],
        "corrections": [
            {"before": "Second line.", "after": "Second now updated.", "source": f"[[{b}]]", "quote": "Second replaced."},
            {"before": "First line.", "after": "First now updated.", "source": b, "quote": "First replaced."},
        ],
    }
    body, changes = inc._apply_patch(loaded, [b, c_src], patch)
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
            {"before": "First line.", "after": "Unified state.", "source": b, "quote": "First replaced."},
            {"before": "Second line.", "after": "Unified state.", "source": b, "quote": "Second replaced."},
        ],
    }
    merged_body, merged_changes = inc._apply_patch(loaded, [b], merge_patch)
    inc.deterministic_check(loaded, merged_body, [b], files, merged_changes)
    merged_current = merged_body.split("## Сейчас\n", 1)[1].split("\n\n## Как пришли", 1)[0]
    assert merged_current == f"- Unified state. [[{b}]]"
    merged_history = merged_body.split("## Как пришли\n", 1)[1].split("\n\n## Решено", 1)[0]
    assert merged_history.endswith(f"- First line. [[{a}]]\n- Second line. [[{a}]]")


def test_apply_patch_preserves_untouched_sections_and_em_dash_and_rejects_malformed():
    m1 = "2099-01-02_100000"
    empty_current_body = (
        "## Сейчас\n—\n"
        "## Как пришли\n— \n"
        "## Решено\n—\n"
        "## Открыто\n—\n"
        "## Кто в теме\n—"
    )
    loaded_empty = _loaded_with_body(empty_current_body)
    # Addition to Открыто must leave untouched ## Сейчас with "—" intact, and clean "— " when Как пришли is edited
    body, changes = inc._apply_patch(loaded_empty, [m1], {
        "additions": [
            {"section": "Как пришли", "text": "History note.", "source": m1},
            {"section": "Открыто", "text": "Open question.", "source": m1},
        ],
        "corrections": [],
    })
    assert changes == []
    assert body.startswith("## Сейчас\n—\n## Как пришли\n- History note. [[2099-01-02_100000]]\n## Решено\n—\n")
    assert "## Открыто\n- Open question. [[2099-01-02_100000]]\n## Кто в теме\n—" in body

    # Correction targeting placeholder "—" in ## Сейчас must be refused
    with pytest.raises(inc.Refused, match="must identify one current line"):
        inc._apply_patch(loaded_empty, [m1], {
            "additions": [],
            "corrections": [{"before": "—", "after": "New state.", "source": m1, "quote": "q"}],
        })

    loaded = _loaded_dossier()
    bad_cases = [
        ({"additions": "bad", "corrections": []}, "malformed patch entry"),
        ({"additions": [], "corrections": "bad"}, "malformed patch entry"),
        ({"additions": ["bad"], "corrections": []}, "malformed patch entry"),
        ({"additions": [{"section": "Сейчас", "text": "x"}], "corrections": []}, "malformed patch entry"),
        ({"additions": [{"section": 123, "text": "x", "source": m1}], "corrections": []}, "invalid patch section"),
        ({"additions": [{"section": "Правки автора", "text": "x", "source": m1}], "corrections": []}, "invalid patch section"),
        ({"additions": [{"section": "Сейчас", "text": "x", "source": "other"}], "corrections": []}, "unsupported patch source"),
        ({"additions": [], "corrections": ["bad"]}, "malformed correction ledger"),
        ({"additions": [], "corrections": [{"before": "Vent A"}]}, "malformed correction ledger"),
        ({"additions": [], "corrections": [{"before": 123, "after": "x", "source": m1, "quote": "q"}]}, "empty correction ledger entry"),
        ({"additions": [], "corrections": [{"before": "   ", "after": "x", "source": m1, "quote": "q"}]}, "empty correction ledger entry"),
        ({"additions": [], "corrections": [{"before": "Vent A", "after": "x", "source": m1, "quote": 123}]}, "empty correction ledger entry"),
        ({"additions": [], "corrections": [{"before": "Vent A", "after": "x", "source": m1, "quote": "   "}]}, "empty correction ledger entry"),
        ({"additions": [], "corrections": [{"before": "Vent A", "after": "x", "source": "other", "quote": "q"}]}, "unsupported or unattributed correction ledger"),
        ({"additions": [], "corrections": [{"before": "Absent claim", "after": "x", "source": m1, "quote": "q"}]}, "must identify one current line"),
        ({"additions": [], "corrections": [
            {"before": "Vent A", "after": "Closed 1.", "source": m1, "quote": "q"},
            {"before": "Vent A", "after": "Closed 2.", "source": m1, "quote": "q"},
        ]}, "must identify one current line"),
    ]
    for bad_patch, pattern in bad_cases:
        with pytest.raises(inc.Refused, match=pattern):
            inc._apply_patch(loaded, [m1], bad_patch)

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
    files = {m1: {"text": "Собеседник 1 сообщил: Vent A закрыт. Спикер 2 отметил риск."}}
    patch = {
        "additions": [
            {"section": "Открыто", "text": "Спикер 2 отметил риск.", "source": m1},
            {"section": "Кто в теме", "text": "Собеседник 3 назначен ответственным.", "source": m1},
            {"section": "Кто в теме", "text": "[[Люди/Спикер 1]]", "source": m1},
            {"section": "Кто в теме", "text": "участника встречи", "source": m1},
            {"section": "Кто в теме", "text": "участнику встречи", "source": m1},
            {"section": "Кто в теме", "text": "с участником встречи", "source": m1},
            {"section": "Кто в теме", "text": "об участнике встречи", "source": m1},
            {"section": "Кто в теме", "text": "[[Люди/Спикер 1|Анна Смирнова]]", "source": m1},
            {"section": "Кто в теме", "text": "Иван Петров (Спикер 2)", "source": m1},
            {"section": "Кто в теме", "text": "Соучастник встречи Борис", "source": m1},
        ],
        "corrections": [
            {
                "before": "Vent A is open.",
                "after": "Собеседник 1 сообщил: Vent A закрыт.",
                "source": m1,
                "quote": "Собеседник 1 сообщил: Vent A закрыт.",
            }
        ],
    }
    body, changes = inc._apply_patch(loaded, [m1], patch)
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
    body_weird, _ = inc._apply_patch(loaded, [weird_src], {
        "additions": [{"section": "Кто в теме", "text": "Анна Смирнова", "source": weird_src}],
        "corrections": [],
    })
    assert f"- Анна Смирнова [[{weird_src}]]" in body_weird.split("## Кто в теме", 1)[1]

    # Validation still runs BEFORE omitting anonymous Кто в теме entries (bad source/multiline refused).
    with pytest.raises(inc.Refused, match="unsupported patch source"):
        inc._apply_patch(loaded, [m1], {
            "additions": [{"section": "Кто в теме", "text": "Собеседник 3", "source": "unknown"}],
            "corrections": [],
        })
    # When an anonymous Кто в теме entry is the ONLY entry for a source, deterministic_check refuses attribution.
    body_only_anon, changes_only_anon = inc._apply_patch(loaded, [m1], {
        "additions": [{"section": "Кто в теме", "text": "Собеседник 3", "source": m1}],
        "corrections": [],
    })
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
                {"section": "Сейчас", "text": "Glasshouse vent sealed.", "source": m1},
                {"section": "Кто в теме", "text": "Собеседник 3", "source": m1},
            ],
            "corrections": [],
        }),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": "Glasshouse vent sealed.", "source": m1,
                        "quote": "Glasshouse vent sealed.", "relevant": True, "supported": True, "reason": "ok"}],
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
    max_budget = (inc.MAX_AUDIT_CONTEXT - inc.MAIN_JUDGE_PREDICT - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN
    assert inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (min_budget - sys_len),
                             inc.MAIN_JUDGE_PREDICT)["llm"]["num_ctx"] == inc.MIN_AUTO_CONTEXT
    assert inc._stage_config({"llm": {"num_ctx": 8192}}, sys_len, "x" * (min_budget + 1 - sys_len),
                             inc.MAIN_JUDGE_PREDICT)["llm"]["num_ctx"] == inc.MID_AUTO_CONTEXT
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
                        "quote": "- Header\n  - Glasshouse vent sealed.",
                        "relevant": True, "supported": True, "reason": "ok"}],
        })

    monkeypatch.setattr(inc, "local_complete", fake_complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **kw: None)
    inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
    assert len(seen_calls) == 1
    _, prompt, num_predict = seen_calls[0]
    assert num_predict == inc.MAIN_JUDGE_PREDICT
    assert "without joining separate lines, bullets, or speaker turns with a space" in prompt
    assert "for a multiline span, preserve exact newlines, indentation, bullet markers, and prefixes" in prompt

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

    # Wikilinks with [[ in quote: canonicalize alias typography/spaces/NFD while keeping exact target
    nfd_link_src = unicodedata.normalize("NFD", "- [[Ядра/Маяк|Проект\u00a0—\u00a0Маяк]]: готово.")
    assert inc._quote_hits(nfd_link_src, "- [[Ядра/Маяк|Проект - Маяк]]: готово.") == 1
    assert inc._quote_hits(nfd_link_src, "- [[Чужой/Маяк|Проект - Маяк]]: готово.") == 0
    assert inc._quote_hits("- [[Ядра/Орбита|орбита]]: ok.", "- [[Ядра/Орбита]]: ok.") == 1
    assert inc._quote_hits("- [[Ядра/Орбита| ]]: ok.", "- [[Ядра/Орбита| ]]: ok.") == 1
    assert inc._quote_hits("- [[Ядра/Орбита#sec|Орбита]]: ok.", "- [[Ядра/Орбита#sec]]: ok.") == 0

    # Sentinel characters \ufdd0/\ufdd1/\ufdd2 cannot forge hidden/link matches
    for bad_q in ("s\ufdd2e\ufdd2c\ufdd2r\ufdd2e\ufdd2t", "a\ufdd0b", "a\ufdd1b"):
        assert inc._quote_hits("Код `secret` ок. a\ufdd0b a\ufdd1b", bad_q) == 0
    assert inc._quote_hits("x \ufdd0:0\ufdd1 y [[Ядра/А]]", "x [[Ядра/А]] y") == 0
    assert inc._quote_hits("x [[Ядра/А|\ufdd0:0\ufdd1]] y [[Ядра/Б]]", "x [[Ядра/Б]] y") == 0

    # Hidden-zone symmetry & duplicate detection
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

    # 1) Pure claim_quote_missing triggers exactly 1 bounded retry with correction_feedback and succeeds
    seen_prompts: list[str] = []
    audited: list[dict] = []
    replies = iter([
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "ok",
            "claims": [{"text": fact1, "source": f"[[{m1}]]",
                        "quote": "Valve pressure ... 4 bar.", "relevant": True, "supported": True, "reason": "ok"}],
        }),
        json.dumps({
            "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
            "reason": "fixed quote",
            "claims": [{"text": fact1, "source": m1,
                        "quote": fact1, "relevant": True, "supported": True, "reason": "ok"}],
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
    fb = payload_retry["correction_feedback"]
    assert fb["failure"] == "one or more claim quotes were not found verbatim in new_sources"
    assert fb["missing_quotes"] == [{"text": fact1, "source": m1, "quote": "Valve pressure ... 4 bar."}]
    assert "supported=false and verdict=reject" in fb["instruction"]
    assert len(audited) == 1 and audited[0]["quote"] == fact1

    # 2) Bounded retry: 2 consecutive misses stop after 2 calls and record claim_quote_missing
    calls_twice: list[str] = []
    bad_verdict = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [{"text": fact1, "source": m1,
                    "quote": "not in source", "relevant": True, "supported": True, "reason": "ok"}],
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

    # 3) Truncation limits: at most QUOTE_FEEDBACK_LIMIT items and QUOTE_FEEDBACK_CHARS per text/quote
    long_facts = [f"Fact{i} " + ("x" * 230) + "." for i in range(6)]
    long_body = BODY.replace("## Как пришли", "".join(f"- {f} [[{m1}]]\n" for f in long_facts) + "## Как пришли")
    long_files = {m1: {"text": "completely different text"}}
    long_prompts: list[str] = []
    long_verdict = json.dumps({
        "verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
        "reason": "ok",
        "claims": [
            {"text": f, "source": m1, "quote": f"Quote{i} " + ("y" * 230),
             "relevant": True, "supported": True, "reason": "ok"}
            for i, f in enumerate(long_facts)
        ],
    })
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (long_prompts.append(p), long_verdict)[1])
    with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
        inc.semantic_check("Glasshouse", loaded, [m1], long_files, LOCAL, long_body, [])
    assert len(long_prompts) == 2
    fb_long = json.loads(long_prompts[1].split("\n", 1)[0])["correction_feedback"]
    assert len(fb_long["missing_quotes"]) == inc.QUOTE_FEEDBACK_LIMIT
    assert all(len(item["text"]) == inc.QUOTE_FEEDBACK_CHARS for item in fb_long["missing_quotes"])
    assert all(len(item["quote"]) == inc.QUOTE_FEEDBACK_CHARS for item in fb_long["missing_quotes"])

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

    # 5) Critical invariant: missing quote on claim[0] NEVER triggers retry when ANY other check fails
    two_body = BODY.replace("## Как пришли", f"- {fact1} [[{m1}]]\n- {fact2} [[{m2}]]\n## Как пришли")
    ambig_files = {m1: {"text": f"Log: {fact1}"}, m2: {"text": f"{fact2}\n{fact2}"}}
    missing_c1 = {"text": fact1, "source": m1, "quote": "absent quote", "relevant": True, "supported": True, "reason": "ok"}
    for bad_claims, err_re, use_names, use_files, use_body in (
        ([missing_c1, {"text": fact2, "source": m2, "quote": fact2, "relevant": True, "supported": True, "reason": "ok"}],
         "ambiguous claim evidence", [m1, m2], ambig_files, two_body),
        ([missing_c1, {"text": "broken"}], "malformed candidate claim", [m1, m2], files, two_body),
        ([missing_c1, {"text": fact2, "source": m2, "quote": fact2, "relevant": True, "supported": False, "reason": "no"}],
         "not relevant and supported", [m1, m2], files, two_body),
        ([missing_c1, {"text": fact2, "source": "unknown", "quote": fact2, "relevant": True, "supported": True, "reason": "ok"}],
         "unsupported new source", [m1, m2], files, two_body),
        ([missing_c1, missing_c1], "duplicate candidate claim", [m1], files, body),
        ([missing_c1, {"text": "not in body", "source": m2, "quote": fact2, "relevant": True, "supported": True, "reason": "ok"}],
         "not an exact candidate span", [m1, m2], files, two_body),
        ([missing_c1], "uncovered new significant text", [m1, m2], files, two_body),
        ([missing_c1], "contribution", [m1, m2], files, body),
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

    # 6) Budget guard on retry: never exceed stage_c["llm"]["num_ctx"], and non-ollama config doesn't KeyError
    tight_calls: list[int] = []
    # Compute prompt size close to MIN_AUTO_CONTEXT so attempt 0 fits 8192 but attempt 1 (+ feedback) exceeds 8192
    sys_len = len(inc.MAIN_JUDGE_SYSTEM)
    min_budget = (inc.MIN_AUTO_CONTEXT - inc.MAIN_JUDGE_PREDICT - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN
    base_calls: list[str] = []
    monkeypatch.setattr(inc, "local_complete", lambda p, *a, **kw: (base_calls.append(p), bad_verdict)[1])
    with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
        inc.semantic_check("Glasshouse", loaded, [m1], files, LOCAL, body, [])
    pad_len = max(0, min_budget - sys_len - len(base_calls[0]) - 20)
    tight_files = {m1: {"text": f"Inspection log: {fact1} " + ("z" * pad_len)}}
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

"""Targeted tests for #990: inline emphasis quotes, [[source]] canonicalization,
context_budget sequential fallback, and closed stage_refusals telemetry."""
from __future__ import annotations

# Shared pytest fixtures are imported under their fixture names.
# ruff: noqa: F811

import json

import pytest

import dossier_incremental as inc
import dossier_telemetry as telemetry
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
    # Wikilink nested inside **...**
    assert inc._quote_hits(source, "По Платформа Альфа запущен тест.") == 1
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


@pytest.mark.parametrize("reply_text,expected_detail", [
    ("not json at all " + SENTINEL, "json_malformed"),
    (json.dumps({"verdict": "relevant", "reason": "ok"}), "json_schema"),
    (json.dumps({"verdict": "relevant", "reason": "   ", "quote": "x"}), "relevance_incomplete"),
    (json.dumps({"verdict": "relevant", "reason": "ok", "quote": "absent quote"}), "relevance_quote_missing"),
    (json.dumps({"verdict": "uncertain", "reason": "ambiguous", "quote": ""}), "relevance_uncertain"),
])
def test_stage_refusals_classifies_source_relevance_failures(world, monkeypatch, reply_text, expected_detail):
    nd, graph, _ = world
    _new(graph, "2099-01-02_100000", f"Glasshouse fact {SENTINEL}.")
    monkeypatch.setattr(inc, "local_complete", _seq_replies([reply_text]))

    result = nd.run_all([graph], LOCAL, full=False, dry=False)
    snap = result.telemetry.snapshot()
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

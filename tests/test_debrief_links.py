"""Synthetic contracts for chronological links; no model or storage required."""
import copy
import json
from collections import Counter
from time import perf_counter

import pytest

import debrief_links as links
import debrief_refinement as ref


def fixture(count=1):
    done = []
    for i in range(count + 1):
        text = f"Участник A: Для модуля {i} сначала предложили проверку."
        window = {"id": f"w{i}", "start": i * 100, "end": i * 100 + len(text), "text": text}
        row = {"text": text, "quotes": [text], "uncertain": False}
        done.append({"window": window, "rows": [row], "ids": [f"w{i}:p0"]})
    current = done.pop()
    return done, current


def relation(early="w0:p0", late="w1:p0"):
    return {"early_id": early, "late_id": late, "kind": "clarifies", "status": "candidate"}


def caller(found=None, audit_patch=None, source_patch=None, maximum=None, calls=None):
    def call(prompt, system, predict):
        data = json.loads(prompt)
        if calls is not None:
            calls.append((system, data))
        if system == links.ANCHOR_AUDIT_SYSTEM:
            value = {"verdict": "supported"}
        elif system == links.PAIR_AUDIT_SYSTEM:
            value = {**{key: data["relation"][key] for key in ("early_id", "late_id", "kind")},
                     "verdict": "supported",
                     "early_indices": [0], "late_indices": [0],
                     "early_quotes": [], "late_quotes": []}
            value.update(source_patch or {})
        else:
            ids = [row["id"] for row in data["early_paragraphs"]]
            if maximum is not None and len(ids) > maximum:
                raise ref.RefinementRefused("complete_input_exceeds_context")
            if system == links.DISCOVER_SYSTEM:
                value = {"checked_ids": ids, "relations": found or []}
            else:
                assert system == links.DISCOVERY_AUDIT_SYSTEM
                value = {"checked_ids": ids, "status": "complete", "missing_relations": [], "uncertain_ids": []}
                value.update(audit_patch or {})
        return json.dumps(value)
    return call


def run(call, count=1):
    done, current = fixture(count)
    return links.link_window(done, current["window"], current["rows"], call)


def verify(call, done=None, current=None, candidate=None):
    if done is None:
        done, current = fixture()
    return links._verify(candidate or relation(),
        (done[0]["rows"][0], done[0]["window"]), (current["rows"][0], current["window"]), call)


def discover(call):
    done, current = fixture()
    ids, late_ids = done[0]["ids"], current["ids"]
    inputs = {"early_paragraphs": [{"id": ids[0], "text": done[0]["rows"][0]["text"]}],
              "current_paragraphs": [{"id": late_ids[0], "text": current["rows"][0]["text"]}],
              "current_source": links._source(current["window"])}
    return links._discover(inputs, ids, late_ids, call)


def test_negative_discovery_audits_all_ids_without_source_calls_or_truncation():
    done, current = fixture(5)
    done[0]["rows"][0]["text"] *= 20
    calls = []
    assert links.link_window(done, current["window"], current["rows"], caller(maximum=2, calls=calls)) == []
    audited = [data for system, data in calls if system == links.DISCOVERY_AUDIT_SYSTEM]
    assert [row["id"] for data in audited for row in data["early_paragraphs"]] == [f"w{i}:p0" for i in range(5)]
    for system, data in calls:
        if system in (links.DISCOVER_SYSTEM, links.DISCOVERY_AUDIT_SYSTEM):
            assert all(set(row) == {"id", "text"} for row in data["early_paragraphs"])
            assert data["current_paragraphs"] == [{"id": current["ids"][0], "text": current["rows"][0]["text"]}]
            assert data["current_source"]["final_speech"] == current["window"]["text"]
    assert audited[0]["early_paragraphs"][0]["text"] == done[0]["rows"][0]["text"]
    assert all(system != links.PAIR_AUDIT_SYSTEM for system, _ in calls)


def test_missing_link_is_independently_verified_and_input_stays_immutable():
    done, current = fixture()
    snapshot = copy.deepcopy((done, current))
    calls = []
    result = links.link_window(done, current["window"], current["rows"], caller(
        audit_patch={"status": "incomplete", "missing_relations": [relation()]}, calls=calls))
    assert result == [{**relation(), "status": "supported"}]
    assert (done, current) == snapshot
    assert [system for system, _ in calls[-2:]] == [links.PAIR_AUDIT_SYSTEM, links.ANCHOR_AUDIT_SYSTEM]
    body = links.render_conversation(done + [current], result)
    assert "Позднее уточнение — фрагмент 2" in body and "Уточнение к фрагменту 1" in body
    assert body.count(current["rows"][0]["text"]) == 1
    assert body.index(done[0]["rows"][0]["text"]) < body.index(current["rows"][0]["text"])


def mixed_windows():
    done, current = fixture()
    for item in (done[0], current):
        original = item["rows"][0]
        uncertain = {**original, "text": "Спорный вывод.", "uncertain": True}
        item["rows"] = [uncertain, original]
        item["ids"].append(f"{item['window']['id']}:p1")
    return done, current


def test_uncertain_endpoints_keep_their_places_but_only_certain_pairs_reach_audits_and_notes():
    done, current = mixed_windows()
    valid = relation("w0:p1", "w1:p1")
    calls = []
    result = links.link_window(done, current["window"], current["rows"], caller(found=[valid], calls=calls))
    assert result == [{**valid, "status": "supported"}]
    assert [s for s, _ in calls] == [links.DISCOVER_SYSTEM, links.DISCOVERY_AUDIT_SYSTEM,
                                     links.PAIR_AUDIT_SYSTEM, links.ANCHOR_AUDIT_SYSTEM]
    for _, data in calls[:2]:
        assert [r["id"] for r in data["early_paragraphs"]] == ["w0:p1"]
        assert [r["id"] for r in data["current_paragraphs"]] == ["w1:p1"]
    # Even a supplied supported edge cannot attach a note to an uncertain row.
    forged = [{**result[0], "early_id": "w0:p0"}, {**result[0], "late_id": "w1:p0"}]
    body = links.render_conversation(done + [current], result + forged)
    assert body.count("Позднее уточнение") == body.count("Уточнение к фрагменту") == 1
    assert "Позднее уточнение — фрагмент 4." in body
    assert "Уточнение к фрагменту 2." in body
    for number, row in enumerate(done[0]["rows"] + current["rows"], 1):
        marker = "**Требует уточнения.** " if row["uncertain"] else ""
        assert f"{number}. {marker}{row['text']}" in body


@pytest.mark.parametrize("candidate", [relation("w0:p0", "w1:p1"), relation("w0:p1", "w1:p0")])
def test_discovery_cannot_nominate_excluded_uncertain_id(candidate):
    done, current = mixed_windows()
    calls = []
    assert links.link_window(done, current["window"], current["rows"], caller(found=[candidate], calls=calls)) == []
    assert [system for system, _ in calls] == [links.DISCOVER_SYSTEM]


@pytest.mark.parametrize("side", ["early", "late"])
def test_all_uncertain_endpoints_need_no_requests(side):
    done, current = fixture()
    owner = done[0] if side == "early" else current
    owner["rows"][0]["uncertain"] = True
    calls = []
    assert links.link_window(done, current["window"], current["rows"], caller(calls=calls)) == []
    assert calls == []


def test_bad_first_batch_does_not_hide_later_audited_candidate():
    done, current = fixture(4)
    candidate = relation("w3:p0", "w4:p0")
    calls = []
    normal = caller(maximum=2, calls=calls)

    def call(prompt, system, predict):
        response = json.loads(normal(prompt, system, predict))
        if system == links.DISCOVERY_AUDIT_SYSTEM:
            if candidate["early_id"] in response["checked_ids"]:
                response.update(status="incomplete", missing_relations=[candidate])
            else:
                response["checked_ids"] = []
        return json.dumps(response)

    assert links.link_window(done, current["window"], current["rows"], call) == [{**candidate, "status": "supported"}]
    audited = [row["id"] for system, payload in calls if system == links.DISCOVERY_AUDIT_SYSTEM
               for row in payload["early_paragraphs"]]
    assert audited == [f"w{i}:p0" for i in range(4)]
    assert [s for s, _ in calls[-2:]] == [links.PAIR_AUDIT_SYSTEM, links.ANCHOR_AUDIT_SYSTEM]


def long_synthetic_link_metrics():
    """Compare revisions with identical evidence, replies and 8192-token admission."""
    done, counts = [], Counter()
    source_chars = refused = 0
    expected = {**relation("w5:p5", "w7:p3"), "kind": "answers"}
    stage = {links.DISCOVER_SYSTEM: "discover", links.DISCOVERY_AUDIT_SYSTEM: "discovery_audit",
             links.PAIR_AUDIT_SYSTEM: "source_audit", links.ANCHOR_AUDIT_SYSTEM: "anchor_audit"}
    started = perf_counter()
    for i in range(8):
        rows = []
        for j in range(6):
            key = f"w{i}:p{j}"
            summary = f"Образец {i}-{j} принят на испытание."
            if key == expected["early_id"]:
                summary = "На какой площадке испытают синий образец?"
            elif key == expected["late_id"]:
                summary = "Синий образец испытают на северной площадке."
            quote = (f"Докладчик: {summary} " +
                     " ".join(f"В журнале образца {i}-{j} отдельно записано наблюдение номер {n}."
                              for n in range(6)))
            rows.append({"text": summary, "quotes": [quote], "uncertain": False})
        source = "\n".join(r["quotes"][0] for r in rows)
        window = {"id": f"w{i}", "start": source_chars, "end": source_chars + len(source), "text": source}
        source_chars += len(source)
        seen = {"discover": [], "discovery_audit": []}

        def call(prompt, system, predict):
            nonlocal refused
            limit = (8192 - predict - ref.CONTEXT_RESERVE) * ref.CHARS_PER_TOKEN
            if len(prompt) + len(system) > limit:
                refused += 1
                raise ref.RefinementRefused("complete_input_exceeds_context")
            name = stage[system]
            counts[name] += 1
            if name in seen:
                payload = json.loads(prompt)
                ids = [entry["id"] for entry in payload["early_paragraphs"]]
                seen[name].extend(ids)
                target = [entry["id"] for entry in payload["current_paragraphs"]]
                if expected["early_id"] in ids and expected["late_id"] in target:
                    return caller(found=[expected])(prompt, system, predict)
            return caller()(prompt, system, predict)

        result = links.link_window(done, window, rows, call)
        assert result == ([{**expected, "status": "supported"}] if i == 7 else [])
        prior = [key for item in done for key in item["ids"]]
        # An audit overflow may replay discovery on both halves of a batch.
        assert list(dict.fromkeys(seen["discover"])) == seen["discovery_audit"] == prior
        done.append({"window": window, "rows": rows, "ids": [f"w{i}:p{j}" for j in range(len(rows))]})
    return {"calls": dict(counts), "total_calls": sum(counts.values()), "admission_refusals": refused,
            "source_chars": source_chars, "scripted_seconds": perf_counter() - started}


def test_long_discovery_fits_seven_batches_and_checks_the_supported_pair_twice():
    metrics = long_synthetic_link_metrics()
    assert metrics["calls"] == {"discover": 7, "discovery_audit": 7, "source_audit": 1, "anchor_audit": 1}
    assert metrics["admission_refusals"] == 0


@pytest.mark.parametrize("patch", [
    {"checked_ids": []}, {"checked_ids": ["w0:p0", "w0:p0"]}, {"status": []},
    {"status": "incomplete"}, {"status": "uncertain"}, {"extra": True},
    {"uncertain_ids": [["w0:p0"]]}, {"uncertain_ids": ["foreign"]},
    {"missing_relations": None},
])
def test_invalid_discovery_audit_refuses(patch):
    with pytest.raises(ref.RefinementRefused):
        discover(caller(audit_patch=patch))
    assert run(caller(audit_patch=patch)) == []


@pytest.mark.parametrize("patch", [
    {"early_id": []}, {"early_id": "foreign"}, {"late_id": "w0:p0"},
    {"kind": "continues"}, {"kind": {}}, {"status": True}, {"note": "invented"},
])
def test_invalid_relation_refuses_before_source_audit(patch):
    calls = []
    with pytest.raises(ref.RefinementRefused):
        discover(caller(found=[{**relation(), **patch}], calls=calls))
    assert run(caller(found=[{**relation(), **patch}])) == []
    assert all(system != links.PAIR_AUDIT_SYSTEM for system, _ in calls)


def test_unknown_late_target_is_not_published():
    result = run(caller(audit_patch={"status": "uncertain", "uncertain_ids": ["w0:p0"]}))
    assert result == []
    done, current = fixture()
    assert links.render_conversation(done + [current], result) == ref._render(
        [done[0]["rows"][0], current["rows"][0]])


@pytest.mark.parametrize("verdict", ["unsupported", "uncertain"])
def test_unproved_source_verdict_is_not_published(verdict):
    result = run(caller(found=[relation()], source_patch={"verdict": verdict, "early_quotes": [], "late_quotes": []}))
    assert result == []


@pytest.mark.parametrize("patch", [
    {"early_indices": []}, {"early_quotes": ["Чужая цитата без оснований."]},
    {"early_quotes": [12]}, {"verdict": []}, {"kind": "answers"}, {"extra": False},
])
def test_source_audit_closed_schema_and_quotes(patch):
    with pytest.raises(ref.RefinementRefused):
        verify(caller(found=[relation()], source_patch=patch))
    assert run(caller(found=[relation()], source_patch=patch)) == []


def test_late_quote_cannot_be_used_as_early_evidence_or_neighbor_only():
    done, current = fixture()
    late_quote = current["window"]["text"]
    done[0]["window"]["context_after"] = late_quote
    with pytest.raises(ref.RefinementRefused, match="link_source_quotes"):
        verify(caller(found=[relation()], source_patch={"early_quotes": [late_quote]}), done, current)
    assert links.link_window(done, current["window"], current["rows"],
        caller(found=[relation()], source_patch={"early_quotes": [late_quote]})) == []


def test_single_pair_context_failure_propagates_without_lossy_retry():
    calls = []
    normal = caller(found=[relation()])
    def call(prompt, system, predict):
        calls.append(system)
        if system == links.PAIR_AUDIT_SYSTEM:
            raise ref.RefinementRefused("complete_input_exceeds_context")
        return normal(prompt, system, predict)
    with pytest.raises(ref.RefinementRefused, match="complete_input_exceeds_context"):
        run(call)
    assert calls.count(links.PAIR_AUDIT_SYSTEM) == 1


def test_without_links_render_matches_original_and_has_no_numbering():
    done, current = fixture()
    assert links.render_conversation(done + [current], []) == ref._render([done[0]["rows"][0], current["rows"][0]])


@pytest.mark.parametrize("patch", [{"checked_ids": []}, {"relations": {}}, {"extra": True}])
def test_discovery_schema_and_coverage_refuse_before_audit(patch):
    calls = []
    def call(prompt, system, predict):
        calls.append(system)
        return json.dumps({"checked_ids": ["w0:p0"], "relations": [], **patch})
    with pytest.raises(ref.RefinementRefused):
        discover(call)
    assert calls == [links.DISCOVER_SYSTEM]
    assert run(call) == []


def test_audit_overflow_splits_and_rechecks_each_id():
    normal = caller()
    audited = []
    def call(prompt, system, predict):
        data = json.loads(prompt)
        if system == links.DISCOVERY_AUDIT_SYSTEM:
            ids = [row["id"] for row in data["early_paragraphs"]]
            if len(ids) > 1:
                raise ref.RefinementRefused("output_budget")
            audited.extend(ids)
        return normal(prompt, system, predict)
    assert run(call, count=3) == []
    assert audited == ["w0:p0", "w1:p0", "w2:p0"]


def test_repeated_owned_quote_cannot_choose_occurrence_without_uncertainty():
    done, current = fixture()
    quote = done[0]["window"]["text"]
    done[0]["window"]["text"] += "\n" + quote
    with pytest.raises(ref.RefinementRefused, match="link_ambiguous_source_anchor"):
        verify(caller(found=[relation()], source_patch={"early_quotes": [quote]}), done, current)
    assert links.link_window(done, current["window"], current["rows"],
        caller(found=[relation()], source_patch={"early_quotes": [quote]})) == []


@pytest.mark.parametrize("side", ["early", "late"])
def test_quote_from_another_paragraph_in_same_window_cannot_support_selected_id(side):
    done, current = fixture()
    owner = done[0] if side == "early" else current
    other = "Участник B: Для другого предмета срок назначен на пятницу."
    owner["window"]["text"] += "\n" + other
    owner["window"]["end"] += len(other) + 1
    with pytest.raises(ref.RefinementRefused, match="link_paragraph_anchor"):
        verify(caller(found=[relation()], source_patch={side + "_quotes": [other]}), done, current)
    assert links.link_window(done, current["window"], current["rows"],
        caller(found=[relation()], source_patch={side + "_quotes": [other]})) == []


def test_expanded_quote_preserves_original_paragraph_anchor():
    done, current = fixture()
    anchor = done[0]["rows"][0]["quotes"][0]
    done[0]["window"]["text"] = "Условие обсуждения: " + anchor + " Только после приёмки."
    result = links.link_window(done, current["window"], current["rows"], caller(found=[relation()]))
    assert result[0]["status"] == "supported"


@pytest.mark.parametrize("verdict", ["unsupported", "uncertain", "supported", "invalid"])
def test_anchor_challenge_cannot_borrow_an_adjacent_answer(verdict):
    done, current = fixture()
    first, last = done[0]["rows"][0], current["rows"][0]
    current["window"]["text"] += " Ответ из соседнего абзаца."
    normal = caller(found=[{**relation(), "kind": "answers"}])

    def call(prompt, system, predict):
        if system == links.ANCHOR_AUDIT_SYSTEM:
            assert json.loads(prompt) == {"kind": "answers", "early_quotes": first["quotes"],
                                          "late_quotes": last["quotes"]}
            return json.dumps({"verdict": verdict})
        return normal(prompt, system, predict)

    if verdict == "invalid":
        with pytest.raises(ref.RefinementRefused, match="link_anchor_verdict"):
            verify(call, done, current, {**relation(), "kind": "answers"})
        assert links.link_window(done, current["window"], current["rows"], call) == []
    else:
        result = links.link_window(done, current["window"], current["rows"], call)
        assert [link["status"] for link in result] == (["supported"] if verdict == "supported" else [])


@pytest.mark.parametrize("indices", [[True], [-1], [1], [0, 0], ["0"], None, [{}]])
def test_source_indices_are_closed_and_bound_to_selected_paragraph(indices):
    with pytest.raises(ref.RefinementRefused, match="link_source_indices"):
        verify(caller(found=[relation()], source_patch={"early_indices": indices}))
    assert run(caller(found=[relation()], source_patch={"early_indices": indices})) == []


def test_uncertain_full_source_relation_is_omitted_without_anchor_request():
    calls = []
    result = run(caller(found=[relation()], source_patch={"verdict": "uncertain"}, calls=calls))
    assert result == []
    assert links.PAIR_AUDIT_SYSTEM in [system for system, _ in calls]
    assert links.ANCHOR_AUDIT_SYSTEM not in [system for system, _ in calls]


def test_repeated_anchor_needs_exact_unique_expansion():
    done, current = fixture()
    anchor = done[0]["rows"][0]["quotes"][0]
    expanded = "Первое предложение: " + anchor
    done[0]["window"]["text"] = expanded + "\nПовтор: " + anchor
    result = links.link_window(done, current["window"], current["rows"],
        caller(found=[relation()], source_patch={"early_quotes": [expanded]}))
    assert result[0]["status"] == "supported"


def test_schema_matches_explicit_indexed_evidence_contract():
    inputs = {"relation": relation(), "early_anchors": [{"index": 0, "quote": "x"}],
              "late_anchors": [{"index": 0, "quote": "y"}]}
    schema = links.audit_schema(links.PAIR_AUDIT_SYSTEM, inputs)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["early_indices"]["items"]["maximum"] == 0
    assert schema["properties"]["early_indices"]["uniqueItems"] is True
    assert links.audit_schema(links.ANCHOR_AUDIT_SYSTEM, {})["required"] == ["verdict"]


@pytest.mark.parametrize("repeated", [None, "early", "late"])
def test_pair_schema_locks_only_sides_with_unique_owned_anchors(repeated):
    done, current = fixture()
    owners = {"early": done[0], "late": current}
    for side, owner in owners.items():
        # Multiple independent anchors reproduce the 3/2-anchor shape without
        # copying any real speech. Preserve the newline between each quote.
        count = 3 if side == "early" else 2
        quotes = [f"Участник A: Предмет {side} номер {i} пока обсуждается." for i in range(count)]
        owner["rows"][0]["quotes"] = quotes
        owner["window"]["text"] = "\n".join(quotes)
    if repeated:
        owner = owners[repeated]
        owner["window"]["text"] += "\n" + owner["rows"][0]["quotes"][0]
    calls = []
    links.link_window(done, current["window"], current["rows"],
        caller(found=[relation()], source_patch={"verdict": "uncertain"}, calls=calls))
    inputs = next(data for system, data in calls if system == links.PAIR_AUDIT_SYSTEM)
    schema = links.audit_schema(links.PAIR_AUDIT_SYSTEM, inputs)
    for side in owners:
        field = schema["properties"][side + "_quotes"]
        if side == repeated:
            assert field == {"type": "array", "items": {"type": "string", "minLength": 12}}
        else:
            assert field == {"const": []}
        assert schema["properties"][side + "_indices"]["uniqueItems"] is True
        assert side + "_indices" in schema["required"]
        assert inputs[side + "_source"]["final_speech"] == owners[side]["window"]["text"]


def test_pair_schema_does_not_lock_neighbor_only_anchors():
    quote = "Соседняя реплика не принадлежит текущему окну."
    source = {"final_speech": "Собственная речь о другом предмете.",
              "neighbor_context": {"before": quote, "after": ""}}
    assert not links._unique_owned_anchors([{"index": 0, "quote": quote}], source)


def test_locked_optional_quotes_do_not_skip_semantic_or_evidence_checks():
    calls = []
    result = run(caller(found=[relation()], calls=calls))
    assert result[0]["status"] == "supported"
    assert [system for system, _ in calls[-2:]] == [links.PAIR_AUDIT_SYSTEM, links.ANCHOR_AUDIT_SYSTEM]
    with pytest.raises(ref.RefinementRefused, match="link_source_quotes"):
        verify(caller(found=[relation()], source_patch={"late_quotes": ["Склеенная выдуманная реплика."]}))
    assert run(caller(found=[relation()], source_patch={"late_quotes": ["Склеенная выдуманная реплика."]})) == []
    assert run(caller(found=[relation()], source_patch={"verdict": "unsupported"})) == []


@pytest.mark.parametrize("middle", ["invalid", "focused_uncertain", "focused_unsupported"])
def test_bad_middle_pair_does_not_erase_supported_neighbors(middle):
    done, current = fixture(3)
    candidates = [relation(f"w{i}:p0", "w3:p0") for i in range(3)]
    normal = caller(found=candidates)
    focused = []
    source_ids = []

    def call(prompt, system, predict):
        data = json.loads(prompt)
        if system == links.PAIR_AUDIT_SYSTEM:
            early_id = data["relation"]["early_id"]
            source_ids.append(early_id)
            if early_id == "w1:p0" and middle == "invalid":
                return json.dumps({"early_id": early_id, "late_id": "w3:p0", "kind": "clarifies",
                    "verdict": "supported", "early_indices": [999], "late_indices": [0],
                    "early_quotes": [], "late_quotes": []})
        if system == links.ANCHOR_AUDIT_SYSTEM:
            focused.append(data["early_quotes"])
            if data["early_quotes"] == done[1]["rows"][0]["quotes"]:
                return json.dumps({"verdict": middle.removeprefix("focused_")})
        return normal(prompt, system, predict)

    result = links.link_window(done, current["window"], current["rows"], call)
    assert result == [{**candidates[i], "status": "supported"} for i in (0, 2)]
    assert source_ids == ["w0:p0", "w1:p0", "w2:p0"]
    assert done[0]["rows"][0]["quotes"] in focused
    assert done[2]["rows"][0]["quotes"] in focused
    body = links.render_conversation(done + [current], result)
    assert "Уточнение к фрагменту 1." in body and "Уточнение к фрагменту 3." in body
    assert "Уточнение к фрагменту 2." not in body
    assert "Возможная связь" not in body and "999" not in body


@pytest.mark.parametrize("stage", [links.DISCOVER_SYSTEM, links.DISCOVERY_AUDIT_SYSTEM,
                                    links.PAIR_AUDIT_SYSTEM, links.ANCHOR_AUDIT_SYSTEM])
@pytest.mark.parametrize("reason", ["local_request_failed", "request_deferred", "model_request_budget",
                                    "complete_input_exceeds_context", "output_budget"])
def test_system_failures_are_never_suppressed_as_bad_model_links(stage, reason):
    normal = caller(found=[relation()])
    def call(prompt, system, predict):
        if system == stage:
            raise ref.RefinementRefused(reason)
        return normal(prompt, system, predict)
    with pytest.raises(ref.RefinementRefused, match=reason):
        run(call)


def test_duplicate_input_identity_is_not_a_discardable_model_reply():
    done, current = fixture()
    done.append(copy.deepcopy(done[0]))
    calls = []
    with pytest.raises(ref.RefinementRefused, match="link_input_identity"):
        links.link_window(done, current["window"], current["rows"], caller(calls=calls))
    assert calls == []


def test_bad_discovery_reply_discards_its_batch_without_source_calls():
    calls = []
    assert run(caller(audit_patch={"checked_ids": []}, calls=calls), count=3) == []
    assert links.PAIR_AUDIT_SYSTEM not in [system for system, _ in calls]

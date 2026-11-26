"""Stop export → checked conclusion → published derivative, with synthetic speech."""
import json

import pytest

import debrief_links as links
import debrief_pipeline as pipeline
import debrief_refinement as ref
from test_debrief_pipeline import make_meeting, previous_debrief, run

EARLY = "Предварительно выставку откроем в среду."
LATE = "Открытие выставки переносим на пятницу после проверки зала."
OWNER = "Каталог подготовлю я, имя пока не называем."
THREAD = "Выставка в среду. Кажется, каталог поручили Ольге. Уточнить дату."
FINAL = "Выставку откроют в пятницу после проверки зала."
OPEN = "Каталог обещали подготовить. Имя исполнителя не подтверждено."


def paragraph(text, quotes, uncertain=False):
    return {"text": text, "quotes": quotes, "uncertain": uncertain}


def verdict(rows):
    return {"paragraphs": [{"index": i, "supported": True} for i in range(len(rows))],
            "coverage_complete": True, "corrections_resolved": True}


@pytest.mark.parametrize("long_meeting,unresolved", [(False, False), (True, False), (True, True)])
def test_publication_requires_resolved_correction_and_marks_unknown_owner(tmp_path, monkeypatch, long_meeting, unresolved):
    width = ref.WINDOW_CHARS - 1 if long_meeting else 0
    speech = "".join(line.ljust(width - int(long_meeting and i == 0)) + "\n" for i, line in enumerate((EARLY, OWNER, LATE)))
    live, handoff = make_meeting(tmp_path, speech=speech, thread=THREAD)
    original = live.read_bytes()
    final = [paragraph(FINAL, [EARLY, LATE]), paragraph(OPEN, [OWNER], True)]
    audit_inputs = []

    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        if system == ref.SYSTEM or system == links.CONCLUSION_SYSTEM:
            assert [item["text"] for item in data["live_thread"]] == [THREAD]
            if system == links.CONCLUSION_SYSTEM:
                assert predict == 2000
                assert {q for row in data["verified"] for q in row["quotes"]} == {EARLY, LATE, OWNER}
            return json.dumps({"paragraphs": final})
        if system in (ref.AUDIT_SYSTEM, links.CONCLUSION_AUDIT_SYSTEM):
            assert predict == 1000
            audit_inputs.append(data)
            return json.dumps({**verdict(data["paragraphs"]), "corrections_resolved": not unresolved})
        if system == ref.WINDOW_SYSTEM:
            owned = data["final_speech"].strip()
            row = {EARLY: paragraph("Сначала открытие наметили на среду.", [EARLY]),
                   LATE: paragraph(FINAL, [LATE]), OWNER: final[1]}[owned]
            return json.dumps({"paragraphs": [row]})
        if system == ref.WINDOW_AUDIT_SYSTEM:
            return json.dumps({**verdict(data["paragraphs"]), "correction_quotes": [],
                               "has_cross_window_correction": False})
        ids = [row["id"] for row in data["early_paragraphs"]]
        if system == links.DISCOVER_SYSTEM:
            return json.dumps({"checked_ids": ids, "relations": []})
        assert system == links.DISCOVERY_AUDIT_SYSTEM
        return json.dumps({"checked_ids": ids, "status": "complete", "missing_relations": [], "uncertain_ids": []})

    monkeypatch.setattr(ref, "local_request", request)
    if unresolved:
        destination, previous = previous_debrief(live)
        with pytest.raises(ref.UnverifiedReply, match="audit_refused"):
            run(live, handoff)
        assert audit_inputs and destination.read_bytes() == previous
        assert live.read_bytes() == original and not pipeline.publication_path(live).exists()
        return
    destination = run(live, handoff)
    assert pipeline.verify_publication(live) == destination
    assert live.read_bytes() == original
    body = destination.read_text()
    assert ref.PARTIAL_NOTE not in body
    assert body.count(FINAL) == 1 and "**Требует уточнения.** " + OPEN in body
    assert not any(value in body for value in (EARLY, LATE, OWNER, "Ольг", "среду", "**Из стенограммы**"))
    assert len(audit_inputs) == 1 and audit_inputs[0]["paragraphs"] == final


def test_no_verified_conclusion_preserves_existing_derivative(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination, original = previous_debrief(live)
    monkeypatch.setattr(ref, "local_request", lambda *args: '{"paragraphs":[]}')
    with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
        run(live, handoff)
    assert destination.read_bytes() == original
    assert not pipeline.publication_path(live).exists()


def test_partial_publication_keeps_only_supported_material(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path, speech=LATE + "\n" + OWNER, thread=THREAD)
    original = live.read_bytes()
    confirmed = paragraph(FINAL, [LATE])
    unknown = paragraph(OPEN, [OWNER], True)
    invented = paragraph("Каталог подготовит Ольга.", [OWNER])
    rejected = {**verdict([confirmed, unknown, invented]), "paragraphs": [
        {"index": i, "supported": i != 2} for i in range(3)]}

    def request(cfg, prompt, system, predict):
        if system in (ref.SYSTEM, ref.WINDOW_SYSTEM):
            return json.dumps({"paragraphs": [confirmed, unknown, invented]})
        if system == ref.AUDIT_SYSTEM:
            return json.dumps(rejected)
        if system == ref.WINDOW_AUDIT_SYSTEM:
            return json.dumps({**rejected, "has_cross_window_correction": False,
                               "correction_quotes": [], "repair_quotes": [OWNER]})
        if system == links.CONCLUSION_SYSTEM:
            data = json.loads(prompt)
            assert data["partial"] and [r["text"] for r in data["verified"]] == [FINAL, OPEN]
            return json.dumps({"paragraphs": [confirmed, unknown]})
        if system == links.CONCLUSION_AUDIT_SYSTEM:
            return json.dumps(verdict([confirmed, unknown]))
        assert system.startswith(ref.WINDOW_REPAIR_SYSTEM)
        return json.dumps({"changes": [paragraph("Неверная правка.", ["Неподтверждённый фрагмент."])]})

    monkeypatch.setattr(ref, "local_request", request)
    body = run(live, handoff).read_text()
    assert live.read_bytes() == original
    assert body.count(FINAL) == 1 and "**Требует уточнения.** " + OPEN in body
    assert "Ольга" not in body and "Ольге" not in body and OWNER not in body
    assert ref.PARTIAL_NOTE in body and "Из стенограммы" not in body


@pytest.mark.parametrize("failure", ["unverified_quote", "uncertainty", "audit", "context", "transport"])
def test_final_edit_failure_cannot_add_a_fact_or_hide_uncertainty(failure):
    certain = paragraph(FINAL, [LATE])
    uncertain = paragraph(OPEN, [OWNER], True)
    done = [{"ids": [str(i)], "rows": [row], "window": {}, "reason": "verified"}
            for i, row in enumerate((certain, uncertain))]
    calls = []
    def call(prompt, system, predict):
        calls.append(system)
        if failure in ("context", "transport"):
            raise ref.RefinementRefused("complete_input_exceeds_context" if failure == "context" else "local_request_failed")
        if system == links.CONCLUSION_SYSTEM:
            quote = { "unverified_quote": OWNER + " " + LATE, "uncertainty": OWNER, "audit": LATE }[failure]
            return json.dumps({"paragraphs": [paragraph("Выдуманное назначение.", [quote])]})
        assert system == links.CONCLUSION_AUDIT_SYSTEM
        return json.dumps({**verdict([certain]), "coverage_complete": False})

    with pytest.raises(ref.RefinementRefused):
        links.render_conversation(done, [], [], call)
    assert len(calls) == (2 if failure == "audit" else 1)


def test_audits_receive_speakers_bound_to_their_own_evidence():
    question = "Кто проверит подписи к экспонатам?"
    answer = "Я возьму проверку подписей."
    first = "**Координатор** [12:00]:\n" + question + "\n"
    speech = first + "**Собеседник** [12:01]:\n" + answer + "\n" + question
    rows = [paragraph("Проверку обещал собеседник.", [answer, question])]
    expected = {answer: ["Собеседник"], question: ["Координатор", "Собеседник"]}
    assert ref._quote_speakers(speech, rows) == {"quote_speakers": expected}
    assert ref._quote_speakers(answer, rows, "Собеседник")["quote_speakers"][answer] == ["Собеседник"]
    assert ref._quote_speakers(answer, rows) == {}
    across = question + "\n**Собеседник** [12:01]:\n" + answer
    assert ref._quote_speakers(first + "**Собеседник** [12:01]:\n" + answer,
                               [paragraph("Два участника.", [across])])["quote_speakers"][across] == ["Координатор", "Собеседник"]


    prompts = {}

    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        if system in (ref.SYSTEM, ref.WINDOW_SYSTEM):
            return json.dumps({"paragraphs": rows}, ensure_ascii=False)
        prompts[system] = data
        if system == ref.AUDIT_SYSTEM:
            return json.dumps(verdict(rows))
        assert system == ref.WINDOW_AUDIT_SYSTEM
        return json.dumps({**verdict(rows), "has_cross_window_correction": False,
                           "correction_quotes": [], "repair_quotes": []})

    ref.refine(speech, [], "", {}, request=request)
    # Only the answer belongs to this window. Its question is available as context.
    owned = "**Собеседник** [12:01]:\n" + answer
    window = {"start": len(first), "text": owned, "context_before": first, "context_after": "",
              "speaker_at_start": "Собеседник", "context_speaker": "Координатор"}
    assert ref._refine_window(window, [], "", lambda p, s, n: request({}, p, s, n))[0] == rows
    assert prompts[ref.AUDIT_SYSTEM]["quote_speakers"] == expected
    assert prompts[ref.WINDOW_AUDIT_SYSTEM]["quote_speakers"] == {answer: ["Собеседник"], question: ["Координатор"]}


def test_repeated_turns_keep_the_complete_source_together_for_draft_and_audit(monkeypatch):
    early = "**Host** [09:00]:\n" + EARLY + "\n"
    late = "**Host** [10:01]:\n" + LATE
    routine = "**Guest** [09:01]:\nThe microphone is ready.\n"
    speech = "Before the first speaker\n" + early + routine * 128 + late
    monkeypatch.setattr(ref, "MAX_WINDOWS", 1)
    monkeypatch.setattr(ref, "WINDOW_CHARS", len(speech))
    stages = []

    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        source = data["final_speech"]
        assert source["turns"] == [early, routine, late]
        assert source["order"] == [0] + [1] * 128 + [2]
        assert source["prefix"] + "".join(source["turns"][i] for i in source["order"]) == speech
        assert ref._output_schema(system, prompt) is not None
        stages.append(predict)
        if predict == 3000:
            return json.dumps({"paragraphs": [paragraph(FINAL, [EARLY, LATE])]})
        assert data["quote_speakers"] == {EARLY: ["Host"], LATE: ["Host"]}
        return json.dumps(verdict(data["paragraphs"]))

    result = ref.refine(speech, [{"text": THREAD}], "", {"llm": {"num_ctx": 6200}}, request=request)
    assert stages == [3000, 1000]
    assert FINAL in result and ref.PARTIAL_NOTE not in result

    # One additional character exceeds the source budget even when turns repeat.
    with pytest.raises(ref.RefinementRefused, match="source_window_budget"):
        ref.refine(speech + "!", [{"text": THREAD}], "", {"llm": {"num_ctx": 6200}}, request=request)
    assert stages == [3000, 1000]


@pytest.mark.parametrize("source", [
    "plain text without speaker headers",
    "**Guest** [09:00]:\nA routine answer.\n**Guest** [09:01]:\nA routine answer.\n",
])
def test_indexing_keeps_distinct_timestamps_and_ignores_unstructured_input(source):
    assert ref._indexed_speech(source) is None


def test_full_text_at_the_model_budget_stays_plain():
    turn = "**Guest** [09:00]:\n" + EARLY + "\n"
    speech = turn * 10 + "**Host** [09:10]:\n" + LATE
    thread = [{"text": THREAD}]
    prompt = json.dumps({"final_speech": speech, "live_thread": thread, "previous_debrief": ""}, ensure_ascii=False)
    length = len(ref.SYSTEM) + len(prompt)
    padding = -length % ref.CHARS_PER_TOKEN
    speech += " " * padding
    context = 3000 + ref.CONTEXT_RESERVE + (length + padding) // ref.CHARS_PER_TOKEN
    requests = []

    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        assert data["final_speech"] == speech
        requests.append(predict)
        if predict == 3000:
            return json.dumps({"paragraphs": [paragraph(FINAL, [LATE])]})
        return json.dumps(verdict(data["paragraphs"]))

    assert FINAL in ref.refine(speech, thread, "", {"llm": {"num_ctx": context}}, request=request)
    assert requests == [3000, 1000]

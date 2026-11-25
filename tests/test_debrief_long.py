"""Chronological window contracts on synthetic inputs; fake audits do not prove model quality."""
import json
import re

import pytest

import debrief_refinement as ref
import debrief_links as links



class ScriptedModel:
    def __init__(self, correction=None):
        self.calls, self.accepted_sources = [], []
        self.correction, self.first_id = correction, None

    def __call__(self, cfg, prompt, system, predict):
        value = json.loads(prompt)
        self.calls.append((system, value))
        if system.startswith(ref.WINDOW_SYSTEM):
            source = value["final_speech"]
            self.accepted_sources.append(source)
            source_id = value["source_window"]["id"]
            self.first_id = self.first_id or source_id + ":p0"
            quote = next(line for line in source.splitlines() if len(line.strip()) >= 12)[:120]
            result = {"paragraphs": [{"text": f"Обсуждение {source_id} на этом этапе.",
                                      "quotes": [quote], "uncertain": False}]}
        elif system.startswith(ref.WINDOW_REPAIR_SYSTEM):
            result = {"changes": []}
        elif system == ref.WINDOW_AUDIT_SYSTEM:
            present = bool(self.correction and self.correction in value["final_speech"])
            result = {**audit(len(value["paragraphs"])), "has_cross_window_correction": present,
                      "correction_quotes": [self.correction] if present else []}
        elif system == links.DISCOVER_SYSTEM:
            ids = [item["id"] for item in value["early_paragraphs"]]
            relations = []
            if (self.correction and self.correction in value["current_source"]["final_speech"]
                    and self.first_id in ids and value["current_paragraphs"]):
                relations = [{"early_id": self.first_id, "late_id": value["current_paragraphs"][0]["id"],
                              "kind": "clarifies", "status": "candidate"}]
            result = {"checked_ids": ids, "relations": relations}
        elif system == links.DISCOVERY_AUDIT_SYSTEM:
            result = {"checked_ids": [item["id"] for item in value["early_paragraphs"]],
                      "status": "complete", "missing_relations": [], "uncertain_ids": []}
        elif system == links.ANCHOR_AUDIT_SYSTEM:
            result = {"verdict": "supported"}
        elif system == links.PAIR_AUDIT_SYSTEM:
            result = {key: value["relation"][key] for key in ("early_id", "late_id", "kind")}
            result.update(verdict="supported", early_indices=[0], late_indices=[0],
                          early_quotes=[], late_quotes=[])
        elif system == links.CONCLUSION_SYSTEM:
            result = {"paragraphs": [{k: row[k] for k in ("text", "quotes", "uncertain")}
                                     for row in value["verified"]]}
        elif system == links.CONCLUSION_AUDIT_SYSTEM:
            result = audit(len(value["paragraphs"]))
        else:
            raise AssertionError("unexpected short-path request")
        return json.dumps(result, ensure_ascii=False)


def verbatim_slices(body):
    blocks = re.finditer(r"^\*\*Из стенограммы\*\*\n\n(?P<fence>`{3,})text\n(?P<source>.*?)\n(?P=fence)$",
                         body, flags=re.MULTILINE | re.DOTALL)
    slices = []
    for block in blocks:
        source = block["source"]
        assert all(len(run) < len(block["fence"]) for run in re.findall(r"`+", source))
        slices.append(source)
    return slices


def speech_lines(count=220):
    return "".join(f"Участник A: Тема номер {i:04d}. Решение пока открыто, требуется обсуждение.\n"
                   for i in range(count))


def audit(count):
    return {"paragraphs": [{"index": i, "supported": True} for i in range(count)],
            "coverage_complete": True, "corrections_resolved": True}


def test_long_source_visits_every_character_and_checks_all_prior_paragraphs():
    source = speech_lines()
    model = ScriptedModel()
    gates = []
    events = []
    result = ref.refine(source, [], "", {}, request=model,
                        before_request=lambda: gates.append(1), progress=events.append)
    assert "".join(model.accepted_sources) == source
    assert len(model.accepted_sources) > 2
    assert len(model.calls) == len(gates)
    assert len(model.calls) >= 4 * len(model.accepted_sources) - 2
    ids = [value["source_window"]["id"] for system, value in model.calls if system == ref.WINDOW_SYSTEM]
    assert [result.index(source_id) for source_id in ids] == sorted(result.index(source_id) for source_id in ids)
    complete = {"phase": "complete", "source_windows": len(ids), "source_chars": len(source),
                "coverage_complete": True, "semantic_proof": False}
    assert {key: events[-1][key] for key in complete} == complete
    assert all("Тема номер" not in json.dumps(event, ensure_ascii=False) for event in events)


def test_context_refusal_splits_full_window_without_losing_source():
    source = speech_lines()
    model = ScriptedModel()
    refusals = []

    def request(cfg, prompt, system, predict):
        value = json.loads(prompt)
        if system == ref.WINDOW_SYSTEM and len(value["final_speech"]) > 1600:
            refusals.append(value["source_window"]["id"])
            raise ref.RefinementRefused("complete_input_exceeds_context")
        return model(cfg, prompt, system, predict)

    ref.refine(source, [], "", {}, request=request)
    assert refusals
    assert "".join(model.accepted_sources) == source
    assert all(len(part) <= 1600 for part in model.accepted_sources)


@pytest.mark.parametrize("field,value", [("has_cross_window_correction", None),
                                        ("has_cross_window_correction", "false"),
                                        ("correction_quotes", ["Несуществующая поправка."]),
                                        ("coverage_complete", False)])
def test_unknown_correction_or_coverage_refuses_after_visiting_each_window(field, value):
    model = ScriptedModel()

    def request(cfg, prompt, system, predict):
        response = json.loads(model(cfg, prompt, system, predict))
        if system == ref.WINDOW_AUDIT_SYSTEM:
            response[field] = value
        return json.dumps(response)

    source = speech_lines()
    with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
        ref.refine(source, [], "", {}, request=request)
    assert "".join(model.accepted_sources) == source


def test_source_window_budget_refuses_before_any_model_request(monkeypatch):
    monkeypatch.setattr(ref, "MAX_WINDOWS", 2)
    model = ScriptedModel()
    with pytest.raises(ref.RefinementRefused, match="source_window_budget"):
        ref.refine(speech_lines(), [], "", {}, request=model)
    assert model.calls == []


def test_request_budget_does_not_publish_partial_result(monkeypatch):
    monkeypatch.setattr(ref, "MAX_REQUESTS", 3)
    model = ScriptedModel()
    events = []
    with pytest.raises(ref.RefinementRefused, match="model_request_budget"):
        ref.refine(speech_lines(), [], "", {}, request=model, progress=events.append)
    assert len(model.calls) == 3
    assert not any(event["phase"] == "complete" for event in events)


def test_live_gate_can_stop_between_generation_and_audit():
    model = ScriptedModel()
    gates = iter([True, False])
    with pytest.raises(ref.RefinementRefused, match="request_deferred"):
        ref.refine(speech_lines(), [], "", {}, request=model, before_request=lambda: next(gates))
    assert len(model.calls) == 1


def test_lexical_draft_candidates_bounded_and_not_speech_evidence():
    source = speech_lines()
    thread = [{"text": "Тема номер 0000: ошибочный кандидат про бюджет."},
              {"text": "Совершенно посторонняя реклама."},
              {"text": "Тема номер 0001: прежний вопрос."}]
    model = ScriptedModel()
    result = ref.refine(source, thread, "Бюджет точно согласован.", {}, request=model)
    inputs = [value for system, value in model.calls if system == ref.WINDOW_SYSTEM]
    assert all(value["live_thread"] == [thread[0]["text"], thread[2]["text"]] for value in inputs)
    assert "бюджет" not in result
    assert "".join(model.accepted_sources) == source


def test_source_split_retains_native_speaker_and_exact_offsets():
    source = "**Участник A** [00:01]:\n" + "Продолжение длинной реплики. " * 100
    windows = ref._windows(source, 250)
    assert "".join(window["text"] for window in windows) == source
    assert all(window["speaker_at_start"] == "Участник A" for window in windows[1:])
    assert all(source[window["start"]:window["end"]] == window["text"] for window in windows)


def test_audited_noise_window_can_be_removed_without_dropping_source_input():
    source = speech_lines()
    model = ScriptedModel()
    first = True

    def request(cfg, prompt, system, predict):
        nonlocal first
        response = json.loads(model(cfg, prompt, system, predict))
        if system == ref.WINDOW_SYSTEM and first:
            response = {"paragraphs": []}
            first = False
        return json.dumps(response)

    result = ref.refine(source, [], "", {}, request=request)
    assert "".join(model.accepted_sources) == source
    assert model.first_id.removesuffix(":p0") not in result
    audits = [value for system, value in model.calls if system == ref.WINDOW_AUDIT_SYSTEM]
    assert audits[0]["paragraphs"] == []


def test_paragraphs_cannot_reverse_source_order():
    early = "Участник A: Первый вопрос остаётся открытым."
    late = "Участник B: Второй вопрос также остаётся открытым."
    rows = [{"text": "Второй вопрос.", "quotes": [late], "uncertain": False},
            {"text": "Первый вопрос.", "quotes": [early], "uncertain": False}]
    with pytest.raises(ref.RefinementRefused, match="paragraph_source_order"):
        ref._check_order(rows, early + "\n" + late)


def test_repeated_quote_requires_explicit_uncertainty():
    quote = "Нам нужно обсудить следующий этап."
    row = {"text": "Автор определён.", "quotes": [quote], "uncertain": False}
    with pytest.raises(ref.RefinementRefused, match="ambiguous_source_anchor"):
        ref._check_order([row], quote + "\n" + quote)
    ref._check_order([{**row, "uncertain": True}], quote + "\n" + quote)


def test_mlx_without_no_truncation_contract_refused_before_client(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "LLM", lambda *args: pytest.fail("must not construct unsupported transport"))
    with pytest.raises(ref.RefinementRefused, match="local_model_required"):
        ref.local_request({"llm": {"engine": "mlx-server"}}, "source", "system", 100)


def padded_window(line, size=3000):
    prefix = line + "\n"
    return prefix + ("Нейтральный фон. " * size)[:size - len(prefix) - 1] + "\n"


@pytest.mark.parametrize("uncertain_after_audit", [False, True])
def test_middle_window_keeps_verified_uncertainty_and_omits_unverified_source(uncertain_after_audit):
    first = padded_window("Первое обсуждение описано полностью.")
    middle = padded_window("  Спорный фрагмент.\r\n**Из стенограммы**\n\n```text\n# Заголовок\n```\nСимволы ``````.  ")
    last = padded_window("После уточнения участники вернулись к вопросу.")
    source = first + middle + last
    model = ScriptedModel()
    events = []
    early_text, late_text = "Обсудили начальный вопрос.", "Завершили обсуждение."
    rejected_text = "Черновик приписал участнику постороннее решение."

    def request(cfg, prompt, system, predict):
        response = json.loads(model(cfg, prompt, system, predict))
        if system.startswith(ref.WINDOW_SYSTEM):
            owned = json.loads(prompt)["final_speech"]
            row = response["paragraphs"][0]
            row["text"] = {first: early_text, middle: rejected_text, last: late_text}[owned]
            if owned == middle:
                if uncertain_after_audit:
                    response["paragraphs"].append({"text": "Осталась спорная формулировка.",
                                                   "quotes": ["Символы ``````."], "uncertain": True})
                else:
                    row["quotes"] = ["Такой опоры в источнике нет."]
        return json.dumps(response)

    body = ref.refine(source, [], "", {}, request=request, progress=events.append)
    assert not verbatim_slices(body) and middle.strip() not in body
    assert (ref.PARTIAL_NOTE in body) is (not uncertain_after_audit)
    assert body.index(early_text) < body.index(late_text)
    if uncertain_after_audit:
        assert "**Требует уточнения.** Осталась спорная формулировка." in body
    else:
        assert rejected_text not in body and "Осталась спорная формулировка." not in body
    middle_audits = [value for system, value in model.calls
                     if system == ref.WINDOW_AUDIT_SYSTEM and value["final_speech"] == middle]
    assert len(middle_audits) == int(uncertain_after_audit)
    discoveries = [value for system, value in model.calls if system == links.DISCOVER_SYSTEM]
    assert discoveries
    for value in discoveries:
        ids = [item["id"] for item in value["early_paragraphs"]]
        assert f"s{len(first)}-{len(first + middle)}:p1" not in ids
        if value["current_source"]["final_speech"] == last:
            expected = [f"s0-{len(first)}:p0"]
            if uncertain_after_audit:
                expected.append(f"s{len(first)}-{len(first + middle)}:p0")
            assert ids == expected
    assert first not in body and last not in body
    visited = [event for event in events if event["phase"] == "source_window"]
    assert [(e["start_char"], e["source_chars"]) for e in visited] == [
        (0, len(first)), (len(first), len(first + middle)), (len(first + middle), len(source))]
    assert [e["verbatim"] for e in visited] == [False, False, False]
    if uncertain_after_audit:
        assert visited[1]["reason"] == "verified_with_uncertainty"
    assert all(type(e["verbatim"]) is bool for e in visited)
    assert events[-1]["phase"] == "complete" and events[-1]["source_chars"] == len(source)


@pytest.mark.parametrize("opening,continuation", [
    ("Участник A: Выпуск назначен на 12 июня,", "только после успешной приёмки."),
    ("Участник A: Кто подготовит итоговый отчёт?", "Участник B: Итоговый отчёт подготовлю я."),
])
def test_boundary_condition_and_answer_reach_generation_and_audit(opening, continuation):
    left = padded_window("Участник C: Предыдущая нейтральная тема.", 3000 - len(opening) - 1) + opening + "\n"
    source = left + padded_window(continuation) + padded_window("Участник C: Следующая независимая тема.")
    model = ScriptedModel()
    ref.refine(source, [], "", {}, request=model)
    generations = [value for system, value in model.calls if system == ref.WINDOW_SYSTEM]
    audits = [value for system, value in model.calls if system == ref.WINDOW_AUDIT_SYSTEM]
    assert opening in generations[0]["final_speech"]
    assert continuation in generations[0]["neighbor_context"]["after"]
    assert opening in generations[1]["neighbor_context"]["before"]
    assert continuation in generations[1]["final_speech"]
    assert audits[0]["neighbor_context"] == generations[0]["neighbor_context"]
    assert audits[1]["neighbor_context"] == generations[1]["neighbor_context"]
    assert "".join(model.accepted_sources) == source


def test_output_budget_splits_owned_source_without_loss():
    source = speech_lines()
    model = ScriptedModel()

    def request(cfg, prompt, system, predict):
        value = json.loads(prompt)
        if system == ref.WINDOW_SYSTEM and len(value["final_speech"]) > 1600:
            raise ref.RefinementRefused("output_budget")
        return model(cfg, prompt, system, predict)

    ref.refine(source, [], "", {}, request=request)
    assert "".join(model.accepted_sources) == source



@pytest.mark.parametrize("verdict", ["supported", "uncertain", "unsupported"])
def test_late_correction_keeps_early_proposal_and_only_displays_supported_link(verdict):
    early = "Участник A: Для проекта Север предложили выпуск 12 июня."
    distinct = "Участник B: Для проекта Юг предложили выпуск 20 июня."
    late = "Участник A: Выпуск Севера переносим на 15 июня только после приёмки."
    source = padded_window(early) + padded_window(distinct) + padded_window(late)
    model = ScriptedModel(late)

    def request(cfg, prompt, system, predict):
        value = json.loads(model(cfg, prompt, system, predict))
        if system == ref.WINDOW_SYSTEM:
            value["paragraphs"][0]["text"] = value["paragraphs"][0]["quotes"][0]
        if system == links.ANCHOR_AUDIT_SYSTEM:
            value["verdict"] = verdict
        if system == ref.WINDOW_AUDIT_SYSTEM:
            value.update(has_cross_window_correction=False, correction_quotes=[])
        return json.dumps(value)

    body = ref.refine(source, [], "", {}, request=request)
    assert body.index(early) < body.index(distinct) < body.index(late)
    editor = next(value for system, value in model.calls if system == links.CONCLUSION_SYSTEM)
    assert bool(editor["relations"]) is (verdict == "supported")
    assert "Возможная связь" not in body
    assert sum(system == links.ANCHOR_AUDIT_SYSTEM for system, _ in model.calls) == 1
    pairs = [value for system, value in model.calls if system == links.PAIR_AUDIT_SYSTEM]
    assert len(pairs) == 1
    assert early in pairs[0]["early_source"]["final_speech"]
    assert late in pairs[0]["late_source"]["final_speech"]
    assert "".join(model.accepted_sources) == source


def test_sixteen_windows_check_every_prior_id_within_original_request_budget():
    source = "".join(padded_window(f"Участник A: Независимая тема номер {i:04d} пока открыта.")
                     for i in range(16))
    model = ScriptedModel()
    ref.refine(source, [], "", {}, request=model)
    checked = {}
    for system, value in model.calls:
        if system == links.DISCOVER_SYSTEM:
            key = value["current_source"]["source_window"]["id"]
            checked.setdefault(key, []).extend(item["id"] for item in value["early_paragraphs"])
    prior = []
    for system, value in model.calls:
        if system == ref.WINDOW_SYSTEM:
            key = value["source_window"]["id"]
            assert checked.get(key, []) == prior
            prior.append(key + ":p0")
    assert len(prior) == 16
    assert len(model.calls) <= 256


@pytest.mark.parametrize("reject_second", [False, True])
@pytest.mark.parametrize("all_supported", [False, True])
def test_repair_inserts_only_changes_then_audits_whole_window(reject_second, all_supported):
    model = ScriptedModel()
    calls = []
    first_window = None

    def request(cfg, prompt, system, predict):
        nonlocal first_window
        value = json.loads(prompt)
        response = json.loads(model(cfg, prompt, system, predict))
        if system == ref.WINDOW_SYSTEM:
            first_window = first_window or value["source_window"]["id"]
            if value["source_window"]["id"] == first_window:
                last = value["final_speech"].splitlines()[2]
                response["paragraphs"].append({"text": "Позднее обсудили отдельное условие.", "uncertain": False,
                                               "quotes": [last]})
        if system == ref.WINDOW_REPAIR_SYSTEM:
            original = calls[0]["paragraphs"]
            missing = value["final_speech"].splitlines()[1]
            assert value["repair_quotes"] == [missing]
            assert value["kept_text"] == [row["text"] for row in (original if all_supported else original[:1])]
            assert value["rejected"] == ([] if all_supported else original[1:])
            assert value["final_speech"] == calls[0]["final_speech"]
            response = {"changes": [{"text": "Обсудили следующий вопрос.", "quotes": [missing], "uncertain": False}]}
            if not all_supported:
                response["changes"].insert(0, {**original[1], "text": "Позднее уточнили условие."})
        if system == ref.WINDOW_AUDIT_SYSTEM and value["source_window"]["id"] == first_window:
            calls.append(value)
            if len(calls) == 1 or reject_second:
                response["coverage_complete"] = False
                response["paragraphs"][-1]["supported"] = all_supported
                response["repair_quotes"] = [value["final_speech"].splitlines()[1]]
        return json.dumps(response)

    body = ref.refine(speech_lines(), [], "", {}, request=request)
    assert len(calls) == 2
    assert not verbatim_slices(body)
    assert (ref.PARTIAL_NOTE in body) == reject_second
    assert "Обсудили следующий вопрос." in body
    assert calls[0]["final_speech"] == calls[1]["final_speech"]
    original, repaired = [call["paragraphs"] for call in calls]
    assert repaired[0] == original[0]
    assert repaired[1]["quotes"] == [calls[0]["final_speech"].splitlines()[1]]
    assert repaired[2] == (original[1] if all_supported else {**original[1], "text": "Позднее уточнили условие."})


@pytest.mark.parametrize("count,rejected", [(0, False), (0, True), (2, False), (2, True)])
def test_window_audit_schema_allows_honest_verdicts_and_enforces_row_shape(count, rejected):
    source = "Решение пока остаётся открытым."
    actual = {**audit(count), "coverage_complete": not rejected, "has_cross_window_correction": False,
              "correction_quotes": [], "repair_quotes": [source] if rejected else []}
    if count:
        actual["paragraphs"][-1]["supported"] = not rejected
    ok, no = ref._window_audit_schema(count)["anyOf"]
    schema = no if rejected else ok
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(actual)
    fields = schema["properties"]
    rows = fields["paragraphs"]
    assert rows["minItems"] == rows["maxItems"] == count
    assert rows["items"]["additionalProperties"] is False
    assert rows["items"]["required"] == ["index", "supported"]
    assert rows["items"]["properties"]["index"]["maximum"] == max(0, count - 1)
    if rejected:
        assert fields["repair_quotes"]["minItems"] == 1
        assert fields["coverage_complete"] == {"type": "boolean"}
    else:
        assert fields["repair_quotes"]["maxItems"] == 0
        assert fields["coverage_complete"] == fields["corrections_resolved"] == {"const": True}
        assert rows["items"]["properties"]["supported"] == {"const": True}
    _, feedback = ref._window_audit(json.dumps(actual), count, source)
    assert (feedback is not None) == rejected


@pytest.mark.parametrize("repair_quotes", [[], ["Чужая реплика за пределами окна."], [False]])
def test_coverage_repair_accepts_only_witnesses_in_owned_speech(repair_quotes):
    verdict = {**audit(0), "coverage_complete": False, "has_cross_window_correction": False,
               "correction_quotes": [], "repair_quotes": repair_quotes}
    with pytest.raises(ref.RefinementRefused, match="invalid_repair_evidence"):
        ref._window_audit(json.dumps(verdict), 0, "Согласовали только обсуждение условий.")


def test_repair_conflicts_cannot_duplicate_or_displace_a_supported_event():
    early, kept, late = "Ранний вопрос открыт.", "Уточнили место встречи.", "Следующий вопрос открыт."
    window = {"context_before": "", "context_after": "", "text": "\n".join([early, kept, late, late])}
    original = {"text": kept, "quotes": [kept], "uncertain": False}
    for change, reason in [
        (original, "repair_source_conflict"),
        ({**original, "text": "Другие слова о встрече."}, "repair_source_conflict"),
        ({**original, "quotes": [early]}, "repair_source_conflict"),
        ({"text": "Неясное утверждение.", "quotes": [late], "uncertain": True}, "ambiguous_repair_anchor"),
    ]:
        with pytest.raises(ref.RefinementRefused, match=reason):
            ref._merge_repair([original], [change], window)
    window["text"] = "\n".join([early, kept, late])
    straddling = {"text": "Объединённые этапы.", "quotes": [early, late], "uncertain": False}
    with pytest.raises(ref.RefinementRefused, match="repair_source_conflict"):
        ref._merge_repair([original], [straddling], window)


def test_repair_schema_requires_full_changes_without_prior_markers():
    schema = ref._repair_schema()
    assert schema["required"] == ["changes"] and schema["additionalProperties"] is False
    row = schema["properties"]["changes"]["items"]
    assert set(row["required"]) == {"text", "quotes", "uncertain"} and row["additionalProperties"] is False
    assert row["properties"]["quotes"]["minItems"] == 1
    assert row["properties"]["quotes"]["items"]["minLength"] == 12
    with pytest.raises(ref.RefinementRefused, match="paragraph_schema"):
        ref._paragraphs('{"changes":[{"prior":0}]}', "", field="changes")


def test_render_failure_cannot_emit_complete_progress(monkeypatch):
    events = []
    def refuse(*args):
        raise ref.RefinementRefused("link_render_identity")
    monkeypatch.setattr(links, "render_conversation", refuse)
    with pytest.raises(ref.RefinementRefused, match="link_render_identity"):
        ref.refine(speech_lines(), [], "", {}, request=ScriptedModel(), progress=events.append)
    assert not any(event["phase"] == "complete" for event in events)


def test_dense_long_input_stops_at_request_limit_without_complete_or_partial_result():
    source = "".join(padded_window("\n".join(
        f"Участник A: В теме {window:02d}.{row} предложили независимое условное решение; ответ ещё уточняют."
        for row in range(8))) for window in range(32))
    model = ScriptedModel()
    events = []

    def request(cfg, prompt, system, predict):
        value = json.loads(prompt)
        if system == ref.WINDOW_SYSTEM:
            model.calls.append((system, value))
            model.accepted_sources.append(value["final_speech"])
            rows = [{"text": line + " На этом этапе обсуждения окончательное решение ещё не принимали.",
                     "quotes": [line], "uncertain": False}
                    for line in value["final_speech"].splitlines() if line.startswith("Участник A:")]
            return json.dumps({"paragraphs": rows})
        return model(cfg, prompt, system, predict)

    with pytest.raises(ref.RefinementRefused, match="model_request_budget"):
        ref.refine(source, [], "", {}, request=request, progress=events.append)
    assert len(model.calls) == ref.MAX_REQUESTS
    assert not any(event["phase"] == "complete" for event in events)

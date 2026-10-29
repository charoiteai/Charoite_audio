"""Минутки после пересборки — не черновик и без «Собеседник N» (№146).

Встреча 08:45 31.08: стенограмма пересобрана, имена подставлены, а минутки
рядом остались с шапкой «черновик, встреча идёт» и участниками «Собеседник 2,
Собеседник 4» — человек читал устаревший документ с ярлыками. Перештамповка
точечная: маркер и метки; ручные правки человека не перегенерируются.
"""
import pathlib
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import live_sidecar  # noqa: E402
import meeting_source  # noqa: E402
import llm as llm_mod  # noqa: E402
import rebuild_transcript  # noqa: E402
import transcript  # noqa: E402


def _make(tmp_path, minutes: str):
    live = tmp_path / "2026-08-31_0845.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    mpath = tmp_path / "2026-08-31_0845_minutes.md"
    mpath.write_text(minutes, encoding="utf-8")
    return live, mpath


def test_marker_removed_and_labels_become_names(tmp_path):
    live, mpath = _make(
        tmp_path,
        transcript.MINUTES_DRAFT_MARK + "\n# Минутки\n\n"
        "Участники: Ян, Собеседник 2, Собеседник 4\n\n"
        "Поручения:\n- [ ] **Собеседник 2** — прислать смету\n")
    rebuild_transcript.restamp_minutes(
        live, {"Собеседник 2": "Инга", "Собеседник 4": "Марк"})
    out = mpath.read_text(encoding="utf-8")
    assert transcript.MINUTES_DRAFT_MARK not in out
    assert "Инга" in out and "Марк" in out
    assert "Собеседник 2" not in out and "Собеседник 4" not in out
    assert "- [ ] **Инга** — прислать смету" in out


def test_label_boundary_respects_digits(tmp_path):
    """«Собеседник 2» не должен красить «Собеседник 22»."""
    live, mpath = _make(
        tmp_path, "Участники: Собеседник 2, Собеседник 22\n")
    rebuild_transcript.restamp_minutes(live, {"Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert "Инга, Собеседник 22" in out, out


def test_idempotent_and_quiet_without_minutes(tmp_path):
    """Повторная пересборка (retry_unfinished) не должна ни падать без
    минуток, ни переписывать уже перештампованный файл."""
    live = tmp_path / "no_minutes.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    rebuild_transcript.restamp_minutes(live, {"Собеседник 2": "Инга"})  # файла нет — тихо

    live2, mpath = _make(tmp_path, "# Минутки\nУчастники: Инга\n")
    before = mpath.stat().st_mtime_ns
    rebuild_transcript.restamp_minutes(live2, {"Собеседник 2": "Инга"})
    assert mpath.stat().st_mtime_ns == before, "нечего менять — файл не трогается"


def test_owner_label_is_never_substituted(tmp_path):
    """names может нести и метку владельца («Я» при пустом user_name —
    names_by_time скорит все сегменты, №147): без фильтра re.sub без границы
    слова переписал бы каждое «Я» и каждое «Яблоко» документа (DS Critical
    по #464). Подменяются только нейтральные «Собеседник N»."""
    live, mpath = _make(
        tmp_path,
        "# Минутки\n\nЯ отвечаю за релиз. Январь — срок Яна. Яблочный пирог — Собеседник 2.\n")
    rebuild_transcript.restamp_minutes(
        live, {"Я": "Имярек", "Ян": "Имярек2", "Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert "Я отвечаю за релиз. Январь — срок Яна." in out, out
    assert "Яблочный пирог — Инга." in out, out
    assert "Имярек" not in out


def test_bare_channel_label_is_substituted_but_not_numbered_prefix(tmp_path):
    """Голый «Собеседник» — канальная метка установок без моделей диаризации
    (audio.SPEAKER): на них перештамповка не работала вовсе (GLM r2 I1).
    При этом голая метка не должна съедать префикс нумерованной."""
    live, mpath = _make(
        tmp_path, "Участники: Собеседник, Собеседник 2\n- [ ] **Собеседник** — прислать смету\n")
    rebuild_transcript.restamp_minutes(live, {"Собеседник": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert "Участники: Инга, Собеседник 2" in out, out
    assert "- [ ] **Инга** — прислать смету" in out


def test_live_session_names_sanitizes_broken_live_json():
    """Битый live.json («names» — список, число вместо имени) не должен
    ронять перештамповку после уже записанной стенограммы (DS r2 M1)."""
    assert rebuild_transcript.live_session_names({"names": ["x"]}) == {}
    assert rebuild_transcript.live_session_names({}) == {}
    got = rebuild_transcript.live_session_names(
        {"names": {"Собеседник 2": "Инга", "Собеседник 3": 5, 4: "Марк", "Собеседник 5": "  "}})
    assert got == {"Собеседник 2": "Инга"}


def test_minutes_names_follow_the_numbering_the_minutes_were_written_in():
    """Минутки прошлой пересборки (в сайдкаре minutes_source_sha256) написаны
    нумерацией пересборки — имена живой сессии к ним не подходят. Живые
    минутки и мусор вместо хеша — прежнее поведение, имена из live.json."""
    names = {"Собеседник 2": "Инга"}
    assert rebuild_transcript.minutes_names({"names": names}) == names
    assert rebuild_transcript.minutes_names({"names": names, "minutes_source_sha256": "a" * 64}) == {}
    assert rebuild_transcript.minutes_names({"names": names, "minutes_source_sha256": "мусор"}) == names
    assert rebuild_transcript.minutes_names({}) == {}
    assert rebuild_transcript.minutes_names(None) == {}


_EVIDENCE = "\n".join((
    "[·] Собеседник 2: Коль, судя по твоему вопросу, смета готова.",
    "[·] Собеседник 3: Это Марк, я по срокам.",
    "[·] Я: Коля, Марк, спасибо.",
))


def test_judge_live_names_drops_a_self_addressed_name(tmp_path, monkeypatch):
    """№830: живое имя «Коля» перенесено на метку, которая сама зовёт Колю, —
    снято, метка остаётся безымянной и уходит к модели; в журнале — причина.
    Сайдкар не трогается: решение — свойство пары в тексте этого прохода."""
    logs: list[str] = []
    monkeypatch.setattr(rebuild_transcript, "log", logs.append)
    live = tmp_path / "2026-10-28_0848.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    live_sidecar.merge(live, {"names": {"Собеседник 1": "Коля", "Собеседник 4": "Марк"}})
    before = rebuild_transcript.live_meta_path(live).read_bytes()
    kept = rebuild_transcript.judge_live_names(
        {"Собеседник 2": "Коля", "Собеседник 3": "Марк"}, _EVIDENCE, "")
    assert kept == {"Собеседник 3": "Марк"}
    assert logs == ["имена из живой сессии: «Коля» для «Собеседник 2» не принято — "
                    "метка сама обращается по этому имени — имя чужое"]
    assert rebuild_transcript.live_meta_path(live).read_bytes() == before
    assert rebuild_transcript.minutes_names(rebuild_transcript.live_meta(live)) == {
        "Собеседник 1": "Коля", "Собеседник 4": "Марк"}, "живая нумерация судом пересборки не правится"


def test_judge_live_names_keeps_everything_without_a_reason(monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(rebuild_transcript, "log", logs.append)
    assert rebuild_transcript.judge_live_names({"Собеседник 3": "Марк"}, _EVIDENCE, "") == {"Собеседник 3": "Марк"}
    assert rebuild_transcript.judge_live_names({}, _EVIDENCE, "") == {}
    assert logs == []


def test_judge_live_names_passes_the_owner_to_the_judge(monkeypatch):
    monkeypatch.setattr(rebuild_transcript, "log", lambda *_: None)
    assert rebuild_transcript.judge_live_names({"Собеседник 3": "Марк"}, _EVIDENCE, "Марк Тестов") == {}


def test_rebuild_judges_both_sources_of_names_on_the_full_transcript():
    """Контракт на проводку (№830): перенесённые по времени имена и догадка
    модели проходят суд пары по полной итоговой выборке — модель на урезанной
    выборке не вернёт метке имя, снятое по полной (n830-in1 DS C3)."""
    src = (SRC / "rebuild_transcript.py").read_text(encoding="utf-8")
    fn = src[src.index("def rebuild("):src.index("def human_edited_transcript(")]
    carry = fn.index("names = names_by_time(")
    judged = fn.index("names = judge_live_names(names, evidence, user_name)")
    model = fn.index("naming = name_speakers(")
    verified = fn.index("speaker_names.verify_names(naming.names, sample=evidence")
    assert carry < judged < model < verified
    assert "rest = neutral - set(names) - no_names" in fn[judged:model], "снятая метка уходит к модели"
    assert "evidence = \"\\n\".join(_sample_line(spk, text) for _, _, spk, text in lines if text)" in fn


def test_rebuild_wires_live_names_into_restamp():
    """Контракт на проводку: rebuild обязан отдавать в restamp словарь по
    нумерации минуток (minutes_names) — откат на пересборочный `names`
    возвращал бы Critical со смешанной нумерацией при зелёных юнитах (GLM r2
    M4); имена живой сессии на минутках прошлой пересборки — та же ошибка
    с другого конца (аудит зон 12.09)."""
    src = (SRC / "rebuild_transcript.py").read_text(encoding="utf-8")
    fn = src[src.index("def rebuild("):src.index("def finalize_minutes(")]
    assert "finalize_minutes(live, final_text, meta, cfg, minutes_names(meta))" in fn, (
        "в минутки должны идти имена той нумерации, которой они написаны "
        "(minutes_names: живая сессия из live.json или ничего), не пересборочные")
    assert "restamp_minutes(" not in fn, "перештамповка — только внутри finalize_minutes"


def test_names_by_time_never_assigns_to_owner_label():
    """№147 (класс Critical DS по #464): владелец говорит больше всех, и
    живое имя, звучавшее в его репликах (обращение к нему), уходило его
    метке — стенограмма переименовывала абзацы владельца в чужое имя.
    Метка владельца в скоринг не входит; имя достаётся нейтральной."""
    import datetime as dt
    base = dt.datetime(2026, 8, 31, 10, 0)
    live = "**Инга** [10:00–10:02]:\nдлинная реплика\n"
    segments = [(0.0, 100.0, "Ян"), (30.0, 60.0, "Собеседник 1")]
    out = rebuild_transcript.names_by_time(live, base, segments, {"Инга"})
    assert out == {"Собеседник 1": "Инга"}, out

    only_owner = rebuild_transcript.names_by_time(
        live, base, [(0.0, 100.0, "Я")], {"Инга"})
    assert only_owner == {}, only_owner


def test_safe_write_expect_gate(tmp_path):
    """Общий expect-гейт: снимок до чтения — чужая запись в окне не
    затирается (протокол один на всех писателей, критика DS по #464)."""
    from charoite_graph import safe_write
    p = tmp_path / "m.md"
    p.write_text("v1", encoding="utf-8")
    snap = safe_write.stat_snapshot(p)
    p.write_text("чужой финал длиннее", encoding="utf-8")
    assert safe_write.write_text(p, "v2", expect=snap) is False
    assert p.read_text(encoding="utf-8") == "чужой финал длиннее"
    fresh = safe_write.stat_snapshot(p)
    assert safe_write.write_text(p, "v2", expect=fresh) is True
    assert p.read_text(encoding="utf-8") == "v2"


def test_neutral_label_predicate_is_single_and_covers_nbsp():
    """Единый предикат формы нейтральной метки (GLM M2 по #465): три копии
    (` ?\\d+` / `\\s+\\d+` / опциональный номер) давали щель — имя владельца
    «Собеседник 2» (NBSP) не ловилось коллизией, но ловилось скорингом."""
    import channel_labels
    assert channel_labels.is_neutral_label("Собеседник")
    assert channel_labels.is_neutral_label("Собеседник 2")
    assert channel_labels.is_neutral_label("Собеседник 2")
    assert channel_labels.is_neutral_label("Собеседник 10")
    assert not channel_labels.is_neutral_label("Я")
    assert not channel_labels.is_neutral_label("Собеседник 2а")
    assert not channel_labels.is_neutral_label("Ян")


def test_owner_label_never_reaches_name_speakers_rest():
    """DS+GLM I1 по #465: владелец не получает имя по построению — держать
    его в rest значило холостой вызов модели на каждой пересборке и ложную
    плашку «имена не определены» при её молчании. Контракт: rest и unnamed
    считаются от нейтральных меток."""
    src = (SRC / "rebuild_transcript.py").read_text(encoding="utf-8")
    fn = src[src.index("def rebuild("):src.index("def write_final(")]
    assert "neutral = {spk for _, _, spk, _ in lines" in fn
    assert "rest = neutral - set(names)" in fn
    assert "unnamed = neutral - set(names)" in fn


# ---------------------------------------------------------------------------
# finalize_minutes (после встречи 02.09 10:21): нетронутый автотекст —
# заново по финальной стенограмме, правленный руками — только перештамповка.
# ---------------------------------------------------------------------------
import hashlib  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _FakeLLM:
    """Модель на диске не нужна: возвращаем заготовку, считаем вызовы."""
    calls: list[str] = []
    answer = "# Минутки\n**Участники:** Инга, Марк\n## Поручения\n- [ ] **Инга** — прислать смету — до 05.09\n"
    fail = False

    lang = "ru"

    def __init__(self, cfg):
        pass

    recording_block = llm_mod.LLM.recording_block   # настоящий блок, взят до подмены llm.LLM

    def minutes(self, text, recording_note=None):
        _FakeLLM.calls.append(text)
        if _FakeLLM.fail:
            raise RuntimeError("ollama лежит")
        yield _FakeLLM.answer


def _prep(tmp_path, monkeypatch, minutes: str | None, sha: str | None):
    import llm
    _FakeLLM.calls = []
    _FakeLLM.fail = False
    monkeypatch.setattr(llm, "LLM", _FakeLLM)
    # уступка живой встрече читает настоящий logs/daemon.lock — тест на
    # машине с идущей встречей висел бы (GLM r2 Min-2)
    monkeypatch.setattr(rebuild_transcript, "_yield_to_live", lambda *a, **k: None)
    live = tmp_path / "2026-09-02_1021.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    meta = {"speakers": 2, "names": {"Собеседник 2": "Инга"}}
    if sha is not None:
        meta["minutes_sha256"] = sha
    (tmp_path / "2026-09-02_1021.md.live.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    mpath = tmp_path / "2026-09-02_1021_minutes.md"
    if minutes is not None:
        mpath.write_text(minutes, encoding="utf-8")
    return live, mpath, meta


FINAL = "**Инга** [10:21]:\nСмету пришлю к пятому.\n" * 30   # > MINUTES_MIN_CHARS


def test_untouched_draft_is_regenerated_from_final(tmp_path, monkeypatch):
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nУчастники: Собеседник 2\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {"Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert owned != "human"
    assert _FakeLLM.calls == [FINAL.rstrip("\n")], "минутки — по ФИНАЛЬНОЙ стенограмме (речь без хвостовых переводов строк)"
    assert "Черновик" not in out and "- [ ] **Инга** — прислать смету" in out
    # прежняя версия — в .prev/ рядом со стенограммой (advisory DS r1 по #483)
    assert (tmp_path / ".prev" / "2026-09-02_1021_minutes.md").read_text(encoding="utf-8") == draft
    # хеш пишет rebuild() ПОСЛЕ канонизации — по байтам на диске; сама запись
    # хранит остальные поля live.json
    rebuild_transcript._remember_minutes_sha(live, _sha(out))
    saved = json.loads((tmp_path / "2026-09-02_1021.md.live.json").read_text(encoding="utf-8"))
    assert saved["minutes_sha256"] == _sha(out)
    assert saved["names"] == {"Собеседник 2": "Инга"}, "остальные поля live.json целы"


def test_rebuild_records_hash_after_canonization_only_for_machine_text():
    """Контракт проводки (DS r1 Imp-1/Imp-2 по #483): хеш снимается в
    rebuild() после canonize_file и только когда файл машинный — иначе
    лексикон делал файл «правленным руками», а транзиентный отказ модели
    навсегда выключал регенерацию."""
    src = (SRC / "rebuild_transcript.py").read_text(encoding="utf-8")
    fn = src[src.index("def rebuild("):src.index("def record_minutes_passport(")]
    i_fin = fn.index("outcome = finalize_minutes(")
    i_pass = fn.index("record_minutes_passport(live, mpath, outcome, final_text, cfg)")
    assert i_fin < i_pass, "паспорт — после finalize"
    # тройка «канон → хеш байтов → хеш речи» живёт одной функцией для пересборки и
    # ретро-прогона (№309): канон раньше хеша, хеш — только для машинного файла
    passport = src[src.index("def record_minutes_passport("):src.index("def finalize_minutes(")]
    i_can = passport.index("canonize_file(mpath, cfg)")
    i_sha = passport.index("minutes = mpath.read_text(")
    assert i_can < i_sha, "порядок: canonize → хеш по байтам с диска"
    assert 'if outcome == "human":\n        return' in passport[i_can:i_sha], "хеш — только для машинного файла"
    body = src[src.index("def finalize_minutes("):src.index("def restamp_minutes(")]
    assert "_remember_minutes_sha(" not in body, "finalize_minutes сам хеш не пишет"


def test_hand_edited_minutes_are_only_restamped(tmp_path, monkeypatch):
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nУчастники: Собеседник 2\n"
    edited = draft + "\nМоя пометка руками.\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, edited, _sha(draft))
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {"Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert owned == "human", "правленный руками файл — не машинный, хеш не трогать"
    assert _FakeLLM.calls == [], "правленные руками минутки не перегенерируем"
    assert "Моя пометка руками." in out and "Инга" in out
    assert transcript.MINUTES_DRAFT_MARK not in out, "перештамповка сделана"


def test_no_hash_in_live_json_keeps_old_behaviour(tmp_path, monkeypatch):
    # демон старее этой правки: live.json без хеша — считаем, что трогали
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, None)
    rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert _FakeLLM.calls == []
    assert transcript.MINUTES_DRAFT_MARK not in mpath.read_text(encoding="utf-8")


def test_model_failure_falls_back_to_restamp(tmp_path, monkeypatch):
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    _FakeLLM.fail = True
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {"Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert "Черновик" in out and "Инга" in out, "модель лежит — прежние минутки перештампованы, не потеряны"
    assert transcript.MINUTES_DRAFT_MARK not in out
    assert owned != "human", ("после транзиентного отказа файл остаётся машинным: хеш обновится, "
                           "и следующая пересборка сможет перегенерировать (DS r1 Imp-1)")


def test_document_that_appeared_during_generation_is_not_overwritten(tmp_path, monkeypatch):
    # GLM Critical по #483: минуток не было, за 13–60 с генерации их создал
    # mcp «Минутки» — регенерат не должен лечь поверх и объявить чужой
    # текст «своим».
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)

    class _LateWriter(_FakeLLM):
        def minutes(self, text, recording_note=None):
            mpath.write_text("# Минутки от mcp\n", encoding="utf-8")
            yield _FakeLLM.answer

    import llm
    monkeypatch.setattr(llm, "LLM", _LateWriter)
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned == "human", "чужой документ — не машинный, хеш не снимаем"
    assert mpath.read_text(encoding="utf-8") == "# Минутки от mcp\n"


def test_model_gets_speech_without_the_notes_tail(tmp_path, monkeypatch):
    # GLM Imp-4: хвост «Ко-мышление» — мысли модели, не речь; кнопка
    # «Протокол» его не видит, и регенерация — тоже. Порог считается по речи.
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    notes = transcript.NOTES_HEAD + " (📌 КТ · 💎 факты · 💭 мысли)\n> 💎 факт, которого не звучало\n" * 40
    rebuild_transcript.finalize_minutes(live, FINAL + notes, meta, {}, {})
    assert _FakeLLM.calls == [FINAL.rstrip("\n")], "в промпт ушла только речь"
    _FakeLLM.calls = []
    live2 = tmp_path / "2026-09-02_1100.md"
    live2.write_text("# Встреча\n", encoding="utf-8")
    rebuild_transcript.finalize_minutes(live2, "**Инга** [11:00]:\nПривет.\n" + notes, {}, {}, {})
    assert _FakeLLM.calls == [], "порог — по речи: заметки его не набирают"


def test_short_final_keeps_existing_draft_restamped(tmp_path, monkeypatch):
    # advisory GLM r2: замена содержательного черновика регенератом из
    # пустого промпта хуже создания с нуля — короткий финал (эхо-фильтр
    # микрофона) оставляет черновик, перештампованный; файл машинный.
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    short = "**Инга** [10:21]:\nСмету пришлю.\n"
    owned = rebuild_transcript.finalize_minutes(live, short, meta, {}, {"Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert owned != "human" and _FakeLLM.calls == []
    assert "Черновик" in out and "Инга" in out and transcript.MINUTES_DRAFT_MARK not in out


def test_foreign_overwrite_of_draft_plus_model_failure_is_not_claimed(tmp_path, monkeypatch):
    # GLM r2 Imp-1: черновик был машинным, за время ожидания/вызова файл
    # подменил mcp, модель упала → restamp (нет маркера) без записи; файл
    # не наш — хеш снимать нельзя.
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))

    class _OverwriteThenFail(_FakeLLM):
        def minutes(self, text, recording_note=None):
            mpath.write_text("# Минутки от mcp\n", encoding="utf-8")
            raise RuntimeError("ollama лежит")
            yield  # noqa: unreachable

    import llm
    monkeypatch.setattr(llm, "LLM", _OverwriteThenFail)
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned == "human"
    assert mpath.read_text(encoding="utf-8") == "# Минутки от mcp\n"


def test_silent_model_keeps_machine_draft_owned(tmp_path, monkeypatch):
    # модель промолчала, файл не менялся → перештамповка, файл машинный
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    _FakeLLM.answer = "   "
    try:
        owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {"Собеседник 2": "Инга"})
    finally:
        _FakeLLM.answer = "# Минутки\n**Участники:** Инга, Марк\n## Поручения\n- [ ] **Инга** — прислать смету — до 05.09\n"
    assert owned != "human" and "Инга" in mpath.read_text(encoding="utf-8")


def test_undecodable_minutes_do_not_abort_and_are_not_owned(tmp_path, monkeypatch):
    # GLM Min-6: не-UTF8 файл из редактора — перештамповка, не падение
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, "abc")
    mpath.write_bytes(b"\xff\xfe<")
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned == "human" and _FakeLLM.calls == []
    assert mpath.read_bytes() == b"\xff\xfe<"


def test_model_failure_with_late_foreign_file_does_not_claim_it(tmp_path, monkeypatch):
    # DS r2 Imp-1: минуток не было, за окно вызова файл создал чужой
    # процесс, модель упала → restamp ничего не писал, файл не машинный,
    # хеш снимать нельзя (иначе следующая пересборка регенерирует поверх).
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)

    class _LateThenFail(_FakeLLM):
        def minutes(self, text, recording_note=None):
            mpath.write_text("# Минутки от mcp\n", encoding="utf-8")
            raise RuntimeError("ollama лежит")
            yield  # noqa: unreachable — генератор

    import llm
    monkeypatch.setattr(llm, "LLM", _LateThenFail)
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned == "human"
    assert mpath.read_text(encoding="utf-8") == "# Минутки от mcp\n"


def test_live_json_is_found_after_retitle(tmp_path):
    # advisory DS r2: накат темы переименовывает только *.md — сайдкар
    # остаётся посекундным; вторая пересборка обязана его найти.
    (tmp_path / "2026-09-02_102112.md.live.json").write_text(
        json.dumps({"names": {"Собеседник 2": "Инга"}, "minutes_sha256": "abc"}), encoding="utf-8")
    titled = tmp_path / "2026-09-02_1021_Обсуждение_темы.md"
    titled.write_text("# Встреча\n", encoding="utf-8")
    assert rebuild_transcript.live_meta_path(titled).name == "2026-09-02_102112.md.live.json"
    assert rebuild_transcript.live_meta(titled)["names"] == {"Собеседник 2": "Инга"}
    # запись усыновляет сайдкар: он переезжает под имя стенограммы, чтобы
    # дальше не угадывать его по минуте (#489); содержимое сохраняется
    rebuild_transcript._remember_minutes_sha(titled, "def")
    assert not (tmp_path / "2026-09-02_102112.md.live.json").exists()
    saved = json.loads((tmp_path / "2026-09-02_1021_Обсуждение_темы.md.live.json").read_text(encoding="utf-8"))
    assert saved["minutes_sha256"] == "def" and saved["names"] == {"Собеседник 2": "Инга"}
    assert rebuild_transcript.live_meta(titled)["names"] == {"Собеседник 2": "Инга"}
    # чужая минута — не наш сайдкар
    other = tmp_path / "2026-09-02_1100_Другая.md"
    other.write_text("# Встреча\n", encoding="utf-8")
    assert not rebuild_transcript.live_meta_path(other).exists()


def test_missing_minutes_are_built_when_transcript_is_long_enough(tmp_path, monkeypatch):
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert mpath.exists() and "прислать смету" in mpath.read_text(encoding="utf-8")
    _FakeLLM.calls = []
    live2 = tmp_path / "2026-09-02_1100.md"
    live2.write_text("# Встреча\n", encoding="utf-8")
    rebuild_transcript.finalize_minutes(live2, "**Инга** [11:00]:\nПривет.\n", {}, {}, {})
    assert _FakeLLM.calls == [] and not live2.with_name("2026-09-02_1100_minutes.md").exists(), \
        "короткая встреча минуток не заслуживает"


SAID = "**Инга** [10:21]:\nСмету пришлю.\n"


def _cut(text: str) -> str:
    """Финал так, как его пишет пересборка с вынесенным хвостом: пометка среза —
    в хвосте «Ко-мышления» (`transcript.append_note`), не в речи."""
    return transcript.append_note(
        text, rebuild_transcript.AFTER_CALL_CUT_NOTE.format(end="10:40", last="10:52"))


@pytest.mark.parametrize("first_fails", [True, False])
def test_short_final_with_after_call_record_moves_the_draft_without_the_model(
        tmp_path, monkeypatch, first_fails):
    """№733, вариант 2 главной (C1 DS n733-mut-out2): черновик демона собран по
    живой ленте, где запись после звонка ещё внутри; хвост вынесен, речь звонка
    короче порога. Перештамповка отдала бы быт в протокол, а модель на
    короткой речи не зовётся — порог коротких встреч: черновик уходит в .prev/.
    Исход один при отказавшей и исправной модели, и повторный проход его не
    меняет: модель не зовётся ни разу."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    rebuild_transcript.after_call_path(live).write_text("# После звонка\n", encoding="utf-8")
    names = {"Собеседник 2": "Инга"}
    _FakeLLM.fail = first_fails
    said: list[str] = []
    monkeypatch.setattr(rebuild_transcript, "log", said.append)
    assert rebuild_transcript.finalize_minutes(live, _cut(SAID), meta, {}, names) == "human"
    assert not mpath.exists() and _FakeLLM.calls == []
    # лог не обещает пересборку, которой при короткой речи не будет (M1 Sonnet out3)
    assert any("пересборка их не соберёт" in m for m in said)
    assert not any("соберутся" in m for m in said)
    assert (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8") == draft
    _FakeLLM.fail = False
    assert rebuild_transcript.finalize_minutes(live, _cut(SAID), meta, {}, names) == "human"
    assert not mpath.exists() and _FakeLLM.calls == []
    assert (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8") == draft


def test_cut_note_does_not_reach_the_model(tmp_path, monkeypatch):
    """Пометка среза — заметка финала, не речь: в промпт не идёт (Critical Sonnet out2)."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    rebuild_transcript.after_call_path(live).write_text("# После звонка\n", encoding="utf-8")
    assert rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {}) == "regenerated"
    assert _FakeLLM.calls == [FINAL.rstrip("\n")]
    assert "ужин" not in mpath.read_text(encoding="utf-8")


def test_short_cut_final_moves_the_draft_even_without_the_after_call_file(tmp_path, monkeypatch):
    """M1 Haiku n733-mut-out4: признак — строка среза в финале, файла хвоста нет
    (STT пуст) — короткий путь тоже уносит черновик в .prev/, модель не зовётся."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    assert not rebuild_transcript.after_call_path(live).exists()
    owned = rebuild_transcript.finalize_minutes(live, _cut(SAID), meta, {}, {})
    assert owned == "human" and not mpath.exists() and _FakeLLM.calls == []
    assert (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8") == draft


def test_short_final_with_after_call_record_and_no_draft_builds_nothing(tmp_path, monkeypatch):
    """Порог создания с нуля не меняется: минуток не было — короткий финал их не создаёт."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    rebuild_transcript.after_call_path(live).write_text("# После звонка\n", encoding="utf-8")
    rebuild_transcript.finalize_minutes(live, _cut(SAID), meta, {}, {})
    assert _FakeLLM.calls == [] and not mpath.exists()


def test_mcp_minutes_written_while_reading_a_short_cut_final_go_away(tmp_path, monkeypatch):
    """I1 DS out3: минуток на старте не было, MCP «Минутки» записали их по ленте
    с бытом за чтение источника; речь звонка короче порога — машинные байты
    судятся на этот миг и уходят в .prev/, как на пути отказа модели."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    real_of = rebuild_transcript.meeting_source.of

    def of_after_mcp(live_path, final_text):
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- ужин\n", "b" * 64, cut=False)
        return real_of(live_path, final_text)
    monkeypatch.setattr(rebuild_transcript.meeting_source, "of", of_after_mcp)
    owned = rebuild_transcript.finalize_minutes(live, _cut(SAID), meta, {}, {})
    assert owned == "human" and not mpath.exists() and _FakeLLM.calls == []
    assert "ужин" in (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8")


def test_mcp_minutes_of_this_very_speech_written_meanwhile_stay(tmp_path, monkeypatch):
    """I DS n733-mut-out4: минуток на старте не было, MCP «Минутки» записали их за
    чтение источника по той же речи (запись после звонка пуста, паспорт — хеш этой
    речи). Это свежие минутки, а не черновик с бытом: остаются на месте, «fresh»."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    real_of = rebuild_transcript.meeting_source.of

    def of_after_mcp(live_path, final_text):
        src = real_of(live_path, final_text)
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- смета\n", src.sha(), cut=False)
        return src
    monkeypatch.setattr(rebuild_transcript.meeting_source, "of", of_after_mcp)
    owned = rebuild_transcript.finalize_minutes(live, _cut(SAID), meta, {}, {})
    assert owned == "fresh" and "смета" in mpath.read_text(encoding="utf-8")
    assert not (tmp_path / ".prev" / mpath.name).exists() and _FakeLLM.calls == []


def test_minutes_built_from_a_cut_final_survive_a_short_cut_final(tmp_path, monkeypatch):
    """I2 DS out3 = Minor 7 Haiku: минутки собраны из финала со срезом — чистые;
    правленый финал со срезом стал короче порога — перештамповка без модели,
    файл на месте, в .prev/ ничего."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    final = _cut("**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n")
    outcome = rebuild_transcript.finalize_minutes(live, final, meta, {}, {})
    assert outcome == "regenerated"
    rebuild_transcript.record_minutes_passport(live, mpath, outcome, final, {})
    built = mpath.read_text(encoding="utf-8")
    _FakeLLM.calls.clear()
    owned = rebuild_transcript.finalize_minutes(live, _cut(SAID), live_sidecar.read(live), {}, {})
    assert owned == "restamped" and mpath.read_text(encoding="utf-8") == built
    assert _FakeLLM.calls == [] and not (tmp_path / ".prev" / mpath.name).exists()


@pytest.mark.parametrize("answer, fail", [("", True), ("   ", False)])
def test_model_refusal_with_after_call_record_moves_the_live_draft_away(
        tmp_path, monkeypatch, answer, fail):
    """№733, Critical Haiku выходного круга: при записи после звонка и отказе
    модели (исключение или пустой ответ) черновик живой ленты не перештамповывается
    — в нём хвост. Он уходит в .prev/, файла минуток нет: состояние MISSING, его
    собирают следующая пересборка и ретро-обход (Critical DS out2: пометка на месте
    файла запиралась бы чужим писателем)."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    rebuild_transcript.after_call_path(live).write_text("# После звонка\n", encoding="utf-8")
    _FakeLLM.fail = fail
    if not fail:
        monkeypatch.setattr(_FakeLLM, "answer", answer)
    said: list[str] = []
    monkeypatch.setattr(rebuild_transcript, "log", said.append)
    # финал длиннее порога: короткий уходит в .prev/ раньше модели (вариант 2),
    # и тест не дошёл бы до отказа (выжившие мутанты двери CI #852)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {"Собеседник 2": "Инга"})
    assert _FakeLLM.calls, "модель звалась"
    assert owned == "human" and not mpath.exists()
    assert (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8") == draft
    assert any("соберутся" in m for m in said) and not any("не соберёт" in m for m in said)
    assert live_sidecar.derivative_state(mpath, meta, "minutes", "0" * 64) == live_sidecar.MISSING
    rebuild_transcript.record_minutes_passport(live, mpath, owned, _cut(FINAL), {})
    assert not mpath.exists(), "паспорт и канон не воскрешают файл"


def test_model_refusal_moves_minutes_built_from_another_final_away(tmp_path, monkeypatch):
    """Critical DS out3: «собраны из финала» чистоты не доказывает — прежний финал
    мог хвост не вынести (до №733, сомнение в канале). Речь другая, модель
    отказала — минутки с бытом в .prev/, не перештамповка."""
    built = "# Минутки\n- [ ] **Инга** — прислать смету\n- ужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, built, _sha(built))
    meta["minutes_source_sha256"] = "a" * 64
    _FakeLLM.fail = True
    long_final = _cut("**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n")
    owned = rebuild_transcript.finalize_minutes(live, long_final, meta, {}, {})
    assert owned == "human" and not mpath.exists()
    assert (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8") == built


def test_minutes_built_from_a_cut_final_survive_a_refusal_on_an_edited_final(tmp_path, monkeypatch):
    """Haiku-C1 = Sonnet I1 out4: минутки собраны из финала со срезом — чистые.
    Человек поправил стенограмму (срез унаследован), модель отказала —
    минутки остаются перештамповкой, а не уходят в .prev/. Признак пишет паспорт."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    final = _cut("**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n")
    outcome = rebuild_transcript.finalize_minutes(live, final, meta, {}, {})
    assert outcome == "regenerated"
    rebuild_transcript.record_minutes_passport(live, mpath, outcome, final, {})
    passport = live_sidecar.read(live)
    assert passport["minutes_cut_source_sha256"] == passport["minutes_source_sha256"]
    built = mpath.read_text(encoding="utf-8")
    _FakeLLM.fail = True
    edited = final.replace("Смету пришлю.", "Смету пришлю!", 1)
    owned = rebuild_transcript.finalize_minutes(live, edited, passport, {}, {})
    assert owned == "restamped" and mpath.read_text(encoding="utf-8") == built
    assert not (tmp_path / ".prev" / mpath.name).exists()


def test_regeneration_from_a_final_without_the_cut_breaks_the_proof(tmp_path, monkeypatch):
    """Последняя регенерация шла из финала без среза (сомнение в канале — хвост
    в речи): ключи паспорта расходятся, отказ при новом срезе уносит минутки в .prev/."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    first = _cut("**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n")
    rebuild_transcript.record_minutes_passport(
        live, mpath, rebuild_transcript.finalize_minutes(live, first, meta, {}, {}), first, {})
    whole = "**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n**Марк** [10:53]:\nужин и мусор\n"
    passport = live_sidecar.read(live)
    rebuild_transcript.record_minutes_passport(
        live, mpath, rebuild_transcript.finalize_minutes(live, whole, passport, {}, {}), whole, {})
    passport = live_sidecar.read(live)
    assert passport["minutes_cut_source_sha256"] != passport["minutes_source_sha256"]
    _FakeLLM.fail = True
    owned = rebuild_transcript.finalize_minutes(live, first, passport, {}, {})
    assert owned == "human" and not mpath.exists() and (tmp_path / ".prev" / mpath.name).exists()


def test_cut_in_the_final_is_the_only_sign_even_without_the_after_call_file(tmp_path, monkeypatch):
    """Critical Sonnet out3: строка среза в финале есть, файла хвоста нет (STT пуст,
    старая версия) — черновик живой ленты всё равно не перештамповывается."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    assert not rebuild_transcript.after_call_path(live).exists()
    _FakeLLM.fail = True
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert _FakeLLM.calls and owned == "human" and not mpath.exists()


def test_refusal_with_a_failing_move_keeps_the_file_and_does_not_raise(tmp_path, monkeypatch):
    """M6 Sonnet out3: ошибка ФС при переносе в .prev/ не роняет прогон после
    записанной стенограммы — файл остаётся, исход «human»."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    _FakeLLM.fail = True

    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(rebuild_transcript.os, "replace", boom)
    (tmp_path / ".prev").write_text("не каталог", encoding="utf-8")   # mkdir тоже падает (M1 out4)
    # длинный финал: путь отказа модели, а не порога (M1 Sonnet n733-mut-out4)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert _FakeLLM.calls and owned == "human" and mpath.read_text(encoding="utf-8") == draft


def test_model_refusal_without_after_call_record_restamps_as_before(tmp_path, monkeypatch):
    """Без записи после звонка отказ модели — прежняя перештамповка (№146)."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    _FakeLLM.fail = True
    long_final = "**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n"
    owned = rebuild_transcript.finalize_minutes(live, long_final, meta, {}, {"Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert owned == "restamped" and "Инга" in out and "смета" in out


def test_second_model_refusal_keeps_the_live_draft_in_prev(tmp_path, monkeypatch):
    """Два отказа подряд: минуток уже нет — второй проход .prev/ не трогает."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    rebuild_transcript.after_call_path(live).write_text("# После звонка\n", encoding="utf-8")
    _FakeLLM.fail = True
    long_final = _cut("**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n")
    assert rebuild_transcript.finalize_minutes(live, long_final, meta, {}, {}) == "human"
    assert rebuild_transcript.finalize_minutes(live, long_final, meta, {}, {}) == "human"
    assert (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8") == draft
    assert not mpath.exists()


def test_stale_after_call_file_without_a_cut_in_this_final_restamps_as_before(tmp_path, monkeypatch):
    """I1 шага Haiku out1: файл `_после_звонка` прошлого прохода пережил отказ
    разделения — в этом финале пометки среза нет, минутки ведут себя как без хвоста."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    rebuild_transcript.after_call_path(live).write_text("# После звонка\n", encoding="utf-8")
    owned = rebuild_transcript.finalize_minutes(live, SAID, meta, {}, {"Собеседник 2": "Инга"})
    assert owned == "restamped" and _FakeLLM.calls == []
    assert "смета" in mpath.read_text(encoding="utf-8")


def test_cut_note_spoken_in_the_body_does_not_count_as_a_cut(tmp_path, monkeypatch):
    """Признак — заметка финала, а не подстрока: те же слова в речи хвостом не считаются."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    rebuild_transcript.after_call_path(live).write_text("# После звонка\n", encoding="utf-8")
    said = "**Инга** [10:21]:\nЗапись после конца звонка пришлю.\n"
    assert rebuild_transcript.finalize_minutes(live, said, meta, {}, {}) == "restamped"
    assert _FakeLLM.calls == []


def test_failed_passport_write_does_not_keep_an_old_cut_proof(tmp_path, monkeypatch):
    """I1 Sonnet = I1 Haiku out5: регенерация из финала без среза, паспорт которой
    не записался, не оставляет старую пару «срез == источник» — отказ модели на
    финале со срезом не перештамповывает минутки с бытом."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    first = _cut("**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n")
    rebuild_transcript.record_minutes_passport(
        live, mpath, rebuild_transcript.finalize_minutes(live, first, meta, {}, {}), first, {})
    whole = "**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n**Марк** [10:53]:\nужин и мусор\n"
    passport = live_sidecar.read(live)
    real_remember = live_sidecar.remember
    with monkeypatch.context() as m:
        m.setattr(_FakeLLM, "answer", _FakeLLM.answer + "- [ ] **Марк** — ужин и мусор\n")
        m.setattr(live_sidecar, "merge", lambda *a, **k: False)
        m.setattr(live_sidecar, "remember", lambda live, key, value, bare=None:
                  False if key == "minutes_source_sha256" else real_remember(live, key, value, bare))
        rebuild_transcript.record_minutes_passport(
            live, mpath, rebuild_transcript.finalize_minutes(live, whole, passport, {}, {}), whole, {})
    assert live_sidecar.read(live) == passport, "паспорт регенерации пишется целиком или никак"
    _FakeLLM.fail = True
    edited = _cut("**Инга** [10:21]:\n" + "Смету пришлю в пятницу. " * 40 + "\n")
    owned = rebuild_transcript.finalize_minutes(live, edited, live_sidecar.read(live), {}, {})
    assert owned != "restamped", "минутки с бытом не выдаются за собранные из финала со срезом"


def test_regeneration_with_an_unwritable_prev_keeps_the_minutes_and_does_not_raise(tmp_path, monkeypatch):
    """I2 Haiku out5: прежняя версия не сохраняется в .prev/ — минутки не
    перезаписываются, исключение ФС не роняет прогон после записанного финала."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    (tmp_path / ".prev").write_text("не каталог", encoding="utf-8")
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned == "human" and mpath.read_text(encoding="utf-8") == draft


def test_cut_is_a_property_of_the_source_not_of_its_hash(tmp_path, monkeypatch):
    """Признак среза — поле `MeetingSource` (I2 Sonnet out5): в хеш источника не
    входит, читают его и пересборка, и MCP «Минутки»."""
    live, _, _ = _prep(tmp_path, monkeypatch, None, None)
    plain = "**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n"
    cut, whole = meeting_source.of(live, _cut(plain)), meeting_source.of(live, plain)
    assert cut.cut and not whole.cut and cut.sha() == whole.sha()


def test_any_minutes_writer_records_the_cut_proof(tmp_path, monkeypatch):
    """I2 Sonnet = M1 Haiku out5: общий писатель производных (MCP «Минутки») пишет
    ключ среза тем же паспортом; без среза — пустым, старое доказательство гаснет."""
    live, mpath, _ = _prep(tmp_path, monkeypatch, None, None)
    src = "a" * 64
    live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n", src, cut=True)
    meta = live_sidecar.read(live)
    assert meta["minutes_cut_source_sha256"] == meta["minutes_source_sha256"] == src
    live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки 2\n", src, cut=False)
    assert not rebuild_transcript._built_from_a_cut_final(live_sidecar.read(live))
    mcp = (SRC / "mcp_server.py").read_text(encoding="utf-8")
    assert "cut=source.cut)" in mcp[mcp.index('"minutes", out, source.sha()'):], "MCP передаёт признак среза"


def test_minutes_written_by_mcp_during_rebuild_are_judged_by_a_fresh_passport(tmp_path, monkeypatch):
    """I2 Opus: MCP «Минутки» записали минутки с бытом, пока шли STT и диаризация, —
    пересборка судит их по паспорту на момент решения, а не по снимку начала: при
    отказе модели и срезе они не перештамповываются и не остаются на месте."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- ужин и мусор\n", "b" * 64, cut=False)
    _FakeLLM.fail = True
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and not mpath.exists()
    assert "ужин и мусор" in (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8"), "перенос, а не удаление (M5 DS)"


def test_minutes_written_by_mcp_while_the_model_runs_are_judged_at_decision_time(tmp_path, monkeypatch):
    """C1 Haiku проверки правок Opus: MCP «Минутки» записали минутки с бытом во
    время вызова модели, модель отказала — минутки уходят в .prev/, не остаются."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))

    def mcp_then_fail(self, text, recording_note=None):
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- ужин\n", "b" * 64, cut=False)
        raise RuntimeError("ollama лежит")
        yield ""
    monkeypatch.setattr(_FakeLLM, "minutes", mcp_then_fail)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and not mpath.exists()
    assert "ужин" in (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8")


def test_hand_edit_during_the_model_call_is_left_alone(tmp_path, monkeypatch):
    """Байты, которых паспорт не знает (правка человека за время модели), отсрочка
    не трогает."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))

    def edit_then_fail(self, text, recording_note=None):
        mpath.write_text("# Мои минутки\n", encoding="utf-8")
        raise RuntimeError("ollama лежит")
        yield ""
    monkeypatch.setattr(_FakeLLM, "minutes", edit_then_fail)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and mpath.read_text(encoding="utf-8") == "# Мои минутки\n"


def test_mcp_minutes_are_not_restamped_with_live_session_names(tmp_path, monkeypatch):
    """M4 DS: имена перештамповки — по паспорту решения: минутки MCP (с хешем
    источника) метками живой сессии не штампуются."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n**Собеседник 2** — смета\n", "b" * 64, cut=False)
    _FakeLLM.fail = True
    long_final = "**Инга** [10:21]:\n" + "Смету пришлю. " * 60 + "\n"
    rebuild_transcript.finalize_minutes(live, long_final, meta, {}, rebuild_transcript.minutes_names(meta))
    assert "Инга" not in mpath.read_text(encoding="utf-8")


def test_unreadable_sidecar_does_not_break_the_minutes_decision(tmp_path, monkeypatch):
    """M3 DS: сбой ввода-вывода сайдкара в решении о минутках — снимок, не исключение."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))

    def boom(*a, **k):
        raise OSError("iCloud")
    monkeypatch.setattr(live_sidecar, "sidecar_for", boom)
    assert rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {}) in ("regenerated", "human", "restamped")


def test_mcp_minutes_written_while_the_model_succeeds_are_replaced(tmp_path, monkeypatch):
    """C1 DS = C2 Haiku проверки правок Opus по №733: MCP «Минутки» записали
    минутки по ленте с бытом за время удачного вызова модели — запись по
    снимку проигрывает гонку, но машинные байты не остаются: в .prev/, на месте
    протокол пересборки."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))

    def mcp_then_answer(self, text, recording_note=None):
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- ужин\n", "b" * 64, cut=False)
        yield _FakeLLM.answer
    monkeypatch.setattr(_FakeLLM, "minutes", mcp_then_answer)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "regenerated"
    assert "ужин" not in mpath.read_text(encoding="utf-8")
    assert "ужин" in (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8")


def test_hand_edit_while_the_model_succeeds_is_left_alone(tmp_path, monkeypatch):
    """Та же гонка с правкой человека: байты, которых паспорт не знает, не трогаем."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))

    def edit_then_answer(self, text, recording_note=None):
        mpath.write_text("# Мои минутки\n", encoding="utf-8")
        yield _FakeLLM.answer
    monkeypatch.setattr(_FakeLLM, "minutes", edit_then_answer)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and mpath.read_text(encoding="utf-8") == "# Мои минутки\n"


def test_clean_mcp_minutes_written_while_the_model_fails_keep_their_names(tmp_path, monkeypatch):
    """I1 DS проверки правок Opus: MCP записали чистые минутки (из финала со
    срезом) за время вызова модели, модель отказала — перештамповка не ставит в
    них метки живой сессии."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    source = meeting_source.of(live, _cut(FINAL))

    def mcp_then_fail(self, text, recording_note=None):
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n**Собеседник 2** — смета\n",
                                      source.sha(), cut=True)
        raise RuntimeError("ollama лежит")
        yield ""
    monkeypatch.setattr(_FakeLLM, "minutes", mcp_then_fail)
    rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, rebuild_transcript.minutes_names(meta))
    assert "Инга" not in mpath.read_text(encoding="utf-8")


def test_mcp_minutes_written_while_the_model_fails_with_no_minutes_before_go_away(tmp_path, monkeypatch):
    """Минуток не было, MCP «Минутки» записали их по ленте с бытом за время
    вызова модели, модель отказала — машинные байты не из среза уходят в
    .prev/, как черновик живой ленты: файл судится на этот миг, а не по снимку
    начала (выжившие мутанты двери в CI по №733)."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)

    def mcp_then_fail(self, text, recording_note=None):
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- ужин\n", "b" * 64, cut=False)
        raise RuntimeError("ollama лежит")
        yield ""
    monkeypatch.setattr(_FakeLLM, "minutes", mcp_then_fail)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and not mpath.exists()
    assert "ужин" in (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8")


def test_model_refusal_with_no_minutes_at_all_leaves_nothing(tmp_path, monkeypatch):
    """Минуток не было и нет — переносить нечего, файла не появляется, и лог
    не говорит, что они «сменились» (M3 Sonnet = M1 Haiku n733-mut-out2)."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    said: list[str] = []
    monkeypatch.setattr(rebuild_transcript, "log", said.append)
    _FakeLLM.fail = True
    assert rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {}) == "human"
    assert not mpath.exists() and not (tmp_path / ".prev").exists()
    assert not any("сменились" in m for m in said)


def test_mcp_minutes_stay_when_the_previous_version_cannot_be_kept(tmp_path, monkeypatch):
    """Удачная модель, MCP записали минутки под пересборкой, а копия их байтов
    в `.prev/` не создаётся — заменять без копии нельзя: исход «human», байты
    MCP на месте. `.prev` ломается после первой копии (черновика) — на записи
    по снимку, которую MCP уже обогнали."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    real_write = rebuild_transcript.safe_write.write_text
    armed = []

    def write_text(path, *a, **k):
        if armed and pathlib.Path(path) == mpath:
            armed.clear()
            shutil.rmtree(tmp_path / ".prev")
            (tmp_path / ".prev").write_text("не каталог\n", encoding="utf-8")
        return real_write(path, *a, **k)

    def mcp_then_answer(self, text, recording_note=None):
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- ужин\n", "b" * 64, cut=False)
        armed.append(True)
        yield _FakeLLM.answer
    monkeypatch.setattr(rebuild_transcript.safe_write, "write_text", write_text)
    monkeypatch.setattr(_FakeLLM, "minutes", mcp_then_answer)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and mpath.read_text(encoding="utf-8") == "# Минутки\n- ужин\n"
    assert (tmp_path / ".prev").is_file(), "до второй копии дошло"


def test_mcp_minutes_stay_when_the_replacement_loses_a_second_race(tmp_path, monkeypatch):
    """Та же гонка, и вторая запись по новому снимку тоже проиграла — файл не
    наш: исход «human», байты MCP на месте."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    real_write = rebuild_transcript.safe_write.write_text
    lost = []

    def write_text(path, *a, **k):
        if lost and pathlib.Path(path) == mpath:
            return False
        return real_write(path, *a, **k)

    def mcp_then_answer(self, text, recording_note=None):
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- ужин\n", "b" * 64, cut=False)
        lost.append(True)
        yield _FakeLLM.answer
    monkeypatch.setattr(rebuild_transcript.safe_write, "write_text", write_text)
    monkeypatch.setattr(_FakeLLM, "minutes", mcp_then_answer)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and mpath.read_text(encoding="utf-8") == "# Минутки\n- ужин\n"


def test_final_exactly_at_the_threshold_goes_to_the_model(tmp_path, monkeypatch):
    """Порог строгий: речь ровно в `MINUTES_MIN_CHARS` знаков — уже не короткая."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    monkeypatch.setattr(rebuild_transcript, "MINUTES_MIN_CHARS", len(meeting_source.of(live, FINAL).speech))
    assert rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {}) == "regenerated"
    assert len(_FakeLLM.calls) == 1


@pytest.mark.parametrize("meta", [None, [], "minutes_cut_source_sha256"])
def test_a_passport_that_is_not_a_dict_proves_no_cut(meta):
    assert rebuild_transcript._built_from_a_cut_final(meta) is False


def test_sidecar_failure_while_remembering_a_hash_is_a_refusal(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise OSError("диск отвалился")
    monkeypatch.setattr(live_sidecar, "remember", broken)
    assert rebuild_transcript._remember_sha(tmp_path / "x.md", "minutes_sha256", "a" * 64) is False


def test_minutes_passport_refusal_after_regeneration_is_said_aloud(tmp_path, monkeypatch):
    """Без паспорта следующая перештамповка возьмёт имена живой сессии — об
    отказе говорится громко, пока минутки нетронуты (DS I3 по #551)."""
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    mpath.write_text("# Минутки\n", encoding="utf-8")
    said: list[str] = []
    monkeypatch.setattr(rebuild_transcript, "log", said.append)
    monkeypatch.setattr(live_sidecar, "attest", lambda *a, **k: False)
    rebuild_transcript.record_minutes_passport(live, mpath, "regenerated", FINAL, {})
    assert any("признак их нумерации не записан" in m for m in said)


def test_unchanged_draft_is_moved_even_when_the_oracle_cannot_confirm_it(tmp_path, monkeypatch):
    """Черновик признан машинным в начале (`minutes_sha256` = хеш байтов), за
    вызов модели снимок не сменился — оракул на этой ветке не переспрашивается
    (подмена `_machine_bytes_now` на «не подтверждаю» исхода не меняет, C1 DS
    n733-mut-out): решение начала в силе, быт уходит в .prev/."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    monkeypatch.setattr(rebuild_transcript, "_machine_bytes_now", lambda *a, **k: None)
    _FakeLLM.fail = True
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and not mpath.exists()
    assert (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8") == draft


def test_draft_deleted_during_the_model_call_is_reported(tmp_path, monkeypatch):
    """Черновик удалили, пока модель думала, модель отказала — переносить
    нечего, и об этом сказано: исход «human», файла нет, .prev/ не создан."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    said: list[str] = []
    monkeypatch.setattr(rebuild_transcript, "log", said.append)

    def delete_then_fail(self, text, recording_note=None):
        mpath.unlink()
        raise RuntimeError("ollama лежит")
        yield ""
    monkeypatch.setattr(_FakeLLM, "minutes", delete_then_fail)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert owned == "human" and not mpath.exists() and not (tmp_path / ".prev").exists()
    assert any("сменились под пересборкой" in m for m in said)


def test_a_cut_key_that_differs_from_the_source_key_proves_no_cut():
    """Регенерация из финала без среза сменила `minutes_source_sha256` — ключи
    разошлись, срез не доказан (оба условия нужны, не одно)."""
    meta = {"minutes_cut_source_sha256": "a" * 64, "minutes_source_sha256": "b" * 64}
    assert rebuild_transcript._built_from_a_cut_final(meta) is False
    assert rebuild_transcript._built_from_a_cut_final({"minutes_cut_source_sha256": "a" * 64,
                                                       "minutes_source_sha256": "a" * 64}) is True


@pytest.mark.parametrize("cut, source", [("мусор", "мусор"), ("", "")])
def test_equal_keys_that_are_not_a_hash_prove_no_cut(cut, source):
    """Пара совпала, но это не хеш — срез не доказан (I2 DS n733-mut-out2)."""
    meta = {"minutes_cut_source_sha256": cut, "minutes_source_sha256": source}
    assert not rebuild_transcript._built_from_a_cut_final(meta)


def test_draft_replaced_between_the_decision_and_the_move_stays(tmp_path, monkeypatch):
    """Снимок совпал при решении, а перед переносом сменился — чужие байты не
    уезжают в .prev/: исход «human», файл на месте (I1 Haiku n733-mut-out2)."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nужин и мусор\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))
    real_snapshot = rebuild_transcript.safe_write.stat_snapshot
    after_refusal: list[int] = []

    def stat_snapshot(path):
        if after_refusal and pathlib.Path(path) == mpath:
            after_refusal.append(1)
            if len(after_refusal) > 2:          # вторая сверка снимка в _defer_live_draft
                return ("подменён",)
        return real_snapshot(path)

    def refuse(self, text, recording_note=None):
        after_refusal.append(0)
        raise RuntimeError("ollama лежит")
        yield ""
    monkeypatch.setattr(rebuild_transcript.safe_write, "stat_snapshot", stat_snapshot)
    monkeypatch.setattr(_FakeLLM, "minutes", refuse)
    owned = rebuild_transcript.finalize_minutes(live, _cut(FINAL), meta, {}, {})
    assert len(after_refusal) == 3, "снимок сверялся дважды после отказа"
    assert owned == "human" and mpath.read_text(encoding="utf-8") == draft
    assert not (tmp_path / ".prev").exists()


def test_mcp_minutes_written_without_a_cut_while_the_model_succeeds_stay(tmp_path, monkeypatch):
    """Без среза в финале гонку не судит оракул: минутки MCP, записанные за
    удачный вызов модели, остаются как есть — прежнее поведение (M4 DS
    n733-mut-out2)."""
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nСобеседник 2\nсмета\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))

    def mcp_then_answer(self, text, recording_note=None):
        live_sidecar.write_derivative(live, mpath, "minutes", "# Минутки\n- смета\n", "b" * 64, cut=False)
        yield _FakeLLM.answer
    monkeypatch.setattr(_FakeLLM, "minutes", mcp_then_answer)
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned == "human" and mpath.read_text(encoding="utf-8") == "# Минутки\n- смета\n"

"""Минутки после пересборки — не черновик и без «Собеседник N» (№146).

Встреча 08:45 31.08: стенограмма пересобрана, имена подставлены, а минутки
рядом остались с шапкой «черновик, встреча идёт» и участниками «Собеседник 2,
Собеседник 4» — человек читал устаревший документ с ярлыками. Перештамповка
точечная: маркер и метки; ручные правки человека не перегенерируются.
"""
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

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


def test_rebuild_wires_live_names_into_restamp():
    """Контракт на проводку: rebuild обязан отдавать в restamp именно словарь
    ЖИВОЙ сессии — откат на пересборочный `names` возвращал бы Critical со
    смешанной нумерацией при зелёных юнитах (GLM r2 M4)."""
    src = (SRC / "rebuild_transcript.py").read_text(encoding="utf-8")
    fn = src[src.index("def rebuild("):src.index("def finalize_minutes(")]
    assert "finalize_minutes(live, final_text, meta, cfg, live_session_names(meta))" in fn, (
        "в минутки должны идти имена живой сессии (live.json), не пересборочные")
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
    import safe_write
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


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _FakeLLM:
    """Модель на диске не нужна: возвращаем заготовку, считаем вызовы."""
    calls: list[str] = []
    answer = "# Минутки\n**Участники:** Инга, Марк\n## Поручения\n- [ ] **Инга** — прислать смету — до 05.09\n"
    fail = False

    def __init__(self, cfg):
        pass

    def minutes(self, text):
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
    assert owned is True
    assert _FakeLLM.calls == [FINAL], "минутки — по ФИНАЛЬНОЙ стенограмме"
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
    fn = src[src.index("def rebuild("):src.index("def finalize_minutes(")]
    i_fin = fn.index("machine_owned = finalize_minutes(")
    i_can = fn.index("canonize_file(mpath, cfg)")
    i_sha = fn.index("_remember_minutes_sha(live, _sha(mpath.read_text(")
    assert i_fin < i_can < i_sha, "порядок: finalize → canonize → хеш по байтам с диска"
    assert "if machine_owned:" in fn[i_can:i_sha], "хеш — только для машинного файла"
    body = src[src.index("def finalize_minutes("):src.index("def restamp_minutes(")]
    assert "_remember_minutes_sha(" not in body, "finalize_minutes сам хеш не пишет"


def test_hand_edited_minutes_are_only_restamped(tmp_path, monkeypatch):
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\nУчастники: Собеседник 2\n"
    edited = draft + "\nМоя пометка руками.\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, edited, _sha(draft))
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {"Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert owned is False, "правленный руками файл — не машинный, хеш не трогать"
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
    assert owned is True, ("после транзиентного отказа файл остаётся машинным: хеш обновится, "
                           "и следующая пересборка сможет перегенерировать (DS r1 Imp-1)")


def test_document_that_appeared_during_generation_is_not_overwritten(tmp_path, monkeypatch):
    # GLM Critical по #483: минуток не было, за 13–60 с генерации их создал
    # mcp «Минутки» — регенерат не должен лечь поверх и объявить чужой
    # текст «своим».
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)

    class _LateWriter(_FakeLLM):
        def minutes(self, text):
            mpath.write_text("# Минутки от mcp\n", encoding="utf-8")
            yield _FakeLLM.answer

    import llm
    monkeypatch.setattr(llm, "LLM", _LateWriter)
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned is False, "чужой документ — не машинный, хеш не снимаем"
    assert mpath.read_text(encoding="utf-8") == "# Минутки от mcp\n"


def test_model_gets_speech_without_the_notes_tail(tmp_path, monkeypatch):
    # GLM Imp-4: хвост «Ко-мышление» — мысли модели, не речь; кнопка
    # «Протокол» его не видит, и регенерация — тоже. Порог считается по речи.
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)
    notes = transcript.NOTES_HEAD + " (📌 КТ · 💎 факты · 💭 мысли)\n> 💎 факт, которого не звучало\n" * 40
    rebuild_transcript.finalize_minutes(live, FINAL + notes, meta, {}, {})
    assert _FakeLLM.calls == [FINAL], "в промпт ушла только речь"
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
    assert owned is True and _FakeLLM.calls == []
    assert "Черновик" in out and "Инга" in out and transcript.MINUTES_DRAFT_MARK not in out


def test_foreign_overwrite_of_draft_plus_model_failure_is_not_claimed(tmp_path, monkeypatch):
    # GLM r2 Imp-1: черновик был машинным, за время ожидания/вызова файл
    # подменил mcp, модель упала → restamp (нет маркера) без записи; файл
    # не наш — хеш снимать нельзя.
    draft = transcript.MINUTES_DRAFT_MARK + "\n# Черновик\n"
    live, mpath, meta = _prep(tmp_path, monkeypatch, draft, _sha(draft))

    class _OverwriteThenFail(_FakeLLM):
        def minutes(self, text):
            mpath.write_text("# Минутки от mcp\n", encoding="utf-8")
            raise RuntimeError("ollama лежит")
            yield  # noqa: unreachable

    import llm
    monkeypatch.setattr(llm, "LLM", _OverwriteThenFail)
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned is False
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
    assert owned is True and "Инга" in mpath.read_text(encoding="utf-8")


def test_undecodable_minutes_do_not_abort_and_are_not_owned(tmp_path, monkeypatch):
    # GLM Min-6: не-UTF8 файл из редактора — перештамповка, не падение
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, "abc")
    mpath.write_bytes(b"\xff\xfe<")
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned is False and _FakeLLM.calls == []
    assert mpath.read_bytes() == b"\xff\xfe<"


def test_model_failure_with_late_foreign_file_does_not_claim_it(tmp_path, monkeypatch):
    # DS r2 Imp-1: минуток не было, за окно вызова файл создал чужой
    # процесс, модель упала → restamp ничего не писал, файл не машинный,
    # хеш снимать нельзя (иначе следующая пересборка регенерирует поверх).
    live, mpath, meta = _prep(tmp_path, monkeypatch, None, None)

    class _LateThenFail(_FakeLLM):
        def minutes(self, text):
            mpath.write_text("# Минутки от mcp\n", encoding="utf-8")
            raise RuntimeError("ollama лежит")
            yield  # noqa: unreachable — генератор

    import llm
    monkeypatch.setattr(llm, "LLM", _LateThenFail)
    owned = rebuild_transcript.finalize_minutes(live, FINAL, meta, {}, {})
    assert owned is False
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
    # хеш пишется в тот же найденный файл
    rebuild_transcript._remember_minutes_sha(titled, "def")
    saved = json.loads((tmp_path / "2026-09-02_102112.md.live.json").read_text(encoding="utf-8"))
    assert saved["minutes_sha256"] == "def" and saved["names"] == {"Собеседник 2": "Инга"}
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

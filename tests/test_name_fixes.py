"""Имена меток стенограммы после ревизии — строгий раздел и перештамповка (№239)."""
import errno
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import graph_updater  # noqa: E402
import live_sidecar  # noqa: E402
import name_fixes as nf  # noqa: E402

REVIEW = """# Ревизия
## Ошибки
- 🔴 Метка «Сергей» — это Мария, ведущая.
## Исправления имён
- **Сергей** → **Мария** — основание: два обращения «Маш», на оба отвечает эта дорожка
- **Собеседник 3** → **Пётр** — основание: сам представился
- **Юля** → **Юля**
- **Иван** → **Ольга** — основание: метки нет
(не уверен)
## Восстановленные поручения
- [ ] **Мария** — прислать сводку
"""

SPEECH = """# Встреча 2026-09-11_1533 — Планёрка

**Сергей** [15:33]:
Начнём.

**Юля** [15:33]:
Маш, нормально всё?

**Сергей** [15:34 – 15:35]:
Да.

**Собеседник 3** [15:35]:
Я Пётр, из соседней команды.

**Владелец** [15:36]:
Угу.

---
## Ко-мышление
**Сергей** [15:40]:
заметка модели про Сергея
"""

MINUTES = ("# Минутки встречи\n**Дата/время:** — **Участники:** Сергей, Владелец, Юля, Собеседник 3\n\n"
           "## Поручения\n- [ ] **Сергей** — согласовать план\n")


def _world(tmp_path, sidecar=True):
    live = tmp_path / "2026-09-11_1533_Планёрка.md"
    live.write_text(SPEECH, encoding="utf-8")
    mpath = tmp_path / "2026-09-11_1533_Планёрка_minutes.md"
    mpath.write_text(MINUTES, encoding="utf-8")
    if sidecar:
        (tmp_path / "2026-09-11_1533_Планёрка.md.live.json").write_text(json.dumps({
            "transcript_sha256": live_sidecar.sha(SPEECH), "minutes_sha256": live_sidecar.sha(MINUTES)}),
            encoding="utf-8")
    rev = tmp_path / "2026-09-11_1533_Планёрка_ревизия_claude.md"
    rev.write_text(REVIEW, encoding="utf-8")
    return live, mpath, rev


def test_name_fixes_take_only_the_strict_section_and_the_strict_form():
    dropped: list[str] = []
    fixes = nf.name_fixes(REVIEW, dropped=dropped)
    assert fixes == [("Сергей", "Мария", "два обращения «Маш», на оба отвечает эта дорожка"),
                     ("Собеседник 3", "Пётр", "сам представился"),
                     ("Юля", "Юля", ""), ("Иван", "Ольга", "метки нет")]
    assert dropped == ["(не уверен)"], "комментарий в скобках — шум, как у моста поручений"
    assert nf.name_fixes("## Восстановленные поручения\n- [ ] **Мария** — x\n") == []
    assert nf.section_present("**Исправления имён:**") and not nf.section_present("имена исправлять не надо")


def test_plan_drops_the_owner_the_same_name_placeholders_and_unknown_labels():
    dropped: list[str] = []
    # «Мари�я» — имя из ревизии, прочитанной с заменой нечитаемых байтов:
    # в заголовки реплик и узел Люди такое не пишем (№263)
    fixes = [("Сергей", "Мария", ""), ("Юля", "Юля", ""), ("Владелец", "Кто-то", ""),
             ("Собеседник 3", "Собеседник 4", ""), ("Иван", "Ольга", ""), ("Сергей", "Анна", ""),
             ("Юля", "[[Люди/Юля]]", ""), ("Юля", "Мари\ufffdя", ""), ("Собеседник 3", "Пётр", "")]
    mapping = nf.plan(fixes, headers={"Сергей", "Юля", "Собеседник 3", "Владелец"},
                      protected={"Владелец"}, dropped=dropped)
    assert mapping == {"Сергей": "Мария", "Собеседник 3": "Пётр"}
    unfit = "имя не годится (заглушка, разметка или длина)"
    assert [d.split(" — ")[1] for d in dropped] == [
        "то же имя", "метка владельца (канал микрофона) не переименовывается",
        unfit, "такой метки в заголовках реплик нет",
        "метка уже исправлена другой строкой", unfit,
        "в имени нечитаемый байт, ревизию читали с заменой"]
    # обмен меток — одним проходом, без каскада: и в заголовках, и в шапке
    # участников (GLM r1 Critical, DS r1 I1 по #548), цепочка A→B→C — тоже
    text, n = nf.rename_headers("Участники (звучали в разговоре): А, Б\n\n**А** [1:00]:\nа\n\n**Б** [1:01]:\nб\n",
                                {"А": "Б", "Б": "А"})
    assert n == 2 and text == "Участники (звучали в разговоре): Б, А\n\n**Б** [1:00]:\nа\n\n**А** [1:01]:\nб\n"
    assert nf.rename_participants("**Участники:** Аня, Боря\n", {"Аня": "Боря", "Боря": "Вера"}) == "**Участники:** Боря, Вера\n"
    # дефис — часть слова; «Участники:» в конце строки не тянет за собой следующую (DS M5, GLM M4)
    assert nf.rename_participants("**Участники:** Анна-Мария, Анна\n", {"Анна": "Мария"}) == "**Участники:** Анна-Мария, Мария\n"
    assert nf.rename_participants("**Участники:**\n- [ ] **Аня** — x\n", {"Аня": "Боря"}) == "**Участники:**\n- [ ] **Аня** — x\n"
    # цель — метка владельца/«Я»: не применяется (DS I4, GLM M5)
    dropped.clear()
    assert nf.plan([("Собеседник 3", "Владелец", ""), ("Сергей", "Я", "")], headers={"Собеседник 3", "Сергей"},
                   protected={"Владелец", "Я"}, dropped=dropped) == {}
    assert all("целевое имя" in d for d in dropped)
    # слияние в существующую чужую дорожку — не делаем (критика DS r2): два голоса под одним именем без отката
    dropped.clear()
    assert nf.plan([("Сергей", "Мария", "")], headers={"Сергей", "Мария"}, protected=set(), dropped=dropped) == {}
    assert "слияние дорожек не делаем" in dropped[0]
    # обмен — не слияние: обе дорожки переименованы; отклонённая правка цели отменяет и слияние в неё (DS r2 I1, GLM r2 M1)
    assert nf.plan([("А", "Б", ""), ("Б", "А", "")], headers={"А", "Б"}, protected=set()) == {"А": "Б", "Б": "А"}
    dropped.clear()
    assert nf.plan([("Б", "Я", ""), ("А", "Б", "")], headers={"А", "Б"}, protected={"Я"}, dropped=dropped) == {}
    assert any("её правка отклонена" in d for d in dropped)
    assert nf.plan([("", "Мария", "")], headers={""}, protected=set(), dropped=dropped) == {} and "пустая метка" in dropped[-1]
    # цепочка А→Б→В→Г с живой «Г»: отказ каскадом до неподвижной точки, иначе «А → Б»
    # сливал бы А в живую дорожку Б (аудит 13.09, GLM M1 по зоне контроля)
    dropped.clear()
    assert nf.plan([("А", "Б", ""), ("Б", "В", ""), ("В", "Г", "")], headers={"А", "Б", "В", "Г"},
                   protected=set(), dropped=dropped) == {}
    assert len(dropped) == 3 and all("другой дорожки" in d for d in dropped)
    # основание в скобках — та же строгая форма (DS M6); скобка без слова «основание» — не форма (DS r2 M1)
    assert nf.name_fixes("## Исправления имён\n- **Сергей** → **Мария** (основание: два обращения)\n") == [("Сергей", "Мария", "два обращения")]
    dropped.clear()
    assert nf.name_fixes("## Исправления имён\n- **Сергей** → **Мария** (из обращения)\n", dropped=dropped) == []
    assert dropped == ["**Сергей** → **Мария** (из обращения)"]


def test_apply_restamps_headers_and_participants_keeps_prev_and_sha(tmp_path):
    live, mpath, rev = _world(tmp_path)
    cfg = {"sufler": {"user_name": "Владелец"}}
    dropped: list[str] = []
    mapping, heads, parts = nf.apply(rev, live, cfg, dropped=dropped)
    assert mapping == {"Сергей": "Мария", "Собеседник 3": "Пётр"} and heads == 3 and parts
    text = live.read_text(encoding="utf-8")
    speech = text.split("\n---\n", 1)[0]
    assert "**Мария** [15:33]:" in speech and "**Мария** [15:34 – 15:35]:" in speech and "**Пётр** [15:35]:" in speech
    assert "**Сергей**" not in speech and "**Владелец** [15:36]:" in speech and "Маш, нормально всё?" in speech
    assert "**Сергей** [15:40]:" in text, "хвост «Ко-мышление» — заметки модели, не речь"
    assert (tmp_path / ".prev" / live.name).read_text(encoding="utf-8") == SPEECH, "версия до правки — в .prev"
    meta = json.loads((tmp_path / "2026-09-11_1533_Планёрка.md.live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == live_sidecar.sha(text), "машинный текст: хеш обновлён, пересборка не сочтёт правкой руками"
    minutes = mpath.read_text(encoding="utf-8")
    assert "**Участники:** Мария, Владелец, Юля, Пётр" in minutes
    assert "- [ ] **Сергей** — согласовать план" in minutes, "поручения не переименовываются наугад — их снимает и возвращает ревизия"
    assert meta["minutes_sha256"] == live_sidecar.sha(minutes)
    assert dropped == ["(не уверен)", "«Юля → Юля» — то же имя", "«Иван → Ольга» — такой метки в заголовках реплик нет"]
    assert (tmp_path / ".prev" / mpath.name).read_text(encoding="utf-8") == MINUTES, "минутки до правки — тоже в .prev (DS I3)"
    # повтор — нечего менять
    again = nf.apply(rev, live, cfg)
    assert again[1] == 0 and again[0] == {}, again


def test_minutes_os_error_reason_carries_no_machine_path(tmp_path, monkeypatch):
    """Минутки не записались — в dropped причина ОС без пути: эти строки
    ложатся в «## Не применено» файла ревизии, а ревизия уходит в облако."""
    live, mpath, rev = _world(tmp_path)

    def denied(*_a, **_k):
        raise OSError(errno.EACCES, os.strerror(errno.EACCES), str(mpath))

    monkeypatch.setattr(nf, "restamp_minutes", denied)
    dropped: list[str] = []
    mapping, heads, parts = nf.apply(rev, live, {"sufler": {"user_name": "Владелец"}}, dropped=dropped)
    assert mapping and heads == 3 and not parts
    line = f"{mpath.name}: участники минуток не перештампованы ({os.strerror(errno.EACCES)})"
    assert line in dropped
    assert nf.record_unapplied(rev, dropped) > 0
    assert str(tmp_path) not in rev.read_text(encoding="utf-8")

def test_non_utf8_meeting_file_skips_names_without_raising(tmp_path):
    """DS r1 I2 по #548: стенограмма в cp1251 (правил чужой редактор) не
    должна ронять мост поручений — имена пропускаются со строкой в dropped."""
    live, mpath, rev = _world(tmp_path)
    live.write_bytes("**Сергей** [15:33]:\nПривет, Юля\n".encode("cp1251"))
    dropped: list[str] = []
    assert nf.apply(rev, live, {"sufler": {}}, dropped=dropped) == ({}, 0, False)
    assert dropped and dropped[0].startswith(live.name) and "не в UTF-8" in dropped[0]
    # битые только минутки — стенограмма перештамповывается, минутки нет, файл назван (критика GLM r2)
    live.write_text(SPEECH, encoding="utf-8")
    mpath.write_bytes("**Участники:** Сергей\n".encode("cp1251"))
    dropped.clear()
    mapping, heads, parts = nf.apply(rev, live, {"sufler": {}}, dropped=dropped)
    assert heads == 3 and not parts and any(d.startswith(mpath.name) for d in dropped)
    # план без правки файлов — для моста поручений в режиме чтения (DS r2 I2)
    live.write_text(SPEECH, encoding="utf-8")
    assert nf.planned(rev, live, {"sufler": {"user_name": "Владелец"}}) == {"Сергей": "Мария", "Собеседник 3": "Пётр"}
    assert live.read_text(encoding="utf-8") == SPEECH


def test_hand_edited_transcript_is_restamped_but_stays_hand_edited(tmp_path):
    live, mpath, rev = _world(tmp_path)
    live.write_text(SPEECH + "\nправка руками\n", encoding="utf-8")
    mapping, heads, _ = nf.apply(rev, live, {"sufler": {"user_name": "Владелец"}})
    assert heads == 3
    meta = json.loads((tmp_path / "2026-09-11_1533_Планёрка.md.live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == live_sidecar.sha(SPEECH), "хеш прежний — для пересборки текст остаётся ручным"
    # без сайдкара — правка проходит, сайдкар не заводится
    (tmp_path / "b").mkdir()
    live2, _, rev2 = _world(tmp_path / "b", sidecar=False)
    assert nf.apply(rev2, live2, {"sufler": {}})[1] == 3
    assert not (tmp_path / "b" / "2026-09-11_1533_Планёрка.md.live.json").exists()


def test_l4_prompt_names_the_strict_section_in_both_modes():
    for may_edit in (False, True):
        prompt = graph_updater.cloud_enrich_prompt(transcript_name="x.md", folder=Path("."), graph=Path("."),
                                                   rev_name="r.md", stamp="2026-09-05_1413", may_edit=may_edit, context="")
        assert "## Исправления имён" in prompt and "**Метка** → **Имя** — основание: …" in prompt
        assert "Раздел применит Чароит, если правка графа включена и перенос сверен" in prompt
        assert "иначе его применяет человек" in prompt
        assert "пиши ему имя с фамилией" in prompt
        assert "переименует заголовки реплик" not in prompt
        assert ("из узла Люди с ошибочным именем убери строку" in prompt) is may_edit


def test_restamp_refuses_to_overwrite_files_changed_underneath(tmp_path, monkeypatch):
    """Гейт expect с повтором на обеих записях (аудит зон 12.09, зона 4; DS I3 по
    #553): файл, сменившийся между чтением и записью дважды, не затирается.
    Стенограмма — LostRace наружу целиком (ничего не применено, лог не скажет
    «исправлено»); минутки после удачной стенограммы — строка с PREFIX в dropped."""
    import pytest
    import review_bridge
    live, mpath, rev = _world(tmp_path)
    cfg = {"sufler": {"user_name": "Владелец"}}
    owned = nf._machine_owned
    hits: list[str] = []

    def clobber(targets):
        def wrapped(live_, key, text):
            out = owned(live_, key, text)
            if key in targets:
                hits.append(key)
                # чужая запись ДОПИСЫВАЕТ к прежнему тексту: заголовки/участники для
                # перештамповки остаются, и вторая попытка тоже хочет писать; своя
                # длина на каждую попытку — одинаковые байты в один тик mtime это
                # заявленная граница safe_write
                target, base = ((live, SPEECH) if key == "transcript_sha256" else (mpath, MINUTES))
                target.write_text(base + "\nчужая правка\n" * len(hits), encoding="utf-8")
            return out
        return wrapped

    monkeypatch.setattr(nf, "_machine_owned", clobber({"transcript_sha256"}))
    dropped: list[str] = []
    with pytest.raises(review_bridge.LostRace):
        nf.apply(rev, live, cfg, dropped=dropped)
    text = live.read_text(encoding="utf-8")
    assert text.startswith(SPEECH) and "чужая правка" in text and "**Мария**" not in text
    assert hits == ["transcript_sha256"] * 2, "две попытки на стенограмму"
    assert not (tmp_path / ".prev" / live.name).exists(), ".prev пишется только после удачной записи (DS M1, круг 2)"
    assert mpath.read_text(encoding="utf-8") == MINUTES, "минутки не тронуты, пока стенограмма не записана"
    meta = json.loads((tmp_path / "2026-09-11_1533_Планёрка.md.live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == live_sidecar.sha(SPEECH), "хеш не обновлён — запись не состоялась"

    live, mpath, rev = _world(tmp_path)
    hits.clear()
    monkeypatch.setattr(nf, "_machine_owned", clobber({"minutes_sha256"}))
    dropped = []
    mapping, heads, parts = nf.apply(rev, live, cfg, dropped=dropped)
    assert mapping and heads == 3 and parts is False
    assert "**Мария** [15:33]:" in live.read_text(encoding="utf-8")
    mtext = mpath.read_text(encoding="utf-8")
    assert mtext.startswith(MINUTES) and "чужая правка" in mtext and "Мария" not in mtext.split("\n", 3)[0:3].__str__()
    assert (tmp_path / ".prev" / live.name).read_text(encoding="utf-8") == SPEECH, ".prev стенограммы — исходник, не чужая версия"
    assert not (tmp_path / ".prev" / mpath.name).exists(), "минутки не записаны — .prev минуток нет"
    assert any(d.startswith(review_bridge.LostRace.PREFIX) and "участники не тронуты" in d for d in dropped), dropped


def _prompt_swap_lines(prompt: str) -> list[str]:
    """Строки примера обмена — из ТЕКСТА промпта, а не из константы: тест держит
    то, что прочтёт модель."""
    lines = prompt.split("\n")
    at = next(i for i, ln in enumerate(lines) if ln.endswith("Пример формы для обмена (метки условные):"))
    out = []
    for ln in lines[at + 1:]:
        if not ln.startswith("- **"):
            break
        out.append(ln)
    return out


def test_prompt_swap_example_is_applied_whole_in_one_pass(tmp_path):
    """Обещание промпта (№560) держит поведение: пример обмена из промпта обоих
    режимов разбирается name_fixes(), plan() принимает его целиком, а apply()
    меняет дорожки местами в заголовках реплик, шапке участников стенограммы и
    строке участников минуток — без слияния в одно имя."""
    for may_edit in (False, True):
        prompt = graph_updater.cloud_enrich_prompt(transcript_name="x.md", folder=Path("."), graph=Path("."),
                                                   rev_name="r.md", stamp="2026-09-05_1413", may_edit=may_edit, context="")
        assert "одновременно, одним проходом" in prompt
        example = _prompt_swap_lines(prompt)
        assert len(example) == 2, example
        dropped: list[str] = []
        fixes = nf.name_fixes("## Исправления имён\n" + "\n".join(example) + "\n", dropped=dropped)
        assert not dropped and len(fixes) == 2
        (a, b, _), (b2, a2, _) = fixes
        assert (a, b) == (a2, b2) and a != b, "пример — обмен двух меток"

        d = tmp_path / str(may_edit)
        d.mkdir()
        live = d / "2026-09-11_1533_Планёрка.md"
        live.write_text(f"# Встреча\nУчастники (звучали в разговоре): {a}, {b}\n\n"
                        f"**{a}** [15:33]:\nпервая\n\n**{b}** [15:34]:\nвторая\n", encoding="utf-8")
        (d / "2026-09-11_1533_Планёрка_minutes.md").write_text(
            f"# Минутки\n**Участники:** {a}, {b}\n", encoding="utf-8")
        rev = d / "2026-09-11_1533_Планёрка_ревизия_claude.md"
        rev.write_text("# Ревизия\n## Исправления имён\n" + "\n".join(example) + "\n", encoding="utf-8")
        mapping, heads, parts = nf.apply(rev, live, {"sufler": {}})
        assert mapping == {a: b, b: a} and heads == 2 and parts
        assert live.read_text(encoding="utf-8") == (
            f"# Встреча\nУчастники (звучали в разговоре): {b}, {a}\n\n"
            f"**{b}** [15:33]:\nпервая\n\n**{a}** [15:34]:\nвторая\n")
        assert (d / "2026-09-11_1533_Планёрка_minutes.md").read_text(encoding="utf-8") == \
            f"# Минутки\n**Участники:** {b}, {a}\n"

        # скопированный на настоящую встречу пример не применяется: таких меток нет
        dropped.clear()
        assert nf.plan(fixes, headers={"Собеседник 1", "Собеседник 2"}, protected=set(), dropped=dropped) == {}
        assert len(dropped) == 2 and all("такой метки в заголовках реплик нет" in x for x in dropped)


# Каждое правило абзаца для модели — вызов plan(), который его держит: фраза
# промпта без живого поведения (или поведение без фразы) краснеет здесь (№560,
# выход r1: первая редакция обещала «связка — только целиком», а plan() снимает
# лишь строку, чьё имя — метка отклонённой дорожки).
PROMPT_RULES = [
    ("пиши обе", [("А", "Б", ""), ("Б", "А", "")], {"А", "Б"}, set(), {"А": "Б", "Б": "А"}),
    ("так же цепочка", [("А", "Б", ""), ("Б", "В", "")], {"А", "Б"}, set(), {"А": "Б", "Б": "В"}),
    ("применится, только если строка этой дорожки тоже пройдёт",
     [("Б", "Я", ""), ("А", "Б", "")], {"А", "Б"}, {"Я"}, {}),
    ("применится, только если строка этой дорожки тоже пройдёт",   # хвост без головы живёт
     [("А", "Я", ""), ("Б", "В", "")], {"А", "Б"}, {"Я"}, {"Б": "В"}),
    ("Одна строка на метку", [("А", "В", ""), ("А", "Г", "")], {"А"}, set(), {"А": "В"}),
    ("которую ты не переименовываешь", [("А", "Б", "")], {"А", "Б"}, set(), {}),
    ("метку владельца (его микрофон) не переименовывай", [("Я", "В", "")], {"Я", "А"}, {"Я"}, {}),
    ("его имя другой дорожке не давай", [("А", "Я", "")], {"Я", "А"}, {"Я"}, {}),
    ("Голое имя, совпавшее с именем владельца встречи, не применится.",
     [("Собеседник 1", "Имя", "")], {"Собеседник 1"},
     nf.NameGuard(mic=frozenset({"Я"}), owner="Имя Фамилия", bare="Имя"), {}),
]


def test_every_prompt_rule_is_what_plan_does():
    for phrase, fixes, headers, protected, expected in PROMPT_RULES:
        assert phrase in nf.PROMPT_PARAGRAPH, phrase
        assert nf.plan(fixes, headers=headers, protected=protected) == expected, phrase


def test_plan_separates_bare_owner_name_full_name_and_mic():
    """Голое имя владельца, его полное имя на чужой дорожке и метка микрофона
    отклоняются разными причинами. Тёзка с фамилией применяется. Защита
    первого слова как метки остаётся."""
    guard = nf.guard_for({"sufler": {"user_name": "Имя Фамилия"}})
    headers = {"Собеседник 1", "Собеседник 2", "Я", "Имя"}
    dropped: list[str] = []
    assert nf.plan([("Собеседник 1", "Имя", "")], headers, guard, dropped) == {}
    assert dropped == ["«Собеседник 1 → Имя» — голое имя владельца: нужна фамилия"]
    dropped.clear()
    assert nf.plan([("Собеседник 1", "Имя Фамилия", "")], headers, guard, dropped) == {}
    assert dropped == ["«Собеседник 1 → Имя Фамилия» — целевое имя — полное имя владельца"]
    assert "микрофона" not in dropped[0]
    dropped.clear()
    assert nf.plan([("Собеседник 1", "Я", "")], headers, guard, dropped) == {}
    assert dropped == ["«Собеседник 1 → Я» — целевое имя — метка владельца (канал микрофона)"]
    dropped.clear()
    assert nf.plan([("Я", "Фамилия", "")], headers, guard, dropped) == {}
    assert dropped == ["«Я → Фамилия» — метка владельца (канал микрофона) не переименовывается"]
    # канал владельца подписан его полным именем — это метка микрофона, не «голое имя»
    dropped.clear()
    assert nf.plan([("Имя Фамилия", "Фамилия", "")], headers | {"Имя Фамилия"}, guard, dropped) == {}
    assert dropped == ["«Имя Фамилия → Фамилия» — метка владельца (канал микрофона) не переименовывается"]
    # первое слово как метка дорожки по-прежнему не переименовывается, и причина не про микрофон
    dropped.clear()
    assert nf.plan([("Имя", "Фамилия", "")], headers, guard, dropped) == {}
    assert dropped == ["«Имя → Фамилия» — голое имя владельца не переименовывается"]
    # первое слово «Фамилия» с именем владельца не совпадает — это не тёзка,
    # и строка всё равно применяется: не голое имя и не полное имя владельца
    assert nf.plan([("Собеседник 1", "Фамилия Имя", "")], headers, guard) == {"Собеседник 1": "Фамилия Имя"}
    # «Я» как первое слово полного имени остаётся причиной канала, не просьбой дописать фамилию
    odd = nf.guard_for({"sufler": {"user_name": "Я Фамилия"}})
    dropped.clear()
    assert nf.plan([("Собеседник 1", "Я", "")], {"Собеседник 1"}, odd, dropped) == {}
    assert "целевое имя — метка владельца (канал микрофона)" in dropped[0]
    assert "голое имя" not in dropped[0]
    # имя владельца совпало с нейтральной меткой — канал микрофона «Я», а
    # дорожка «Собеседник 2» носит имя владельца: её не переименовываем, и
    # причина — про имя, не про канал
    apart = nf.guard_for({"sufler": {"user_name": "Собеседник 2"}})
    dropped.clear()
    assert nf.plan([("Собеседник 2", "Пётр Петров", "")], {"Собеседник 2", "Я"}, apart, dropped) == {}
    assert dropped == ["«Собеседник 2 → Пётр Петров» — полное имя владельца не переименовывается"]


_ROW = "- Собеседник 1 → Имя — голое имя владельца: нужна фамилия"


def test_render_unapplied_appends_the_exact_block():
    """Вход → точный текст. Пустой список текст не меняет. Заголовок модели
    и жирная подпись под ним остаются, свой блок — в конце."""
    row = _ROW
    block = "## Не применено\n" + row + "\n"
    cases = [
        ("", [row], block),
        ("# Ревизия\nстрока\n", [row], "# Ревизия\nстрока\n\n" + block),
        ("# Ревизия\nстрока", [row], "# Ревизия\nстрока\n\n" + block),
        ("# Ревизия\nстрока\n\n\n", [row], "# Ревизия\nстрока\n\n" + block),
        ("# Ревизия\nстрока\n", [], "# Ревизия\nстрока\n"),
        ("# Ревизия\nстрока", [], "# Ревизия\nстрока"),
    ]
    model = ("# Ревизия\n"
             "## Исправления имён\n"
             "- **Собеседник 1** → **Имя** — основание: звучало\n"
             "\n"
             "## Не применено\n"
             "- пункт модели\n"
             "\n"
             "**Что сделано в графе:**\n"
             "- пункт подписи\n")
    cases.append((model, [row],
                  "# Ревизия\n"
                  "## Исправления имён\n"
                  "- **Собеседник 1** → **Имя** — основание: звучало\n"
                  "\n"
                  "## Не применено\n"
                  "- пункт модели\n"
                  "\n"
                  "**Что сделано в графе:**\n"
                  "- пункт подписи\n"
                  "\n" + block))
    for text, rows, expected in cases:
        assert nf.render_unapplied(text, rows) == expected
    bare = ("# Ревизия\n## Исправления имён\n"
            "- **Собеседник 1** → **Мария** — основание: звучало\n")
    appended = nf.render_unapplied(bare, [row])
    assert nf.name_fixes(appended) == nf.name_fixes(bare)
    assert nf.name_fixes(nf.render_unapplied(model, [row])) == nf.name_fixes(model)


def test_record_unapplied_appends_and_an_empty_list_does_not_rewrite(tmp_path):
    rev = tmp_path / "ревизия.md"
    original = ("# Ревизия\n## Исправления имён\n"
                "- **Собеседник 1** → **Имя** — основание: звучало\n")
    rev.write_bytes(original.encode("utf-8"))
    dropped = ["«Собеседник 1 → Имя» — голое имя владельца: нужна фамилия", ""]
    assert nf.record_unapplied(rev, dropped) == 1
    assert rev.read_text(encoding="utf-8") == nf.render_unapplied(original, [_ROW])
    stamp = rev.stat().st_mtime_ns
    raw = rev.read_bytes()
    assert nf.record_unapplied(rev, []) == 0
    assert rev.read_bytes() == raw and rev.stat().st_mtime_ns == stamp
    bad = tmp_path / "битая.md"
    blob = b"\xff\xfe"
    bad.write_bytes(blob)
    bad_stamp = bad.stat().st_mtime_ns
    assert nf.record_unapplied(bad, []) == 0
    assert bad.read_bytes() == blob and bad.stat().st_mtime_ns == bad_stamp


def test_record_unapplied_on_non_utf8_raises_mangled_and_keeps_bytes(tmp_path):
    import pytest
    import review_bridge
    rev = tmp_path / "ревизия.md"
    raw = "проза ".encode("utf-8") + b"\xff" + "\n## Исправления имён\n".encode("utf-8")
    rev.write_bytes(raw)
    with pytest.raises(review_bridge.MangledFile) as exc:
        nf.record_unapplied(rev, ["«Собеседник 1 → Имя» — голое имя владельца: нужна фамилия"])
    assert rev.read_bytes() == raw
    assert "не в UTF-8" in str(exc.value) and "отказы остались в журнале" in str(exc.value)


def test_refusal_lines_turn_a_plan_refusal_into_a_section_line_and_skip_blanks():
    src = "«Собеседник 1 → Имя» — голое имя владельца: нужна фамилия"
    assert nf.refusal_lines([src, "", "   "]) == [_ROW]
    assert nf.refusal_lines(None) == []
    assert nf.refusal_lines([]) == []


def test_another_persons_bare_name_and_a_real_namesake_are_applied():
    """Владелец «Имя Фамилия»: голое имя другого участника применяется,
    тёзка с другой фамилией — тоже. Голое имя владельца — отказ."""
    guard = nf.guard_for({"sufler": {"user_name": "Имя Фамилия"}})
    headers = {"Собеседник 1", "Собеседник 2"}
    assert nf.plan([("Собеседник 1", "Другой", "")], headers, guard) == {"Собеседник 1": "Другой"}
    assert nf.plan([("Собеседник 2", "Имя Другаяфамилия", "")], headers, guard) == {
        "Собеседник 2": "Имя Другаяфамилия"}
    dropped: list[str] = []
    assert nf.plan([("Собеседник 1", "Имя", "")], headers, guard, dropped) == {}
    assert dropped == ["«Собеседник 1 → Имя» — голое имя владельца: нужна фамилия"]


def test_one_word_owner_has_no_bare_name():
    """user_name из одного слова: bare пустое. Цель «Имя» — полное имя
    владельца. Метка «Имя» — канал микрофона: guard_for кладёт это слово
    в mic, и причина канала раньше причины полного имени."""
    guard = nf.guard_for({"sufler": {"user_name": "Имя"}})
    assert guard.bare == ""
    assert guard.owner == "Имя"
    dropped: list[str] = []
    assert nf.plan([("Собеседник 1", "Имя", "")], {"Собеседник 1"}, guard, dropped) == {}
    assert dropped == ["«Собеседник 1 → Имя» — целевое имя — полное имя владельца"]
    dropped.clear()
    assert nf.plan([("Имя", "Фамилия", "")], {"Имя"}, guard, dropped) == {}
    assert dropped == ["«Имя → Фамилия» — метка владельца (канал микрофона) не переименовывается"]


def test_section_noise_is_not_a_malformed_line():
    text = ("## Исправления имён\n"
            "проза до пункта\n"
            "- нет\n"
            "- **Сергей** → **Мария** (из обращения)\n"
            "- **Собеседник 1** → **Имя** — основание: голое\n")
    dropped: list[str] = []
    noise: list[str] = []
    fixes = nf.name_fixes(text, dropped=dropped, noise=noise)
    assert noise == ["проза до пункта", "- нет"]
    assert dropped == ["**Сергей** → **Мария** (из обращения)"]
    assert fixes == [("Собеседник 1", "Имя", "голое")]
    assert nf.refusal_lines(dropped) == ["- Сергей → Мария (из обращения)"]


def test_unread_transcript_is_an_event_appended_after_the_model_text(tmp_path):
    """Стенограмма не читается — строка в dropped, чтобы человек видел это.
    Раздел модели не снимается: свой блок дописывается в конец."""
    rev = tmp_path / "ревизия.md"
    names = ("# Ревизия\n## Исправления имён\n"
             "- **Собеседник 1** → **Имя** — основание: звучало\n")
    original = names + "\n## Не применено\n- Собеседник 1 → Имя — старая причина\n"
    rev.write_text(original, encoding="utf-8")
    missing = tmp_path / "нет.md"
    dropped: list[str] = []
    assert nf.planned(rev, missing, {"sufler": {"user_name": "Имя Фамилия"}}, dropped=dropped) == {}
    assert len(dropped) == 1
    # причина — текст ошибки ОС без пути: путь машины в ревизию не пишется
    assert dropped[0] == (f"{missing.name}: стенограмма не прочитана — имена не перештампованы "
                          f"({os.strerror(errno.ENOENT)})")
    row = "- " + dropped[0]
    assert nf.refusal_lines(dropped) == [row]
    assert nf.record_unapplied(rev, dropped) == 1
    text = rev.read_text(encoding="utf-8")
    assert text == nf.render_unapplied(original, [row])
    assert "старая причина" in text and "не прочитана" in text
    assert text.count("## Не применено") == 2
    assert nf.name_fixes(text) == nf.name_fixes(names)

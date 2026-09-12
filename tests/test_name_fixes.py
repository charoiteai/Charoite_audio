"""Имена меток стенограммы после ревизии — строгий раздел и перештамповка (№239)."""
import json
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
    fixes = [("Сергей", "Мария", ""), ("Юля", "Юля", ""), ("Владелец", "Кто-то", ""),
             ("Собеседник 3", "Собеседник 4", ""), ("Иван", "Ольга", ""), ("Сергей", "Анна", ""),
             ("Юля", "[[Люди/Юля]]", ""), ("Собеседник 3", "Пётр", "")]
    mapping = nf.plan(fixes, headers={"Сергей", "Юля", "Собеседник 3", "Владелец"},
                      protected={"Владелец"}, dropped=dropped)
    assert mapping == {"Сергей": "Мария", "Собеседник 3": "Пётр"}
    assert [d.split(" — ")[1] for d in dropped] == [
        "то же имя", "метка владельца (канал микрофона) не переименовывается",
        "имя не годится (заглушка, разметка или длина)", "такой метки в заголовках реплик нет",
        "метка уже исправлена другой строкой", "имя не годится (заглушка, разметка или длина)"]
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
        assert ("из узла Люди с ошибочным именем убери строку" in prompt) is may_edit

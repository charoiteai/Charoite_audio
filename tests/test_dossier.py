"""Досье-слой: кластеризация, инкрементальность, поиск по индексу."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from charoite_graph import dossier  # noqa: E402
from charoite_graph.graph_schema import name_date  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402


@pytest.mark.parametrize("путь,ждём", [
    ("Встречи/2026-03-05/sync.md", "2026-03-05"),
    ("Встречи/2026-03-05_sync.md", "2026-03-05"),
    ("Встречи/2026-02-30_x/2026-03-01_y.md", "2026-03-01"),
    ("Встречи/2025-01-01_x/2026-03-01_y.md", "2026-03-01"),
    ("Встречи/2026-01-01/2026-02-30_встреча.md", "2026-01-01"),
    ("Встречи/2026-02-30_встреча.md", None),
    ("Встречи/12026-03-01_x.md", None),
    ("Встречи/2026-03-011_x.md", None),
    ("Встречи/２０２６-０３-０１_x.md", "2026-03-01"),
    ("Встречи\\2026-03-05\\sync.md", "2026-03-05"),
    ("", None),
])
def test_дата_в_имени_берётся_из_ближайшего_сегмента(путь, ждём):
    """Валидная дата ближайшего к файлу сегмента; невалидный день пропущен."""
    assert name_date(путь) == ждём


def _граф_с_датами(tmp: pathlib.Path) -> pathlib.Path:
    """Ядро, 20 датированных встреч (дата — в каталоге) и участник из другой
    папки. Имена идут против дат: у новейшей встречи имя по алфавиту последнее,
    остальные по алфавиту — от новой к старой. Так база берёт 13 встреч без
    новейшей и подаёт их от новой к старой."""
    g = tmp / "Граф"
    (g / "Ядра").mkdir(parents=True)
    (g / "Документация").mkdir()
    (g / "Ядра" / "Тема.md").write_text("# Тема\nядро темы\n", encoding="utf-8")
    for dd in range(1, 21):
        d = f"2026-03-{dd:02d}"
        name = "m20" if dd == 20 else f"m{20 - dd:02d}"
        folder = g / "Встречи" / d
        folder.mkdir(parents=True)
        (folder / f"{name}.md").write_text(
            f"# {name}\n[[Ядра/Тема]] встреча {d}\n", encoding="utf-8")
    (g / "Документация" / "Итоги.md").write_text(
        "# Итоги\n[[Ядра/Тема]] прочее\n", encoding="utf-8")
    return g


def test_кластер_ставит_новейшую_встречу_после_ядра(tmp_path):
    """В кластере ядро первым, новейшая встреча второй, дальше встречи от
    новой к старой, прочий участник — последним."""
    g = _граф_с_датами(tmp_path)
    files, backlinks = dossier.scan(g, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)["Тема"]

    assert members[0] == "Тема"
    assert members[1] == "m20", "новейшая встреча (2026-03-20) должна идти второй"
    dates = [dossier.meeting_date(files[m], schema=CHAROITE) for m in members[1:21]]
    assert dates == sorted(dates, reverse=True), dates
    assert dates[0] == "2026-03-20" and dates[-1] == "2026-03-01"
    assert members[21] == "Итоги", "прочий участник — последним"


def test_meeting_date_читает_место_и_дату():
    """Дату даёт только папка встреч схемы; архив — тоже встреча, а участник
    другой папки и мета без полей — нет, и это не KeyError."""
    assert dossier.meeting_date(
        {"kind": "Встречи", "rel": "Встречи/2026-03-05/sync.md"}, schema=CHAROITE) == "2026-03-05"
    assert dossier.meeting_date(
        {"kind": "Встречи-архив", "rel": "Встречи-архив/2026-01-01_старое.md"},
        schema=CHAROITE) == "2026-01-01"
    assert dossier.meeting_date(
        {"kind": "Документация", "rel": "Документация/2026-03-05_итоги.md"},
        schema=CHAROITE) is None
    assert dossier.meeting_date({"kind": "Встречи"}, schema=CHAROITE) is None
    assert dossier.meeting_date({}, schema=CHAROITE) is None


def test_встреча_без_даты_идёт_после_датированных(tmp_path):
    """Цифровое имя встречи без даты не должно обгонять датированные: она
    попадает в отдельный список, а не в общую сортировку по имени."""
    g = tmp_path / "Граф"
    (g / "Ядра").mkdir(parents=True)
    (g / "Ядра" / "Тема.md").write_text("# Тема\nядро\n", encoding="utf-8")
    for dd in range(1, 15):
        folder = g / "Встречи" / f"2026-05-{dd:02d}"
        folder.mkdir(parents=True)
        (folder / f"a{dd:02d}.md").write_text("[[Ядра/Тема]]\n", encoding="utf-8")
    (g / "Встречи" / "0_без_даты.md").write_text("[[Ядра/Тема]]\n", encoding="utf-8")

    files, backlinks = dossier.scan(g, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)["Тема"]
    dated = [m for m in members if dossier.meeting_date(files[m], schema=CHAROITE)]
    assert len(dated) == 14
    assert members.index("0_без_даты") > max(members.index(m) for m in dated), \
        "встреча без даты должна идти после всех датированных"


def _блоки(prompt: str) -> list[str]:
    return re.findall(r"^### \[\[(.+?)\]\]", prompt, re.M)


def _эталон_отбора(members: list[str], files: dict[str, dict]) -> list[str]:
    """Первые участники по порядку, прошедшие бюджет, — как в build_prompt."""
    out, total = [], 0
    for m in members[:dossier.MAX_SOURCES]:
        meta = files.get(m)
        if not meta:
            continue
        body = re.sub(r"^---.*?^---", "", meta["text"],
                      flags=re.S | re.M).strip()[:dossier.SRC_CHARS]
        block = f"### [[{m}]] ({meta['kind']})\n{body}\n"
        if total + len(block) > dossier.PROMPT_CHARS:
            break
        out.append(m)
        total += len(block)
    return out


def test_промпт_ставит_ядро_первым_а_встречи_по_возрастанию(tmp_path):
    """Блок ядра — первым, новейшая встреча во входе, встречи — от старой к
    новой. База брала 13 встреч без новейшей и подавала их от новой к старой."""
    g = _граф_с_датами(tmp_path)
    files, backlinks = dossier.scan(g, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)["Тема"]
    prompt = dossier.build_prompt("Тема", members, files, schema=CHAROITE)
    names = _блоки(prompt)

    assert names[0] == "Тема", "блок ядра должен идти первым"
    assert "m20" in names, "новейшая встреча обязана попасть во вход"
    dated = [dossier.meeting_date(files[n], schema=CHAROITE) for n in names[1:]]
    assert all(dated) and dated == sorted(dated), dated


def test_длинные_тексты_режут_бюджет_до_перестановки(tmp_path):
    """Состав входа равен отбору по порядку members: перестановка его не меняет,
    а длинные тексты держат блоков меньше MAX_SOURCES."""
    g = _граф_с_датами(tmp_path)
    files, backlinks = dossier.scan(g, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)["Тема"]
    for meta in files.values():
        meta["text"] = meta["text"] + "x" * (dossier.SRC_CHARS * 2)

    prompt = dossier.build_prompt("Тема", members, files, schema=CHAROITE)
    names = _блоки(prompt)
    assert 0 < len(names) < dossier.MAX_SOURCES
    assert "m20" in names
    assert set(names) == set(_эталон_отбора(members, files))


def test_отбор_блоков_не_зависит_от_перестановки(tmp_path):
    """Сторож отбора: при любом порядке members множество блоков совпадает с
    эталоном отбора. Сам по себе доказательством не считается."""
    g = _граф_с_датами(tmp_path)
    files, backlinks = dossier.scan(g, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)["Тема"]
    for order in (members, list(reversed(members))):
        prompt = dossier.build_prompt("Тема", order, files, schema=CHAROITE)
        names = _блоки(prompt)
        assert len(names) == len(set(names))
        assert set(names) == set(_эталон_отбора(order, files))


def _граф(tmp: pathlib.Path) -> pathlib.Path:
    """Мини-граф: одно ядро, две встречи и человек, все связаны ссылками."""
    g = tmp / "Граф"
    (g / "Ядра").mkdir(parents=True)
    (g / "Встречи").mkdir()
    (g / "Люди").mkdir()

    (g / "Ядра" / "Настройка доступа.md").write_text(
        "---\ntype: ядро\n---\n# Настройка доступа\n"
        "## Статус\nТокен получен _(обновлено 2026-07-24)_\n"
        "## Хроника\n- [[Встречи/2026-07-22_1000]] — первая попытка\n",
        encoding="utf-8")
    (g / "Встречи" / "2026-07-22_1000.md").write_text(
        "# Встреча\nОбсуждали [[Ядра/Настройка доступа]] и сервисный токен.\n"
        "Участник [[Люди/Пётр]].\n", encoding="utf-8")
    (g / "Встречи" / "2026-07-24_1100.md").write_text(
        "# Встреча\nПродолжение [[Ядра/Настройка доступа]]: получили 403.\n",
        encoding="utf-8")
    (g / "Люди" / "Пётр.md").write_text(
        "# Пётр\nВедёт [[Ядра/Настройка доступа]].\n", encoding="utf-8")
    return g


def test_кластер_собирается_вокруг_ядра(tmp_path):
    g = _граф(tmp_path)
    files, backlinks = dossier.scan(g, schema=CHAROITE)
    cl = dossier.clusters(files, backlinks, min_size=3, schema=CHAROITE)

    assert "Настройка доступа" in cl
    члены = set(cl["Настройка доступа"])
    assert "2026-07-22_1000" in члены and "2026-07-24_1100" in члены
    assert "Пётр" in члены


def test_досье_и_служебное_в_кластеры_не_попадают(tmp_path):
    g = _граф(tmp_path)
    (g / CHAROITE.dossier_dir).mkdir()
    (g / CHAROITE.dossier_dir / "Настройка доступа.md").write_text(
        "# Досье\n[[Ядра/Настройка доступа]]\n", encoding="utf-8")
    (g / "Служебное_ночная_ревизия_2026-07-29.md").write_text(
        "# Ревизия\n[[Ядра/Настройка доступа]]\n", encoding="utf-8")

    files, _ = dossier.scan(g, schema=CHAROITE)
    assert all(not v["rel"].startswith(CHAROITE.dossier_dir) for v in files.values())
    assert not any(k.startswith("Служебное_") for k in files)


def test_отпечаток_меняется_только_при_правке_источника(tmp_path):
    g = _граф(tmp_path)
    files, backlinks = dossier.scan(g, schema=CHAROITE)
    члены = dossier.clusters(files, backlinks, schema=CHAROITE)["Настройка доступа"]
    было = dossier.fingerprint(члены, files)

    # перечитали граф, ничего не трогая
    files2, _ = dossier.scan(g, schema=CHAROITE)
    assert dossier.fingerprint(члены, files2) == было

    # правка одного источника
    p = g / "Встречи" / "2026-07-24_1100.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nДописали строку.\n", encoding="utf-8")
    import os
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 120))
    files3, _ = dossier.scan(g, schema=CHAROITE)
    assert dossier.fingerprint(члены, files3) != было


def test_ручные_правки_переживают_пересборку(tmp_path):
    старое = ("---\ntype: досье\n---\n# Досье: Тема\n"
              "## Сейчас\nстарый текст\n\n## Правки автора\n\nВажное замечание руками\n")
    assert dossier.preserve_manual(старое) == "Важное замечание руками"
    # пустой раздел не считается правкой
    assert dossier.preserve_manual("## Правки автора\n\n—\n") is None
    assert dossier.preserve_manual("нет такого раздела") is None


def test_брак_модели_не_проходит_валидацию():
    диалог = "Принято. Готов работать с этими данными. Что вы хотите дальше?"
    assert not dossier.looks_valid(диалог)

    норм = ("## Сейчас\nсостояние\n## Как пришли\nхроника\n"
            "## Решено\nрешения\n## Открыто\nвопросы\n## Кто в теме\nлюди\n")
    assert dossier.looks_valid(норм)


def test_предисловие_модели_отрезается():
    ответ = "Конечно! Вот сводка:\n\n## Сейчас\nсостояние\n## Как пришли\nх\n"
    assert dossier.trim_to_format(ответ).startswith("## Сейчас")


@pytest.mark.parametrize("запрос,ждём", [
    ("как настроить доступ", True),
    ("что там с сервисным токеном", True),
    ("qwen", True),                       # префиксное совпадение с qwen3-32b
    ("отпуск и график смен", False),      # мимо темы
])
def test_поиск_по_индексу(tmp_path, запрос, ждём):
    folder = tmp_path / CHAROITE.dossier_dir
    dossier.write_index(folder, [{
        "тема": "Настройка доступа",
        "файл": f"{CHAROITE.dossier_dir}/Настройка доступа.md",
        "источников": 4, "собрано": "2026-07-29", "отпечаток": "abc",
        "ключи": ["доступ", "токен", "qwen3-32b", "403", "настроик"],
    }])
    hits = dossier.lookup(folder, запрос)
    assert bool(hits) is ждём


def test_коды_ошибок_остаются_ключами():
    keys = dossier.keywords("получили 403 при запросе, лимит 999 токенов")
    assert "403" in keys and "999" in keys


def test_имена_стенограмм_в_ключи_не_лезут():
    keys = dossier.keywords(
        "2026-07-24_0911_настройка_postman_ревизия_claude настройка доступа")
    assert not any(k.count("_") >= 2 for k in keys)
    assert any(k.startswith("настро") for k in keys)


def test_индекс_читается_обратно(tmp_path):
    folder = tmp_path / CHAROITE.dossier_dir
    записи = [{"тема": "А", "файл": "Досье/А.md", "источников": 3,
               "собрано": "2026-07-29", "отпечаток": "x", "ключи": ["а"]},
              {"тема": "Б", "файл": "Досье/Б.md", "источников": 5,
               "собрано": "2026-07-29", "отпечаток": "y", "ключи": ["б"]}]
    dossier.write_index(folder, записи)

    assert (folder / dossier.INDEX_MD).exists()
    прочитано = dossier.load_index(folder)
    assert {e["тема"] for e in прочитано} == {"А", "Б"}
    # в человекочитаемом индексе есть ссылки на оба досье
    md = (folder / dossier.INDEX_MD).read_text(encoding="utf-8")
    assert "А" in md and "Б" in md


def test_битый_индекс_не_роняет_поиск(tmp_path):
    folder = tmp_path / CHAROITE.dossier_dir
    folder.mkdir(parents=True)
    (folder / dossier.INDEX_JSON).write_text("{это не json", encoding="utf-8")
    assert dossier.load_index(folder) == []
    assert dossier.lookup(folder, "любой запрос") == []


def test_заглушка_tier3_не_становится_темой(tmp_path):
    """После слияния дубль остаётся файлом-редиректом с входящими ссылками;
    раньше кластер вокруг него был «жив», и ночь собирала досье по мёртвой
    теме рядом с каноном (аудит GLM 17.08)."""
    g = _граф(tmp_path)
    (g / "Ядра" / "Доступ и токены.md").write_text(
        "---\ntype: ядро\ntags: [дубль, redirect, tier3-nli]\n---\n"
        "# Доступ и токены → [[Ядра/Настройка доступа]]\n\n"
        "⚠️ **Дубль. Смерджен Tier3-NLI.** Хроника перенесена в "
        "[[Ядра/Настройка доступа|Настройка доступа]].\n", encoding="utf-8")
    (g / "Встречи" / "2026-07-25_0900.md").write_text(
        "# Встреча\nСнова про [[Ядра/Доступ и токены]].\n", encoding="utf-8")

    files, backlinks = dossier.scan(g, schema=CHAROITE)
    assert "Доступ и токены" not in files
    cl = dossier.clusters(files, backlinks, min_size=2, schema=CHAROITE)
    assert "Доступ и токены" not in cl


def test_индекс_не_теряет_досье_сверх_лимита_и_при_браке(tmp_path, monkeypatch):
    """Индекс — карта всех досье на диске: темы сверх лимита ночи и темы с
    отказом раньше выпадали из _index/_ИНДЕКС, а --full оставлял 12 записей;
    брак формата дважды не считался отказом — ночь «ok» (аудит 17.08)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "nightly_dossier", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "nightly_dossier.py")
    nd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nd)

    g = _граф(tmp_path)
    # вторая тема-ядро с двумя источниками
    (g / "Ядра" / "Отчётность.md").write_text(
        "---\ntype: ядро\n---\n# Отчётность\n## Статус\nв работе\n## Хроника\n- [[Встречи/2026-07-22_1000]]\n",
        encoding="utf-8")
    (g / "Встречи" / "2026-07-26_1200.md").write_text(
        "# Встреча\nПро [[Ядра/Отчётность]] и [[Люди/Пётр]].\n", encoding="utf-8")
    (g / "Люди" / "Пётр.md").write_text(
        "# Пётр\nВедёт [[Ядра/Настройка доступа]] и [[Ядра/Отчётность]].\n", encoding="utf-8")
    folder = g / CHAROITE.dossier_dir
    folder.mkdir()
    # у обеих тем уже есть досье на диске (с чужим отпечатком → «изменилось»)
    for theme in ("Настройка доступа", "Отчётность"):
        (folder / f"{theme}.md").write_text(
            f"---\nтема: {theme}\nотпечаток: старый\nсобрано: 2026-07-20\n---\n# {theme}\n"
            "## Сейчас\nбыло\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—\n"
            "## Источники\n- x\n## Правки автора\n\n—\n", encoding="utf-8")

    good = ("## Сейчас\nвсё в порядке\n## Как пришли\nт\n## Решено\nт\n"
            "## Открыто\nт\n## Кто в теме\nт")
    calls = {"n": 0}

    def fake_generate(theme, *a, **k):
        calls["n"] += 1
        return good if theme == "Настройка доступа" else "Принято, что дальше?"

    monkeypatch.setattr(nd, "generate", fake_generate)
    # limit=1: первая тема пересобирается, вторая упирается в лимит ночи
    r = nd.run(g, {"sufler": {}}, full=False, dry=False, limit=1)
    idx = dossier.load_index(folder)
    assert {e["тема"] for e in idx} == {"Настройка доступа", "Отчётность"}, \
        "тема сверх лимита выпала из индекса"
    assert r["собрано"] == 1 and r["отказы"] == 0

    # брак формата дважды на теме — отказ, досье остаётся в индексе
    (folder / "Настройка доступа.md").write_text(
        (folder / "Настройка доступа.md").read_text(encoding="utf-8").replace("отпечаток:", "отпечаток: старый2 #"),
        encoding="utf-8")
    monkeypatch.setattr(nd, "generate", lambda *a, **k: "Принято, что дальше?")
    r = nd.run(g, {"sufler": {}}, full=True, dry=False, limit=5)
    idx = dossier.load_index(folder)
    assert {e["тема"] for e in idx} == {"Настройка доступа", "Отчётность"}, \
        "тема с браком выпала из индекса"
    assert r["отказы"] == 2, "брак формата дважды — это отказ, а не тишина"

    # исключение из модели — ОДИН отказ на тему, а не два (ревью 17.08)
    def boom(*a, **k):
        raise RuntimeError("сервер лёг")

    monkeypatch.setattr(nd, "generate", boom)
    r = nd.run(g, {"sufler": {}}, full=True, dry=False, limit=5)
    assert r["отказы"] == 2, "исключение считается один раз на тему"
    assert {e["тема"] for e in dossier.load_index(folder)} == {"Настройка доступа", "Отчётность"}


def test_закрытое_окно_не_зовёт_модель_и_не_теряет_темы_индекса(tmp_path, monkeypatch):
    """Окно закрыто (ночь вышла): генерация не зовётся, а индекс не худеет —
    оставшиеся темы остаются записями с диска, как темы сверх лимита (круг 7
    по №338: сборка досье звала настоящее окно, и без переменной ночи оно
    всегда открыто — закрытую ветку не покрывал ни один тест)."""
    nd = _скрипт_пересборки()
    g = _граф(tmp_path)
    # вторая тема-ядро с двумя источниками — подготовка как в тесте про индекс
    (g / "Ядра" / "Отчётность.md").write_text(
        "---\ntype: ядро\n---\n# Отчётность\n## Статус\nв работе\n## Хроника\n- [[Встречи/2026-07-22_1000]]\n",
        encoding="utf-8")
    (g / "Встречи" / "2026-07-26_1200.md").write_text(
        "# Встреча\nПро [[Ядра/Отчётность]] и [[Люди/Пётр]].\n", encoding="utf-8")
    (g / "Люди" / "Пётр.md").write_text(
        "# Пётр\nВедёт [[Ядра/Настройка доступа]] и [[Ядра/Отчётность]].\n", encoding="utf-8")
    folder = g / CHAROITE.dossier_dir
    folder.mkdir()
    # у обеих тем уже есть досье на диске (с чужим отпечатком → «изменилось»)
    for theme in ("Настройка доступа", "Отчётность"):
        (folder / f"{theme}.md").write_text(
            f"---\nтема: {theme}\nотпечаток: старый\nсобрано: 2026-07-20\n---\n# {theme}\n"
            "## Сейчас\nбыло\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—\n"
            "## Источники\n- x\n## Правки автора\n\n—\n", encoding="utf-8")
    before = {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md"))
              if p.name != dossier.INDEX_MD}   # индекс прогон переписывает и при
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: False)   # закрытом окне — легитимно

    def модель(*a, **k):
        pytest.fail("генерация позвана при закрытом ночном окне")

    monkeypatch.setattr(nd, "generate", модель)
    r = nd.run(g, {"sufler": {}}, full=False, dry=False, limit=5)
    assert r["собрано"] == 0 and r["не_успели"] == 2, r
    assert {e["тема"] for e in dossier.load_index(folder)} == {"Настройка доступа", "Отчётность"}, \
        "тема, не успевшая за ночь, выпала из индекса"
    after = {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md"))
             if p.name != dossier.INDEX_MD}
    assert after == before, "файлы досье изменились при закрытом окне"


def test_keys_see_cjk_words():
    """Китайская встреча давала пустые ключи — досье не искалось (хвост 20.08, GLM)."""
    from charoite_graph import dossier
    keys = dossier.keywords("会议讨论了数据平台的迁移计划 和 бюджет проекта")
    joined = " ".join(keys) if not isinstance(keys, str) else keys
    assert any("\u4e00" <= ch <= "\u9fff" for ch in joined), keys
    assert "бюджет" in joined or "бюджет" in str(keys)
    ru = dossier.keywords("мы не знали, на что поступить, и не вернулись, бюджет проекта не согласован")
    assert not ({"не", "на", "и"} & set(ru)), ru



def test_long_cjk_run_is_split_into_bigrams(tmp_path):
    """Китайское предложение без пробелов — один токен длиннее 24 знаков —
    выпадал целиком, ключей у встречи не оставалось (luna по #455)."""
    from charoite_graph import dossier
    keys = dossier.keywords("这是一个超过二十四个汉字且中间没有空格的中文句子用于检索测试")
    assert keys and all(len(k) == 2 for k in keys), keys
    folder = tmp_path / "Досье"
    folder.mkdir()
    (folder / dossier.INDEX_JSON).write_text(json.dumps({"досье": [
        {"тема": "数据平台迁移", "ключи": dossier.keywords("会议讨论了数据平台的迁移计划和时间表")},
        {"тема": "Отчётность", "ключи": dossier.keywords("отчётность бюджет квартал")},
    ]}, ensure_ascii=False), encoding="utf-8")
    hits = dossier.lookup(folder, "数据平台迁移计划")
    assert hits and hits[0]["тема"] == "数据平台迁移", hits


def test_bigram_query_matches_a_whole_cjk_key_of_an_old_index(tmp_path):
    """Индекс прежней версии хранит цельную CJK-последовательность; биграммы
    запроса обязаны находить её без пересборки досье (luna r2 по #455)."""
    from charoite_graph import dossier
    folder = tmp_path / "Досье"
    folder.mkdir()
    (folder / dossier.INDEX_JSON).write_text(json.dumps({"досье": [
        {"тема": "数据平台迁移", "ключи": ["数据平台迁移", "会议"]},
        {"тема": "Отчётность", "ключи": ["отчетност", "бюджет"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    hits = dossier.lookup(folder, "数据平台迁移计划")
    assert hits and hits[0]["тема"] == "数据平台迁移", hits
    hits = dossier.lookup(folder, "бюджет квартала")
    assert hits and hits[0]["тема"] == "Отчётность", hits
    assert all(len(k) == 2 for k in dossier.load_index(folder)[0]["ключи"]), "старый ключ порезан на биграммы при чтении"


def _скрипт_пересборки():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "nightly_dossier", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "nightly_dossier.py")
    nd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nd)
    return nd


def test_backup_копирует_досье_и_режет_старые(tmp_path):
    folder = tmp_path / "Досье"
    folder.mkdir()
    path = folder / "Тема.md"
    path.write_text("старое тело", encoding="utf-8")
    assert dossier.backup(folder, "2026-09-13_010000", folder / "нет.md") is None
    for stamp in ("2026-09-13_010000", "2026-09-13_020000", "2026-09-13_030000"):
        dst = dossier.backup(folder, stamp, path, keep=2)
        assert dst == folder / ".backup" / stamp / "Тема.md"
        assert dst.read_text(encoding="utf-8") == "старое тело"
    left = sorted(p.name for p in (folder / ".backup").iterdir())
    assert left == ["2026-09-13_020000", "2026-09-13_030000"], left
    # случайный файл в .backup/ (упавший tmp) тоже уходит под нож (GLM M4 по #561)
    (folder / ".backup" / "0000_мусор.tmp").write_text("x", encoding="utf-8")
    dossier.backup(folder, "2026-09-13_040000", path, keep=2)
    assert sorted(p.name for p in (folder / ".backup").iterdir()) == ["2026-09-13_030000", "2026-09-13_040000"]


def test_пересборка_делает_копию_досье_до_перезаписи(tmp_path, monkeypatch):
    """Пересборка сохраняла только «Правки автора», а тело с редактурой облачной
    ревизии стирала без единой копии (аудит 13.09, GLM C1)."""
    nd = _скрипт_пересборки()
    g = _граф(tmp_path)
    folder = g / CHAROITE.dossier_dir
    folder.mkdir()
    (folder / "Настройка доступа.md").write_text(
        "---\nтема: Настройка доступа\nотпечаток: старый\nсобрано: 2026-07-20\n---\n# Настройка доступа\n"
        "## Сейчас\nправка ревизии, которую нельзя терять\n## Как пришли\n—\n## Решено\n—\n"
        "## Открыто\n—\n## Кто в теме\n—\n## Источники\n- x\n## Правки автора\n\n—\n",
        encoding="utf-8")
    good = ("## Сейчас\nвсё в порядке\n## Как пришли\nт\n## Решено\nт\n## Открыто\nт\n## Кто в теме\nт")
    monkeypatch.setattr(nd, "generate", lambda *a, **k: good)
    r = nd.run(g, {"sufler": {}}, full=False, dry=False, limit=5)
    assert r["собрано"] == 1
    copies = list((folder / ".backup").glob("*/Настройка доступа.md"))
    assert len(copies) == 1, "копии досье до перезаписи нет"
    assert "правка ревизии, которую нельзя терять" in copies[0].read_text(encoding="utf-8")
    assert "всё в порядке" in (folder / "Настройка доступа.md").read_text(encoding="utf-8")


def test_пересборка_без_копии_не_перезаписывает_и_не_падает(tmp_path, monkeypatch):
    """OSError копии (права на .backup, iCloud) убивал весь прогон — остальные темы
    и индекс терялись (круг-1 по #561: DS I2 / GLM I1)."""
    nd = _скрипт_пересборки()
    g = _граф(tmp_path)
    folder = g / CHAROITE.dossier_dir
    folder.mkdir()
    old = ("---\nтема: Настройка доступа\nотпечаток: старый\nсобрано: 2026-07-20\n---\n# Настройка доступа\n"
           "## Сейчас\nбыло\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—\n"
           "## Источники\n- x\n## Правки автора\n\n—\n")
    (folder / "Настройка доступа.md").write_text(old, encoding="utf-8")
    good = ("## Сейчас\nвсё в порядке\n## Как пришли\nт\n## Решено\nт\n## Открыто\nт\n## Кто в теме\nт")
    monkeypatch.setattr(nd, "generate", lambda *a, **k: good)

    def no_copy(*a, **k):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(nd.dossier, "backup", no_copy)
    r = nd.run(g, {"sufler": {}}, full=False, dry=False, limit=5)
    assert r["собрано"] == 0 and r["отказы"] == 1
    assert (folder / "Настройка доступа.md").read_text(encoding="utf-8") == old, "переписано без копии"
    assert {e["тема"] for e in dossier.load_index(folder)} == {"Настройка доступа"}, "тема выпала из индекса"


def _nightly_dossier():
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "nightly_dossier.py"
    spec = importlib.util.spec_from_file_location("nightly_dossier", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ночное_досье_find_ищет_в_папке_досье_схемы(tmp_path, monkeypatch, capsys):
    """`--find` отвечает по индексу в папке досье схемы Чароита: тема находится по ключу,
    печатается с файлом; запрос мимо — «досье не найдено»."""
    graph = tmp_path / "г"
    dossier.write_index(graph / CHAROITE.dossier_dir, [{
        "тема": "Платежи", "источников": 5, "собрано": "2026-08-03", "ключи": ["платежи", "шлюз"],
        "файл": f"{CHAROITE.dossier_dir}/Платежи.md"}])
    nd = _nightly_dossier()
    monkeypatch.setattr(sys, "argv", ["nightly_dossier.py", "--graph", str(graph), "--find", "платежи"])
    assert nd.main() == 0
    out = capsys.readouterr().out
    assert "Платежи" in out and f"{CHAROITE.dossier_dir}/Платежи.md" in out
    monkeypatch.setattr(sys, "argv", ["nightly_dossier.py", "--graph", str(graph), "--find", "погода"])
    assert nd.main() == 0
    assert "досье не найдено" in capsys.readouterr().out

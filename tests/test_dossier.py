"""Досье-слой: кластеризация, инкрементальность, поиск по индексу."""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import dossier  # noqa: E402


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
    files, backlinks = dossier.scan(g)
    cl = dossier.clusters(files, backlinks, min_size=3)

    assert "Настройка доступа" in cl
    члены = set(cl["Настройка доступа"])
    assert "2026-07-22_1000" in члены and "2026-07-24_1100" in члены
    assert "Пётр" in члены


def test_досье_и_служебное_в_кластеры_не_попадают(tmp_path):
    g = _граф(tmp_path)
    (g / dossier.DOSSIER_DIR).mkdir()
    (g / dossier.DOSSIER_DIR / "Настройка доступа.md").write_text(
        "# Досье\n[[Ядра/Настройка доступа]]\n", encoding="utf-8")
    (g / "Служебное_ночная_ревизия_2026-07-29.md").write_text(
        "# Ревизия\n[[Ядра/Настройка доступа]]\n", encoding="utf-8")

    files, _ = dossier.scan(g)
    assert all(not v["rel"].startswith(dossier.DOSSIER_DIR) for v in files.values())
    assert not any(k.startswith("Служебное_") for k in files)


def test_отпечаток_меняется_только_при_правке_источника(tmp_path):
    g = _граф(tmp_path)
    files, backlinks = dossier.scan(g)
    члены = dossier.clusters(files, backlinks)["Настройка доступа"]
    было = dossier.fingerprint(члены, files)

    # перечитали граф, ничего не трогая
    files2, _ = dossier.scan(g)
    assert dossier.fingerprint(члены, files2) == было

    # правка одного источника
    p = g / "Встречи" / "2026-07-24_1100.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nДописали строку.\n", encoding="utf-8")
    import os
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 120))
    files3, _ = dossier.scan(g)
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
    folder = tmp_path / dossier.DOSSIER_DIR
    dossier.write_index(folder, [{
        "тема": "Настройка доступа",
        "файл": f"{dossier.DOSSIER_DIR}/Настройка доступа.md",
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
    folder = tmp_path / dossier.DOSSIER_DIR
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
    folder = tmp_path / dossier.DOSSIER_DIR
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

    files, backlinks = dossier.scan(g)
    assert "Доступ и токены" not in files
    cl = dossier.clusters(files, backlinks, min_size=2)
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
    folder = g / dossier.DOSSIER_DIR
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


def test_keys_see_cjk_words():
    """Китайская встреча давала пустые ключи — досье не искалось (хвост 20.08, GLM)."""
    import dossier
    keys = dossier.keywords("会议讨论了数据平台的迁移计划 和 бюджет проекта")
    joined = " ".join(keys) if not isinstance(keys, str) else keys
    assert any("\u4e00" <= ch <= "\u9fff" for ch in joined), keys
    assert "бюджет" in joined or "бюджет" in str(keys)
    ru = dossier.keywords("мы не знали, на что поступить, и не вернулись, бюджет проекта не согласован")
    assert not ({"не", "на", "и"} & set(ru)), ru



def test_long_cjk_run_is_split_into_bigrams(tmp_path):
    """Китайское предложение без пробелов — один токен длиннее 24 знаков —
    выпадал целиком, ключей у встречи не оставалось (luna по #455)."""
    import dossier
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

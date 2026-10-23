"""Сверка разговора с узлами графа (ревью 15.08): лукап, дайджест, кэш.

Анти-шум автоматического контура проверяется отдельно от чувствительного
ручного: авто-⏮ вставляет строки в нить без спроса, и ложный узел там
дороже пропущенного. Плюс golden-векторы стеммера — их же гоняет
Swift-тест (app/Tests/StemGoldenTests.swift): две реализации не должны
разъезжаться молча.
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from charoite_graph.graph_nodes import NodeIndex, stem  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402

GOLDEN = json.loads(
    (REPO / "tests" / "stem_golden.json").read_text(encoding="utf-8"))


def test_stem_golden_vectors():
    for word, expected in GOLDEN.items():
        assert stem(word) == expected, f"{word!r}: {stem(word)!r} != {expected!r}"


def _graph(tmp_path: pathlib.Path) -> pathlib.Path:
    people = tmp_path / "Люди"
    systems = tmp_path / "Системы"
    cores = tmp_path / "Ядра"
    for d in (people, systems, cores):
        d.mkdir()
    (people / "Иван Мироненко.md").write_text(
        "# Иван Мироненко\n\n## Встречи\n"
        "- [[Встречи/2026-08-01_1000]] — взял интеграцию на себя\n"
        "- [[Встречи/2026-07-30_1400]] — обещал согласовать доступ\n"
        "- [[Встречи/2025-11-11_1200]] — старый вклад\n",
        encoding="utf-8")
    (people / "Иванов.md").write_text("# Иванов\n", encoding="utf-8")
    (systems / "Платёжный шлюз.md").write_text(
        "# Платёжный шлюз\nСтатус: пилот до сентября\n\n## Встречи\n"
        "- [[Встречи/2026-08-02_1500]]\n"
        "- [[Встречи/2026-07-20_1100]] — решили выносить из монолита\n",
        encoding="utf-8")
    (cores / "_ЯДРА.md").write_text("агрегат", encoding="utf-8")
    (cores / "Ретеншн.md").write_text(
        "# Ретеншн\n\n## Хроника\n- [[Встречи/2026-08-03_0900]] — партиции 14 дней\n",
        encoding="utf-8")
    return tmp_path


def test_multiword_name_matches_in_window(tmp_path):
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    hits = idx.lookup("созвонимся с Иваном Мироненко по интеграции")
    assert [n.name for n in hits] == ["Иван Мироненко"]


def test_ivan_alone_does_not_match_ivanov(tmp_path):
    """Стемы, не подстроки: «Иван» не должен цеплять «Иванов»."""
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    hits = idx.lookup("Иван обещал перезвонить", strict=False)
    assert all(n.name != "Иванов" for n in hits)


def test_single_word_strict_needs_two_lines(tmp_path):
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    assert idx.lookup("обсуждали ретеншн партиций") == []
    hits = idx.lookup("обсуждали ретеншн партиций\nретеншн решили не менять")
    assert [n.name for n in hits] == ["Ретеншн"]


def test_single_word_loose_matches_once(tmp_path):
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    hits = idx.lookup("что там с ретеншном?", strict=False)
    assert [n.name for n in hits] == ["Ретеншн"]


def test_known_speaker_person_matches_once(tmp_path):
    """Имя опознанного спикера — достаточное основание и в строгом режиме."""
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    assert idx.lookup("Мироненко за интеграцию") == []
    hits = idx.lookup("Мироненко за интеграцию",
                      known_names={"Иван Мироненко"})
    assert [n.name for n in hits] == ["Иван Мироненко"]


def test_yo_normalisation_in_name(tmp_path):
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    hits = idx.lookup("перенос платежного шлюза\nшлюз платежный не готов")
    assert [n.name for n in hits] == ["Платёжный шлюз"]


def test_digest_takes_head_of_history_not_tail(tmp_path):
    """Конвейер пишет новые записи СВЕРХУ секции — дайджест берёт начало."""
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    node = idx.lookup("Иван Мироненко здесь")[0]
    lines = idx.digest(node)
    assert lines[0].startswith("Иван Мироненко · 01.08:")
    assert all("старый вклад" not in ln for ln in lines) or \
        "11.11.25" in " ".join(ln for ln in lines if "старый вклад" in ln)


def test_digest_skips_link_only_lines_and_keeps_status(tmp_path):
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    node = idx.lookup("платёжный шлюз", strict=False)[0]
    lines = idx.digest(node)
    assert any("пилот до сентября" in ln for ln in lines)
    assert any("20.07: решили выносить" in ln for ln in lines)
    assert all("02.08" not in ln for ln in lines)   # голая ссылка без вклада


def test_old_year_is_visible(tmp_path):
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    node = idx.lookup("Иван Мироненко здесь")[0]
    joined = " ".join(idx.digest(node))
    assert "11.11.25" in joined or "старый вклад" not in joined


def test_service_files_are_not_nodes(tmp_path):
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    assert idx.lookup("ядра агрегат\nядра агрегат") == []


def test_refresh_picks_up_appended_line(tmp_path):
    """Кэш по mtime+size файла: дописанная договорённость видна после refresh."""
    g = _graph(tmp_path)
    idx = NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    p = g / "Ядра" / "Ретеншн.md"
    text = p.read_text(encoding="utf-8").replace(
        "## Хроника\n", "## Хроника\n- [[Встречи/2026-08-15_1200]] — сдвинули на 21 день\n")
    p.write_text(text, encoding="utf-8")
    idx.refresh()
    node = idx.lookup("ретеншн решили\nретеншн опять", limit=1)[0]
    assert any("21 день" in ln for ln in idx.digest(node))


def test_ambiguous_name_is_silent_in_strict(tmp_path):
    g = _graph(tmp_path)
    (g / "Системы" / "Шлюз.md").write_text("# Шлюз\n", encoding="utf-8")
    (g / "Команды").mkdir()
    (g / "Команды" / "Шлюз.md").write_text("# Шлюз\n", encoding="utf-8")
    idx = NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    assert idx.lookup("шлюз горит\nшлюз почини") == []
    loose = idx.lookup("что по шлюзу?", strict=False)
    assert len(loose) == 2   # явному запросу показываем обоих кандидатов


def test_bare_number_does_not_raise_a_node_in_strict(tmp_path):
    """№776: чисто число в речи — количество, год, номер, время; «1» и «8»
    поднимали «Продукт 1.8», метка «[10:41]» — «Систему 41». В strict число
    узел не поднимает; явный запрос (strict=False) — по-прежнему находит."""
    g = _graph(tmp_path)
    (g / "Системы" / "ИС 2049.md").write_text(
        "# ИС 2049\n\n## Встречи\n- [[Встречи/2026-08-05_1000]] — согласовали шину\n",
        encoding="utf-8")
    (g / "Системы" / "Продукт 1.8.md").write_text("# Продукт 1.8\n", encoding="utf-8")
    idx = NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    assert idx.lookup("подключаемся к 2049 после пилота") == []
    assert idx.lookup("шаг 1 из 8\nещё 1 и 8") == []
    assert [n.name for n in idx.lookup("подключаемся к 2049", strict=False)] == ["ИС 2049"]
    assert [n.name for n in idx.lookup("ИС 2049 подключаем")] == ["ИС 2049"], "окно держит"


def test_letter_digit_code_with_one_owner_matches_once_in_strict(tmp_path):
    g = _graph(tmp_path)
    (g / "Системы" / "Хранилище S3.md").write_text(
        "# Хранилище S3\n\n## Встречи\n- [[Встречи/2026-08-05_1000]] — выгрузка холодных данных\n",
        encoding="utf-8")
    idx = NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    assert [n.name for n in idx.lookup("опять бомбят s3 ночью")] == ["Хранилище S3"]


def test_shared_code_needs_the_rest_of_the_name_in_strict(tmp_path):
    """Код двух узлов — не уникален: одиночный «s3» молчит, окно имени — нет.
    Владелец — узел: alias с тем же кодом у того же узла счёт не удваивает."""
    g = _graph(tmp_path)
    (g / "Системы" / "Хранилище S3.md").write_text(
        "---\naliases:\n  - S3 бакет\n---\n# Хранилище S3\n", encoding="utf-8")
    (g / "Системы" / "Шлюз S3.md").write_text("# Шлюз S3\n", encoding="utf-8")
    idx = NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    assert idx.lookup("опять бомбят s3 ночью") == []
    assert [n.name for n in idx.lookup("шлюз s3 лёг")] == ["Шлюз S3"]
    (g / "Системы" / "Шлюз S3.md").unlink()
    idx.refresh()
    assert [n.name for n in idx.lookup("опять бомбят s3 ночью")] == ["Хранилище S3"], \
        "карта владельцев пересчитана вместе со снимком узлов"


def test_one_word_code_name_needs_a_single_owner_in_strict(tmp_path):
    """Однословное имя-код («S3») в strict держится одним упоминанием, только пока
    код у одного узла; второй владелец кода — снова нужны две строки."""
    g = _graph(tmp_path)
    (g / "Системы" / "S3.md").write_text("# S3\n", encoding="utf-8")
    idx = NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    assert [n.name for n in idx.lookup("опять бомбят s3 ночью")] == ["S3"]
    (g / "Системы" / "Хранилище S3.md").write_text("# Хранилище S3\n", encoding="utf-8")
    idx.refresh()
    assert idx.lookup("опять бомбят s3 ночью") == []


def test_participant_label_is_not_a_mention(tmp_path):
    """Участие — не упоминание (№776): узел участника не всплывает по одной
    подписи говорящего, которой в речи нет; названный в речи — всплывает."""
    tr_speech = "давайте по интеграции\nсрок до пятницы"
    idx = NodeIndex(_graph(tmp_path), schema=CHAROITE)
    idx.refresh()
    known = {"Иван Мироненко"}
    assert idx.lookup(tr_speech, known_names=known) == []
    assert [n.name for n in idx.lookup("Мироненко, что по интеграции?", known_names=known)] \
        == ["Иван Мироненко"]


def test_name_window_does_not_cross_replies_in_strict(tmp_path):
    """Окно многословного имени в strict — внутри реплики (№776 out1): хвост одной
    реплики и начало следующей имени не собирают; ручной запрос (strict=False)
    по-прежнему ищет по всему тексту."""
    g = _graph(tmp_path)
    (g / "Ядра" / "Проблема источников.md").write_text("# Проблема источников\n", encoding="utf-8")
    idx = NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    split = "кто платит за развитие источников\nпроблемы я что-то потерял"
    assert idx.lookup(split) == []
    assert [n.name for n in idx.lookup(split, strict=False)] == ["Проблема источников"]
    assert [n.name for n in idx.lookup("опять проблема источников всплыла")] == ["Проблема источников"]


def test_aliases_from_the_header_are_read_by_the_shared_parser(tmp_path):
    """Псевдонимы читаются одним парсером с конвейером (frontmatter.py, #451):
    блок «- имя», кавычки с запятой, битая соседняя строка — всё видно."""
    root = _graph(tmp_path)
    (root / "Системы" / "Реестр.md").write_text(
        "---\ntype: система\nописание: План: перенос\naliases:\n  - Реестр поручений\n  - \"РП, реестр\"\n---\n"
        "# Реестр\n\n## Встречи\n- [[Встречи/2026-08-02_1500]]\n", encoding="utf-8")
    idx = NodeIndex(root, schema=CHAROITE)
    idx.refresh()
    hits = idx.lookup("обсудили реестр поручений на неделю")
    assert [n.name for n in hits] == ["Реестр"]


def test_redirect_stub_is_not_a_node(tmp_path):
    """Заглушка после слияния (`# X → [[Ядра/Y]]`, «Дубль слит») не попадает в
    индекс узлов: иначе дайджест и подсказки цитировали мёртвый файл (хвост 20.08)."""
    root = _graph(tmp_path)
    (root / "Ядра" / "Старое ядро.md").write_text(
        "---\ntype: ядро\n---\n# Старое ядро → [[Ядра/Ретеншн]]\n\n⚠️ **Дубль. Смерджен Tier3-NLI.**\n", encoding="utf-8")
    (root / "Ядра" / "Ретеншн.md").write_text(
        "---\ntype: ядро\n---\n# Ретеншн\n\nУдержание клиентов после первого месяца.\n", encoding="utf-8")
    idx = NodeIndex(root, schema=CHAROITE)
    idx.refresh()
    assert all(n.name != "Старое ядро" for n in idx._nodes.values())
    hits = idx.lookup("обсудили ретеншн и старое ядро", strict=False)
    assert [n.name for n in hits] == ["Ретеншн"], "канон остался в индексе, заглушка не всплывает"


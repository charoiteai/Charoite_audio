"""Протокол некуда отдать: после встречи он есть, а отправить его нечем.

Саммари и Минутки лежат markdown-файлами в папке встречи. Чтобы разослать
участникам «что решили и кто что делает», человек открывает Obsidian, выделяет
текст руками, чистит вики-ссылки и вставляет в письмо — и делает это после
каждой встречи. Ни копирования, ни файла, ни готового текста в продукте не
было.

Требования, которые держит этот файл:

    1. Протокол — это решения, поручения и открытые вопросы, а НЕ стенограмма.
       Сырая расшифровка в письмо уехать не должна ни при каких флагах: это
       главный риск такой функции.
    2. Вики-ссылки и служебная разметка графа в тексте для человека не нужны:
       `[[Люди/Мария Соколова|Мария]]` — это «Мария».
    3. Пустая секция не печатается пустым заголовком: «Открытые вопросы:» без
       строк выглядит как потерянные данные.
    4. Формат `plain` пригоден для мессенджера: без markdown-заголовков.
    5. Нет встречи — внятный ответ, а не трейсбек.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import protocol  # noqa: E402

SUMMARY = """---
type: саммари
дата: 2026-07-15_1400
---
# Саммари — Платёжный провайдер

**Суть одной строкой:** выбрали ЮPay, запуск магазина 1 сентября.

## Темы
- выбор платёжного провайдера
- сроки запуска

## Решили
- 📌 берём ЮPay: 2.8% против 2.1% у FastPay, но интеграция две недели вместо шести
- 📌 сроки не двигаем: магазин открывается 1 сентября

## Поручения
- [ ] [[Люди/Мария Соколова|Мария]] — договор с ЮPay до 22.07
- [ ] [[Люди/Игорь Ветров|Игорь]] — смета на интеграцию до 18.07

## Открытые вопросы
- кто проходит PCI-сертификацию

## Связь с прошлыми встречами
- было: сравнивали провайдеров (10.07) → сегодня: выбрали
"""

MINUTES = """# Минутки

## Темы
- платёжный провайдер

## Решения
- берём ЮPay

## Поручения
- [ ] Мария — договор

## Открытые вопросы

## Риски
- сертификация может занять больше месяца
"""

TRANSCRIPT = """# Встреча 2026-07-15_1400

[14:01] Я: Давай про провайдера.
[14:01] Мария: ЮPay дороже на 0.7%, но интегрируется быстрее.
"""


def _meeting(tmp: pathlib.Path) -> pathlib.Path:
    folder = tmp / "Работа" / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер"
    folder.mkdir(parents=True)
    (folder / "Саммари.md").write_text(SUMMARY, encoding="utf-8")
    (folder / "Минутки.md").write_text(MINUTES, encoding="utf-8")
    (folder / "Стенограмма.md").write_text(TRANSCRIPT, encoding="utf-8")
    return folder


def test_protocol_carries_decisions_tasks_and_open_questions(tmp_path):
    text = protocol.build(_meeting(tmp_path))
    assert "ЮPay" in text
    assert "договор с ЮPay до 22.07" in text
    assert "кто проходит PCI-сертификацию" in text
    assert "Платёжный провайдер" in text, "нет темы встречи"
    assert "2026-07-15" in text or "15.07" in text, "нет даты встречи"


def test_raw_transcript_never_leaks_into_the_protocol(tmp_path):
    """Главный риск функции: разослать участникам всю расшифровку."""
    text = protocol.build(_meeting(tmp_path))
    assert "[14:01]" not in text, "в протокол попали реплики стенограммы"
    assert "интегрируется быстрее" not in text, "в протокол попала сырая речь"


def test_wiki_links_become_plain_names(tmp_path):
    text = protocol.build(_meeting(tmp_path))
    assert "[[" not in text and "]]" not in text, "вики-ссылки остались в тексте"
    assert "Мария" in text and "Игорь" in text, "имена потерялись вместе со ссылками"


def test_empty_sections_are_dropped(tmp_path):
    """«Открытые вопросы:» без строк — выглядит как потеря данных."""
    folder = _meeting(tmp_path)
    (folder / "Саммари.md").write_text(
        SUMMARY.replace("- кто проходит PCI-сертификацию", ""), encoding="utf-8")
    text = protocol.build(folder)
    assert "Открытые вопросы" not in text


def test_plain_format_has_no_markdown_headings(tmp_path):
    text = protocol.build(_meeting(tmp_path), style="plain")
    assert "##" not in text, "plain-формат несёт markdown-заголовки"
    assert "- [ ]" not in text, "чекбоксы markdown в письме читаются как мусор"
    assert "Мария" in text


def test_plain_format_strips_bold_markers(tmp_path):
    """Пункты Саммари приходят с «**Ирина** — …»: в мессенджере звёздочки —
    мусор (инцидент 04.08 — протокол уехал в буфер с пятью «**»)."""
    folder = tmp_path / "2026-07-15 14-00 — Жирный тест"
    folder.mkdir(parents=True)
    (folder / "Саммари.md").write_text(
        "**Суть одной строкой:** решили `быстро`.\n\n"
        "## Решили\n- **Концепция** — свести идеи в одну\n\n"
        "## Поручения\n- [ ] **Мария** — договор до 22.07\n",
        encoding="utf-8",
    )

    plain = protocol.build(folder, style="plain")
    assert "**" not in plain, "жирность уехала в мессенджер"
    assert "`" not in plain
    assert "Концепция — свести идеи в одну" in plain
    assert "Мария — договор до 22.07" in plain

    md = protocol.build(folder, style="md")
    assert "**Концепция**" in md, "markdown-стиль жирность сохраняет"


def test_minutes_are_used_when_there_is_no_summary(tmp_path):
    """Саммари пишется тяжёлой моделью и может не успеть — минутки есть всегда."""
    folder = _meeting(tmp_path)
    (folder / "Саммари.md").unlink()
    text = protocol.build(folder)
    assert "ЮPay" in text, "минутки не подхватились"


def test_latest_meeting_is_found_across_the_archive(tmp_path):
    _meeting(tmp_path)
    older = tmp_path / "Работа" / "Встречи-архив" / "2026-07-10 10-00 — Каталог"
    older.mkdir()
    (older / "Саммари.md").write_text("# Саммари — Каталог\n## Решили\n- 📌 старое\n",
                                      encoding="utf-8")
    found = protocol.latest(tmp_path / "Работа")
    assert found is not None and "2026-07-15" in found.name


def test_a_date_picks_that_meeting(tmp_path):
    _meeting(tmp_path)
    found = protocol.find(tmp_path / "Работа", "2026-07-15")
    assert found is not None and "Платёжный провайдер" in found.name
    assert protocol.find(tmp_path / "Работа", "2026-01-01") is None


def test_missing_meeting_is_explained_not_crashed(tmp_path):
    empty = tmp_path / "Пусто"
    empty.mkdir()
    assert protocol.latest(empty) is None

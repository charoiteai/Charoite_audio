"""Саммари живёт на трёх языках, и разбирается на всех сразу.

Минутки и быстрое саммари давно умеют en/zh, а архивное «Саммари.md» писалось
ВСЕГДА по-русски: в `meeting_archive` не было ни одной ветки по языку. При
`sufler.language: en` человек получал английские минутки и русскую выжимку
поверх них.

Вторая половина той же проблемы — разбор. Манифест и карточка встречи искали
русские заголовки, поэтому у нерусской встречи решения и поручения просто не
находились: разделы есть, а поля пустые.

Здесь закреплено разделение, на котором всё держится:

    генерация  — по языку конфига («на чём писать следующий документ»);
    разбор     — по всем языкам сразу («на чём написан этот файл»).

Иначе переключение языка ломает архив задним числом: старые русские встречи
перестают читаться, стоит поставить `language: en`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import meeting_archive  # noqa: E402

RU = """**Суть одной строкой:** договорились по провайдеру.

## О чём говорили
- **платежи** — сравнили двух провайдеров

## Решили
- **YuPay** — берём, комиссия 2.8%

## Поручения
- **Мария** — договор до 22.07

## Открытые вопросы
- кто платит за интеграцию
"""

EN = """**Bottom line:** the provider is picked.

## What we talked about
- **payments** — compared two providers

## Decisions
- **YuPay** — chosen, 2.8% fee

## Action items
- **Maria** — contract by 22.07

## Open questions
- who pays for the integration
"""

ZH = """**一句话概括：** 已确定支付服务商。

## 讨论了什么
- **支付** — 比较了两家服务商

## 决定
- **YuPay** — 选定，费率 2.8%

## 任务
- **玛丽亚** — 7月22日前签合同

## 待解决问题
- 集成费用由谁承担
"""


def test_gist_is_read_in_every_language():
    assert meeting_archive.summary_gist(RU) == "договорились по провайдеру."
    assert meeting_archive.summary_gist(EN) == "the provider is picked."
    assert meeting_archive.summary_gist(ZH) == "已确定支付服务商。"
    assert meeting_archive.summary_gist("нет такого маркера") is None


def test_sections_are_read_in_every_language():
    for text, decision, task, question in (
        (RU, "**YuPay** — берём, комиссия 2.8%", "**Мария** — договор до 22.07",
         "кто платит за интеграцию"),
        (EN, "**YuPay** — chosen, 2.8% fee", "**Maria** — contract by 22.07",
         "who pays for the integration"),
        (ZH, "**YuPay** — 选定，费率 2.8%", "**玛丽亚** — 7月22日前签合同",
         "集成费用由谁承担"),
    ):
        assert meeting_archive._manifest_items(
            text, meeting_archive.section_names("decisions")) == [decision]
        assert meeting_archive._manifest_items(
            text, meeting_archive.section_names("tasks")) == [task]
        assert meeting_archive._manifest_items(
            text, meeting_archive.section_names("questions")) == [question]


def test_historic_spelling_of_decisions_still_reads():
    """«## Решения» встречалось в архиве наравне с «## Решили»."""
    text = "## Решения\n- **YuPay** — берём\n"
    assert meeting_archive._manifest_items(
        text, meeting_archive.section_names("decisions")) == ["**YuPay** — берём"]


def test_language_is_taken_from_the_document_not_the_config(monkeypatch):
    """Русская встреча остаётся русской, даже когда конфиг переключили на en."""
    monkeypatch.setattr(meeting_archive, "_config_lang", lambda: "en")
    assert meeting_archive.summary_lang(RU) == "ru"
    assert meeting_archive.summary_lang(EN) == "en"
    assert meeting_archive.summary_lang(ZH) == "zh"
    # Документ без заголовков — считаем русским, как было всегда.
    assert meeting_archive.summary_lang("просто текст") == "ru"


def test_decisions_are_restored_in_the_language_of_the_document(monkeypatch):
    """Код возвращает решения в тот раздел, который в документе реально есть."""
    monkeypatch.setattr(meeting_archive, "_config_lang", lambda: "ru")
    english = EN.replace("- **YuPay** — chosen, 2.8% fee", "no decisions were made")
    fixed = meeting_archive._force_decisions(english, ["YuPay берём, комиссия 2.8%"])
    assert "## Decisions" in fixed
    assert "no decisions were made" not in fixed.lower()
    assert "YuPay берём" in fixed
    # Русское саммари лечится своим разделом.
    russian = RU.replace("- **YuPay** — берём, комиссия 2.8%", "решений не было")
    fixed_ru = meeting_archive._force_decisions(russian, ["YuPay берём"])
    assert "## Решили" in fixed_ru and "решений не было" not in fixed_ru.lower()


def test_trimming_sacrifices_sections_in_any_language(monkeypatch):
    """Лимит режет наименее ценные разделы, а не хвост документа.

    На английской выжимке раньше не срабатывало ничего: имена разделов в коде
    были русские, и под нож уходили «Action items» просто потому, что они
    последние.
    """
    monkeypatch.setattr(meeting_archive, "_config_lang", lambda: "en")
    long_topics = "\n".join(f"- **topic {i}** — {'detail ' * 20}" for i in range(3))
    text = EN.replace("- **payments** — compared two providers", long_topics)
    trimmed = meeting_archive._trim_summary(text, limit=300)
    assert len(trimmed) <= 300 + 60      # запас на неделимый заголовок
    assert "## Decisions" in trimmed, "решения переживают обрезку"
    assert "## Action items" in trimmed, "поручения переживают обрезку"
    assert "## What we talked about" not in trimmed, "обзор жертвуется первым"


def test_new_documents_follow_the_config(monkeypatch):
    """Заголовки нового саммари берутся из языка конфига."""
    for lang, head in (("ru", "Решили"), ("en", "Decisions"), ("zh", "决定")):
        monkeypatch.setattr(meeting_archive, "_config_lang", lambda lang=lang: lang)
        assert meeting_archive.SUMMARY_SECTIONS[meeting_archive._config_lang()]["decisions"] == head

"""Дату встречи модель в минутки не пишет (№738).

Модель брала самую заметную дату речи: синтетика 2026-10-17_2000 получила
«Дата/время: 26 октября (ночь)» — день переезда, о котором говорили. На
минутках владельца (21.10, 1197 файлов) поле было в 652, верная дата — в 268;
пусто или «…» — 241, только время — 131, чужая дата или слово — 12.
`fact_check` такую дату не ловит: она есть в речи. Строку в тексте никто не
читает — граф, архив и задачи берут дату из штампа в имени файла, приложение
строку пропускает; поэтому поле убрано из шаблонов, а код его не вставляет.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import llm as llm_mod  # noqa: E402
import meeting_archive  # noqa: E402
import meeting_stamp  # noqa: E402


def test_started_at_on_an_impossible_date_is_none():
    assert meeting_stamp.started_at("2026-02-30_1200") is None


def test_the_manifest_start_follows_the_stamp_grammar(tmp_path):
    """Свой регексп манифеста давал null на штампе коллизии «-1» (DS C3 входа)."""
    folder = tmp_path / "2026-08-04 12-03 — Тема"
    folder.mkdir()
    assert meeting_archive.build_manifest(folder, "2026-08-04_120312-1", "Тема")["started_at"] == "2026-08-04T12:03:00"
    assert meeting_archive.build_manifest(folder, "2026-08-04_1203", "Тема")["started_at"] == "2026-08-04T12:03:00"


def _llm(lang: str) -> llm_mod.LLM:
    return llm_mod.LLM({"sufler": {"role": "роль", "language": lang},
                        "llm": {"base_url": "http://127.0.0.1:11434", "model": "m"}})


@pytest.mark.parametrize("lang,date_label,owner_word", [
    ("ru", "Дата/время", "кто внедряет"), ("en", "Date/time", "who implements"), ("zh", "日期/时间", "谁负责执行")])
def test_the_prompt_asks_neither_the_date_nor_who_implements_a_decision(lang, date_label, owner_word):
    """Дату модель не пишет; у решения нет исполнителя — «кто внедряет» ставил
    поручения в «Решения» и уже стоил саммари раздела решений (03.08)."""
    client = _llm(lang)
    seen: dict = {}

    def fake_stream(prompt, **kw):
        seen["prompt"] = prompt
        return iter(())

    client.stream = fake_stream
    list(client.minutes("[10:00] А: начнём\n"))
    assert date_label not in seen["prompt"] and owner_word not in seen["prompt"]

"""Поручения из минуток должны доходить до окна «Задачи».

Промпт просит модель писать поручение чекбоксом, и она честно выдаёт имя,
суть и срок — но заворачивает всё в свой markdown и теряет `[ ]`. Замер по
рабочему графу: 138 файлов минуток и саммари, чекбоксы нашлись в двух. Окно
задач стояло пустым при том, что поручения формулировались на каждой встрече.
"""
import pathlib
import re
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from action_items import normalize  # noqa: E402

CHECKBOX = re.compile(r"^\s*[-*] \[[ xX]\] ", re.M)

REAL_SHAPE = """# Минутки

**Решения:**
*   **Прямое подключение** — отказ от промежуточного компонента.

**Поручения:**
*   **- **Дмитрий и Ольга** — разобраться с настройкой токенов. — **Срок: завтра**.**
*   **- **Ольга** — подключить Сергея к обсуждениям. — **Срок: текущая встреча**.**

**Открытые вопросы:**
*   Кто отвечает за квоты.
"""


def test_real_model_output_becomes_checkboxes():
    out = normalize(REAL_SHAPE)
    boxes = CHECKBOX.findall(out)
    assert len(boxes) == 2, f"поручения не стали задачами:\n{out}"
    assert "- [ ] **Дмитрий и Ольга** — разобраться" in out, out
    assert "- [ ] **Ольга** — подключить" in out, out


def test_deadline_reads_naturally():
    """Предлог «до» уместен перед датой и лишний перед «до следующего релиза»."""
    out = normalize(REAL_SHAPE)
    assert "— завтра" in out, f"срок потерялся или остался в звёздочках:\n{out}"
    assert "Срок:" not in out

    dated = normalize("**Поручения:**\n*   **- **Ольга** — смета. — **Срок: 25.07**.**")
    assert "— до 25.07" in dated, dated

    already = normalize("**Поручения:**\n*   **- **Все** — правки. — **Срок: до релиза**.**")
    assert "до до" not in already, f"удвоенный предлог: {already}"


def test_other_sections_are_untouched():
    out = normalize(REAL_SHAPE)
    assert "*   **Прямое подключение** — отказ" in out, "тронут раздел решений"
    assert "*   Кто отвечает за квоты." in out, "тронуты открытые вопросы"


def test_already_correct_checkboxes_survive():
    text = """**Поручения:**
- [ ] **Дмитрий** — согласовать бюджет — до 25.07
- [x] **Ольга** — прислать макет — сделано
"""
    out = normalize(text)
    assert out.count("- [ ]") == 1
    assert out.count("- [x]") == 1
    assert "**Дмитрий**" in out and "**Ольга**" in out


def test_english_and_chinese_sections():
    en = """**Action items:**
*   **- **Dmitry** — align the budget. — **Due: Jul 25**.**
"""
    zh = """**行动项：**
*   **- **德米特里** — 与财务对齐预算。**
"""
    assert CHECKBOX.search(normalize(en)), normalize(en)
    assert CHECKBOX.search(normalize(zh)), normalize(zh)


def test_plain_name_without_bold_is_highlighted():
    text = """**Поручения:**
- Дмитрий — согласовать бюджет с финансами
"""
    out = normalize(text)
    assert "- [ ] **Дмитрий** — согласовать" in out, out


def test_no_section_no_changes():
    text = "# Заметка\n\n- обычный пункт списка\n- ещё один\n"
    assert normalize(text) == text


def test_numbered_items_also_work():
    text = """**Поручения:**
1. **Дмитрий** — подготовить демо
2. **Ольга** — собрать правки
"""
    out = normalize(text)
    assert len(CHECKBOX.findall(out)) == 2, out

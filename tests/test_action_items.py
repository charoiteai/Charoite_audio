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


def test_live_draft_minutes_go_through_normalize():
    """Черновик минуток — итоговый документ встречи (автофинализации нет),
    и он обязан проходить normalize ДО записи на диск: 31.08 обе встречи
    дня написали поручения прозой, окно «Задачи» их не видело («58 задач
    как месяц назад»). Проверяем ПОРЯДОК, а не наличие строки: перенос
    normalize после write_text давал бы ложный зелёный (DS I2 по #462)."""
    daemon = (SRC / "daemon.py").read_text(encoding="utf-8")
    # Литерал маркера уехал в transcript.MINUTES_DRAFT_MARK (её же снимает
    # пересборка) — якорь контракта теперь запись черновика через константу.
    draft = daemon[: daemon.index('MINUTES_DRAFT_MARK + "\\n" + out')]
    tail = draft[draft.rindex("if out.strip():"):]
    assert "action_items.normalize(out)" in tail, (
        "черновиковая запись минуток должна прогонять текст через "
        "action_items.normalize до записи на диск")
    assert tail.index("action_items.normalize(out)") < tail.index("minutes_lock"), (
        "normalize обязан отработать ДО входа в блок записи под замком")


def test_normalize_handles_the_bare_caps_section_of_2026_08_31():
    """Реальная форма минуток 31.08: заголовок «ПОРУЧЕНИЯ:» голым капсом и
    пункты «- **Имя (Кто)** — что сделать» — должны стать чекбоксами."""
    real = (
        "Темы:\n- Статус релиза\n\n"
        "ПОРУЧЕНИЯ:\n"
        "- **Собеседник 2 (Мира)** — проверить настройку календаря.\n"
        "- **Инга** — связаться с Верой по задаче 999999.\n\n"
        "Открытые вопросы:\n- Перенос релиза\n"
    )
    out = normalize(real)
    boxes = CHECKBOX.findall(out)
    assert len(boxes) == 2, out
    assert "- [ ] **Собеседник 2 (Мира)**" in out
    assert "Открытые вопросы" in out and "- [ ] Перенос релиза" not in out



def test_indented_label_inside_the_section_does_not_end_it():
    """«Срок:» с продолжением на следующей строке — часть пункта, а не
    заголовок: закрытие раздела по любому двоеточию роняло следующие
    поручения (DS I1 — отступленный вариант, GLM I1 — без отступа).
    Голая строка закрывает раздел только как ИЗВЕСТНАЯ секция минуток."""
    text = (
        "Поручения:\n"
        "- **Мира** — проверить настройку календаря.\n"
        "  Срок:\n"
        "  10.09\n"
        "- **Вера** — связаться с клиентом.\n"
        "Срок:\n"
        "12.09\n"
        "- **Инга** — прислать смету.\n"
        "\nОткрытые вопросы:\n- Качество модели\n"
    )
    out = normalize(text)
    assert len(CHECKBOX.findall(out)) == 3, out
    assert "- [ ] **Вера**" in out and "- [ ] **Инга**" in out
    assert "- [ ] Качество модели" not in out


def test_name_block_and_hard_wrap_do_not_close_the_section():
    """Раскладка «по исполнителям» («Мира:») и жёсткий перенос строки,
    оканчивающийся «сроки:», — не заголовки: поручения после них обязаны
    дойти до окна (GLM I1 по #462, контрпримеры 2 и 3)."""
    by_owner = (
        "Поручения:\n- общий пункт\n\n"
        "Мира:\n- проверить дату релиза\n- **Вера** — вторая задача\n"
    )
    out = normalize(by_owner)
    assert len(CHECKBOX.findall(out)) == 3, out
    assert "- [ ] **Вера** — вторая задача" in out

    wrapped = (
        "Поручения:\n- **Мира** — напомнить, что на прошлом созвоне обсудили следующие\n"
        "сроки:\n10.09 — релиз 2.1\n- **Вера** — уточнить статус\n"
    )
    out = normalize(wrapped)
    assert "- [ ] **Вера** — уточнить статус" in out, out


def test_bare_known_section_still_closes():
    """Известные секции минуток («Риски:», «Вопросы:») закрывают раздел и
    голой строкой — их пункты не должны становиться задачами."""
    text = "Поручения:\n- **Мира** — проверить дату.\nРиски:\n- смещение релиза\n"
    out = normalize(text)
    assert len(CHECKBOX.findall(out)) == 1, out
    assert "- [ ] смещение релиза" not in out


def test_long_bare_heading_still_closes_the_section():
    """Длина заголовка больше не влияет: раньше `{0,60}` создавал слепую
    зону, и длинный заголовок возвращал утечку хвоста в задачи (DS M1)."""
    text = (
        "Поручения:\n- **Мира** — проверить дату.\n\n"
        "Открытые вопросы по сегодняшней встрече и всем рабочим задачам:\n"
        "- Качество модели\n"
    )
    out = normalize(text)
    assert len(CHECKBOX.findall(out)) == 1, out
    assert "- [ ] Качество модели" not in out


def test_en_dash_bullet_is_converted_not_treated_as_heading():
    """Пункт, открытый типографским тире, — пункт, а не заголовок: раньше он
    и раздел закрывал, и сам терялся (DS M2). Второй вход — с двоеточием на
    конце («– Приоритет: высокий»): страж пунктов не даёт ему закрыть раздел,
    и поручение после него живо (GLM M3 — тест обещал двоеточие, но не
    проверял его)."""
    text = "Поручения:\n– **Мира** — проверить дату.\n"
    out = normalize(text)
    assert len(CHECKBOX.findall(out)) == 1, out

    with_colon = (
        "Поручения:\n- **Мира** — задача раз.\n"
        "– Приоритет: высокий\n- **Вера** — задача два.\n"
    )
    out = normalize(with_colon)
    assert "- [ ] **Вера** — задача два" in out, out


def test_deadline_in_live_speech_is_left_alone():
    """«Срок:» в середине живой формулировки — не хвост пункта: normalize
    теперь бежит по черновику, который читает человек, и «обсудить срок:
    завтра решаем» превращалось в «обсудить — завтра решаем» (advisory DS
    по #462). Причёсывается только хвост после тире-разделителя."""
    text = "Поручения:\n- **Мира** — обсудить срок: завтра решаем\n"
    out = normalize(text)
    assert "- [ ] **Мира** — обсудить срок: завтра решаем" in out, out

    # без тире перед «Срок:» строка остаётся как есть — читаемая цена
    plain = normalize("Поручения:\n- **Вера** — прислать смету. Срок: 10.09\n")
    assert "- [ ] **Вера** — прислать смету. Срок: 10.09" in plain, plain


def test_deadline_after_dash_is_still_prettified():
    """Канонический хвост « — Срок: …» жив и после ужесточения."""
    out = normalize("Поручения:\n- **Мира** — собрать данные — Срок: 10.09\n")
    assert "- [ ] **Мира** — собрать данные — до 10.09" in out, out


def test_long_unclosed_name_does_not_leave_dangling_bold():
    """Съеденные обёрткой звёздочки на длинном имени не восстанавливаются —
    но и висячие `**` в живом документе не остаются (Minor DS по #462):
    непарные звёздочки снимаются целиком."""
    name = "Рабочая группа по интеграции и сопровождению внешних подрядчиков корпоративного контура"
    out = normalize(f"**Поручения:**\n*   **- {name}** — подготовить регламент.**\n")
    line = next(l for l in out.split("\n") if l.startswith("- [ ]"))
    assert line.count("**") % 2 == 0, out


def test_mcp_minutes_normalize_before_write():
    """Третий путь записи минуток — mcp-«Минутки» — обязан прогонять
    normalize ДО записи (№141): порядок, а не наличие строки — перенос
    после write_text давал бы ложный зелёный (урок DS I2 по #462)."""
    mcp = (SRC / "mcp_server.py").read_text(encoding="utf-8")
    fn = mcp[mcp.index("def sufler_make_minutes"):]
    fn = fn[: fn.index("\n@")]                     # тело одного инструмента
    assert "action_items.normalize(out)" in fn, (
        "mcp-путь минуток должен звать action_items.normalize")
    assert fn.index("action_items.normalize(out)") < fn.index("tmp.write_text"), (
        "normalize обязан отработать ДО записи файла")


def test_pair_restore_leftover_bold_is_cleaned():
    """Восстановление парности само создаёт нечёт: «Проверить **договор** —
    …» получало ведущие ** и оставляло хвост «договор**» жирным до конца
    строки (GLM M9 по #464). Страховка снимает ВСЕ ** при нечётности —
    последней, когда «— **Срок: …» уже разобран."""
    out = normalize("**Поручения:**\n*   Проверить **договор** — до пятницы\n")
    line = next(l for l in out.split("\n") if l.startswith("- [ ]"))
    assert line.count("**") % 2 == 0, out

    # канонический пункт с «— **Срок:» страховкой не задет
    ok = normalize("**Поручения:**\n*   **- **Ольга** — смета. — **Срок: 25.07**.**")
    assert "- [ ] **Ольга** — смета. — до 25.07" in ok, ok


def test_bold_name_with_bold_deadline_keeps_the_name_bold():
    """Регресс-щит на ложную находку DS r2 M3: «**Имя** — задача —
    **Срок: завтра**.» — хвостовые звёзды съедаются ДО _deadline, счёт **
    остаётся чётным, и страховка не снимает жирность имени."""
    out = normalize("**Поручения:**\n*   **Мира** — задача — **Срок: завтра**.\n")
    assert "- [ ] **Мира** — задача — завтра" in out, out

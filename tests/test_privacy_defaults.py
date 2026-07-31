"""PRIVACY.md обещает: «Cloud layer is opt-in and off by default».

Обещание, которое не проверяется тестом, — это комментарий. Сейчас решение
«уходит ли стенограмма с машины» принимается в двух местах разными
выражениями, и они не совпадают:

    graph_updater.py:522  cfg["sufler"].get("cloud_enrich")            → fail-closed
    daemon.py:354         bool(cfg["sufler"].get("cloud_live", True))  → fail-OPEN

Второе означает: конфиг без ключа cloud_live (свой, дореформенный, урезанный
— любой) включает живые запросы к Anthropic по ходу встречи. Дефолт «True» в
коде переживает любой правильный дефолт в config.example.yaml, потому что
пример конфига — не то, что читает демон у пользователя.

Отсюда требование: ОДНА точка решения (src/privacy.py), fail-closed, с
kill-switch SUFLER_NO_CLOUD поверх конфига.

Второй заход (30.07): тумблеров облака стало четыре, а под этим правилом
жили два. `cloud_hints` читался прямо в src/daemon.py, `cloud_edit_graph` — в
scripts/nightly_dossier_review.py через `bool(...)`, то есть строка «false» из
конфига давала облаку право ПРАВИТЬ граф. Сторож обхода знал те же два имени и
не видел ни одного из этих чтений. Здесь под правило заведены все четыре: имя
ключа больше не перечисляется руками, ловится любой `cloud_*`, кроме имён
моделей.
"""
import pathlib
import re
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

import privacy  # noqa: E402

EXAMPLE = REPO / "config" / "config.example.yaml"

GATES = (privacy.cloud_live_enabled, privacy.cloud_enrich_enabled,
         privacy.cloud_hints_enabled, privacy.cloud_edit_graph_enabled)

# Чтение ключа из словаря — и .get(), и прямое индексирование, кавычки любые.
# Ловим любой ключ облака, а не список из двух: новый тумблер должен попадать
# под правило сам, без правки регулярки. Имена моделей (cloud_live_model,
# cloud_model) — не разрешения, их читать напрямую никто не запрещал.
_DIRECT = re.compile(
    r"""(?:\.get\(\s*|\[\s*)["'](cloud_(?!\w*model\b)[a-z_]+)["']""")

# Файлы, которые ходят в облако и потому обязаны спрашивать privacy.py.
# scripts/ — тоже: ночная ревизия досье живёт там.
DECIDERS = ("src/daemon.py", "src/graph_updater.py",
            "scripts/nightly_dossier_review.py", "scripts/nightly_claude_cores.py")

# Документы, которые обещают пользователю приватность. Все языки, а не только
# английский: обещание не имеет права отличаться от перевода к переводу.
PRIVACY_DOCS = ("PRIVACY.md", "docs/ru/PRIVACY.md", "docs/zh/PRIVACY.md")


def _direct_reads(text: str) -> list[str]:
    """Тумблеры облака, которые этот текст читает из конфига сам."""
    return sorted(set(_DIRECT.findall(text)))


def test_no_key_means_no_cloud():
    """Ключа нет — облако молчит. Главный инвариант файла."""
    for gate in GATES:
        assert gate({"sufler": {}}, {}) is False, gate.__name__


def test_no_sufler_section_means_no_cloud():
    for gate in GATES:
        assert gate({}, {}) is False, gate.__name__


def test_junk_value_means_no_cloud():
    """Мусор в конфиге — не разрешение. Строка «false» тем более."""
    for gate, key in ((privacy.cloud_live_enabled, "cloud_live"),
                      (privacy.cloud_enrich_enabled, "cloud_enrich")):
        for value in (None, "", 0, "false", "нет", []):
            assert gate({"sufler": {key: value}}, {}) is False, f"{gate.__name__}={value!r}"


def test_junk_value_never_grants_the_right_to_edit_the_graph():
    """`bool("false")` — это True, и именно так читался cloud_edit_graph.

    Цена ошибки здесь выше обычной: этот ключ даёт облаку право ПЕРЕПИСЫВАТЬ
    файлы графа, а не только прочитать их. Человек, написавший в конфиге
    строку вместо булева (кавычки в YAML — обычная опечатка), получал ночную
    правку своих досье.
    """
    for value in (None, "", 0, "false", "нет", [], "no"):
        cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": value}}
        assert privacy.cloud_edit_graph_enabled(cfg, {}) is False, repr(value)


def test_explicit_true_enables():
    assert privacy.cloud_live_enabled({"sufler": {"cloud_live": True}}, {}) is True
    assert privacy.cloud_enrich_enabled({"sufler": {"cloud_enrich": True}}, {}) is True


def test_hints_need_both_their_own_switch_and_the_live_layer():
    """Облачные подсказки — тот же живой канал, что ответы: два разрешения.

    Отдельный тумблер у них потому, что подсказки стреляют часто (постоянный
    поток стенограммы), а не потому, что они самостоятельный путь в сеть.
    """
    both = {"sufler": {"cloud_hints": True, "cloud_live": True}}
    assert privacy.cloud_hints_enabled(both, {}) is True
    assert privacy.cloud_hints_enabled({"sufler": {"cloud_hints": True}}, {}) is False
    assert privacy.cloud_hints_enabled({"sufler": {"cloud_live": True}}, {}) is False


def test_graph_edits_need_both_their_own_switch_and_the_enrich_layer():
    both = {"sufler": {"cloud_edit_graph": True, "cloud_enrich": True}}
    assert privacy.cloud_edit_graph_enabled(both, {}) is True
    assert privacy.cloud_edit_graph_enabled({"sufler": {"cloud_edit_graph": True}}, {}) is False


def test_kill_switch_beats_config():
    """SUFLER_NO_CLOUD — рубильник поверх любого «да» в конфиге.

    Все тумблеры сразу: «этот запуск строго офлайн» не должно зависеть от
    того, какой именно облачный шаг человек когда-то включил.
    """
    cfg = {"sufler": {"cloud_live": True, "cloud_enrich": True,
                      "cloud_hints": True, "cloud_edit_graph": True}}
    for gate in GATES:
        assert gate(cfg, {"SUFLER_NO_CLOUD": "1"}) is False, gate.__name__
        assert gate(cfg, {"CHAROITE_NO_CLOUD": "1"}) is False, gate.__name__


def test_example_config_ships_with_cloud_off():
    cfg = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    for gate in GATES:
        assert gate(cfg, {}) is False, gate.__name__


def test_nobody_decides_about_the_cloud_on_their_own():
    """Единственная точка решения — иначе дефолты снова разъедутся.

    Тест по исходникам, а не по поведению: daemon.py не импортируется без
    PortAudio, а инвариант тут именно структурный — «нигде, кроме privacy.py,
    не читают ключи облака напрямую».
    """
    for name in DECIDERS:
        offenders = _direct_reads((REPO / name).read_text(encoding="utf-8"))
        assert not offenders, \
            f"{name} решает про облако сам ({', '.join(offenders)}) — решение живёт в privacy.py"


def _toggles_in_example() -> set[str]:
    """Тумблеры облака из примера конфига: булевы ключи cloud_*, не модели."""
    sufler = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))["sufler"]
    return {k for k, v in sufler.items()
            if k.startswith("cloud_") and isinstance(v, bool)}


def test_privacy_knows_every_toggle_the_config_offers():
    """Ненайденный тумблер — хуже включённого: человек о нём не знает.

    PRIVACY.md — документ, который читают, чтобы решить, можно ли доверить
    продукту чужие переговоры. Тумблер, отправляющий стенограмму и не
    названный там, обесценивает весь документ. Так и было: перечислялись
    cloud_enrich и cloud_live, а cloud_hints (стенограмма в облако на КАЖДУЮ
    подсказку) и cloud_edit_graph (право переписывать граф) — нет.
    """
    toggles = _toggles_in_example()
    assert len(toggles) >= 4, f"тумблеры облака перестали находиться: {toggles}"
    for doc in PRIVACY_DOCS:
        text = (REPO / doc).read_text(encoding="utf-8")
        missing = sorted(t for t in toggles if t not in text)
        assert not missing, (
            f"{doc} не называет тумблеры: {', '.join(missing)} — "
            "пользователь не может узнать о них, не читая исходники")


def test_privacy_knows_every_gate_the_code_offers():
    """Гейт есть, а в примере конфига ключа нет — тоже расхождение."""
    keys = set(privacy.KEYS)
    assert keys == _toggles_in_example(), (
        f"privacy.KEYS и config.example.yaml разошлись: "
        f"только в KEYS — {sorted(keys - _toggles_in_example())}, "
        f"только в примере — {sorted(_toggles_in_example() - keys)}")


BYPASSES = (
    'cfg["sufler"].get("cloud_live")',
    "cfg['sufler'].get('cloud_live')",
    'cfg["sufler"]["cloud_enrich"]',
    "cfg['sufler']['cloud_enrich']",
    'cfg.get("sufler", {}).get("cloud_live", True)',
    '(cfg.get("sufler") or {}).get("cloud_enrich")',
    # тумблеры, добавленные позже: сторож обязан видеть и их, не зная имён
    'cfg["sufler"].get("cloud_hints", False)',
    'may_edit = bool(cfg["sufler"].get("cloud_edit_graph"))',
    'cfg["sufler"].get("cloud_something_new")',
)
NOT_BYPASSES = (
    'model = cfg["sufler"].get("cloud_live_model", "claude-sonnet-5")',
    'model = cfg["sufler"].get("cloud_model", "claude-opus-5")',
    'hints = cfg["sufler"].get("cloud_hints_model", "claude-haiku-4-5")',
    'emit({"type": "status", "text": "облако выключено: sufler.cloud_live"})',
    "cloud_live = privacy.cloud_live_enabled(cfg)",
)


def test_the_bypass_detector_sees_every_shape_of_read():
    """Сторож ищет обход строкой в исходниках — значит он сам нуждается в тесте.

    Раньше сторож искал ровно `get("cloud_live"` — только двойные кавычки и
    только .get. Обход одинарными кавычками или прямым индексированием он
    пропускал молча, и «одна точка решения» держалась на том, что никто так
    не написал. Инвариант, который ловит одну форму из пяти, — не инвариант.
    """
    for src in BYPASSES:
        assert _direct_reads(src), f"обход не замечен: {src}"
    for src in NOT_BYPASSES:
        assert not _direct_reads(src), f"ложная тревога: {src}"

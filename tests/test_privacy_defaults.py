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
"""
import pathlib
import sys

import yaml

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import privacy  # noqa: E402

EXAMPLE = pathlib.Path(__file__).resolve().parent.parent / "config" / "config.example.yaml"

GATES = (privacy.cloud_live_enabled, privacy.cloud_enrich_enabled)

TOGGLES = ("cloud_live", "cloud_enrich")


def _direct_reads(text: str) -> list[str]:
    """Тумблеры облака, которые этот текст читает из конфига сам."""
    return [k for k in TOGGLES if f'get("{k}"' in text]


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


def test_explicit_true_enables():
    assert privacy.cloud_live_enabled({"sufler": {"cloud_live": True}}, {}) is True
    assert privacy.cloud_enrich_enabled({"sufler": {"cloud_enrich": True}}, {}) is True


def test_kill_switch_beats_config():
    """SUFLER_NO_CLOUD — рубильник поверх любого «да» в конфиге."""
    cfg = {"sufler": {"cloud_live": True, "cloud_enrich": True}}
    for gate in GATES:
        assert gate(cfg, {"SUFLER_NO_CLOUD": "1"}) is False, gate.__name__


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
    for name in ("daemon.py", "graph_updater.py"):
        offenders = _direct_reads((SRC / name).read_text(encoding="utf-8"))
        assert not offenders, \
            f"{name} решает про облако сам ({', '.join(offenders)}) — решение живёт в privacy.py"


BYPASSES = (
    'cfg["sufler"].get("cloud_live")',
    "cfg['sufler'].get('cloud_live')",
    'cfg["sufler"]["cloud_enrich"]',
    "cfg['sufler']['cloud_enrich']",
    'cfg.get("sufler", {}).get("cloud_live", True)',
    '(cfg.get("sufler") or {}).get("cloud_enrich")',
)
NOT_BYPASSES = (
    'model = cfg["sufler"].get("cloud_live_model", "claude-sonnet-5")',
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

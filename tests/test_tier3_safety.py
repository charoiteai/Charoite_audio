"""Tier3 — единственное место, где автомат ПЕРЕЗАПИСЫВАЕТ файл пользователя.

Дубль превращается в redirect-заглушку из пяти строк, оригинал остаётся
только в Ядра/.tier3_backup/. Два свойства должен держать тест, а не
аккуратность вызывающего кода:

1) БЕЗ явного apply=True revise не пишет на диск ничего. Сейчас дефолт
   apply=True, и graph_updater вызывает revise() без аргумента — то есть
   после каждой встречи автомат молча сливает ядра. Ручной CLI
   (scripts/tier3_cores.py) при этом безопасен по умолчанию: режимы
   разошлись, и опасный достался тому пути, который пользователь не звал.

2) Сравнивается СОДЕРЖАНИЕ ядра, а не одно имя файла. load_cores берёт
   суть как sect("Суть") or sect("Задача одной фразой"), но upsert_core
   пишет ядра из «## Статус» и «## Хроника» — секции «## Суть» в
   продуктовых ядрах не существует ни в одной. Значит repr сводится к
   «<имя файла>. », и NLI судит пары голых заголовков без контекста.

Оба дефекта чинятся одним заходом: если научить load_cores видеть суть,
не тронув дефолт apply, автомат начнёт сливать не вслепую, а уверенно —
и станет опаснее, чем был.

NLI и Ollama здесь не поднимаются: обе внешние зависимости подменены на
«уверенный дубль». Тест проверяет поведение автомата, а не качество модели.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import nli  # noqa: E402
import tier3  # noqa: E402

# Ровно тот формат, который пишет graph_updater.upsert_core:
# фронтматтер, «## Статус» с меткой обновления, «## Хроника» со ссылками.
CORE_A = """---
type: ядро
вид: задача
tags: [ядро, авто]
---
# Оплата картой

## Статус
Подключаем эквайринг ЮPay, ждём PCI-аудит _(обновлено 2026-07-20)_

## Хроника
- [[Встречи/2026-07-20|2026-07-20]] — выбрали провайдера
"""

CORE_B = """---
type: ядро
вид: задача
tags: [ядро, авто]
---
# Приём платежей

## Статус
Эквайринг ЮPay на подключении, PCI-аудит в очереди _(обновлено 2026-07-21)_

## Хроника
- [[Встречи/2026-07-21|2026-07-21]] — согласовали сроки
"""


@pytest.fixture
def graph(tmp_path, monkeypatch):
    folder = tmp_path / "Ядра"
    folder.mkdir()
    (folder / "Оплата картой.md").write_text(CORE_A, encoding="utf-8")
    (folder / "Приём платежей.md").write_text(CORE_B, encoding="utf-8")
    monkeypatch.setattr(nli, "is_available", lambda: True)
    monkeypatch.setattr(tier3, "_embed_all", lambda cores: [[1.0, 0.0]] * len(cores))
    monkeypatch.setattr(nli, "entail_prob", lambda a, b: 0.99)
    return tmp_path


def _snapshot(graph: pathlib.Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in (graph / "Ядра").glob("*.md")}


def test_revise_is_read_only_by_default(graph):
    """Вызов без apply не имеет права трогать граф."""
    before = _snapshot(graph)
    report = tier3.revise(graph)
    assert report["dups"], "дубль не найден — тест ничего не проверяет"
    assert _snapshot(graph) == before, "revise без apply=True переписал ядра"
    assert not (graph / "Ядра" / ".tier3_backup").exists(), \
        "бэкап создан — значит автомат собирался писать"


def test_revise_still_merges_when_asked(graph):
    """Осторожный дефолт не должен ломать сам механизм слияния."""
    report = tier3.revise(graph, apply=True)
    assert report["log"], "с apply=True слияние обязано произойти"
    texts = _snapshot(graph).values()
    assert any("Дубль. Смерджен" in t for t in texts)
    assert (graph / "Ядра" / ".tier3_backup").is_dir(), "слияние без бэкапа"


def test_essence_comes_from_the_section_that_actually_exists(tmp_path):
    """У продуктового ядра нет «## Суть» — суть обязана браться из статуса."""
    folder = tmp_path / "Ядра"
    folder.mkdir()
    (folder / "Оплата картой.md").write_text(CORE_A, encoding="utf-8")
    core = tier3.load_cores(folder)[0]
    assert core["essence"], "суть пустая: NLI получит голое имя файла"
    assert "эквайринг" in core["repr"].lower(), \
        f"в текст для NLI не попало содержание ядра: {core['repr']!r}"


def test_service_date_marker_does_not_leak_into_nli_input(tmp_path):
    """«_(обновлено 2026-07-20)_» — служебная метка, не смысл ядра.

    Две одинаковые темы, размеченные разными датами, не должны из-за этого
    расходиться по NLI, а одинаковые даты — сближать разные темы.
    """
    folder = tmp_path / "Ядра"
    folder.mkdir()
    (folder / "Оплата картой.md").write_text(CORE_A, encoding="utf-8")
    core = tier3.load_cores(folder)[0]
    assert "обновлено" not in core["repr"], \
        f"служебная метка попала в текст для NLI: {core['repr']!r}"
    assert core["date"] == "2026-07-20", "дата обновления перестала разбираться"


def test_explicit_suti_section_still_wins(tmp_path):
    """Если «## Суть» всё же написана руками — она главнее статуса."""
    folder = tmp_path / "Ядра"
    folder.mkdir()
    (folder / "Ядро.md").write_text(
        "# Ядро\n\n## Суть\nручная формулировка темы\n\n"
        "## Статус\nавтостатус _(обновлено 2026-07-20)_\n", encoding="utf-8")
    core = tier3.load_cores(folder)[0]
    assert core["essence"] == "ручная формулировка темы"

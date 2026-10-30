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

3) С №628 ядро с одним автостатусом не судится вовсе: «запасная» суть из
   статуса дала 0 явных дублей из 10 уверенных пар (05.10), а ночь с ревизией
   шла втрое дольше. Решение 19.08 «требовать «## Суть» — значит выключить
   --apply молча» отменено замером; молчания нет — исход «no_work» называет,
   у скольких ядер своя суть.

NLI и Ollama здесь не поднимаются: обе внешние зависимости подменены на
«уверенный дубль». Тест проверяет поведение автомата, а не качество модели.
"""
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import tier3  # noqa: E402

# Ровно тот формат, который пишет graph_updater.upsert_core:
# фронтматтер, «## Статус» с меткой обновления, «## Хроника» со ссылками.

def _open() -> bool:
    """Ночное окно открыто: гейт вызывающего, который модулю графа передают параметром."""
    return True

def fake_embedder(vectors=None):
    """Векторизатор-подделка: заданные векторы или по одному на ядро."""
    from charoite_graph.model_seam import Embedder

    def run(texts, timeout):
        return vectors if vectors is not None else [[1.0, 0.0] for _ in texts]

    return Embedder(run, "test-fake")


def fake_judge(p=0.0, ready=True, refused="", entail=None):
    """Судья-подделка. По умолчанию доступен, готов и ничего не подтверждает.

    Отдельный `entail` — чтобы проверять отказ судьи посреди прогона: он
    бросает, а не возвращает ноль, и пара обязана вернуться в фокус.
    """
    from charoite_graph.model_seam import Judge

    return Judge(lambda: ready, entail or (lambda a, b: p), refused)


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


# То же самое, но с написанной руками «## Суть»: NLI судит формулировку темы,
# а не сегодняшний автостатус. Это единственный случай, когда автомату
# разрешено необратимое слияние.
CORE_C = """---
type: ядро
вид: задача
tags: [ядро, авто]
---
# Оплата картой

## Суть
Приём платежей банковскими картами на сайте

## Статус
Подключаем эквайринг ЮPay, ждём PCI-аудит _(обновлено 2026-07-20)_

## Хроника
- [[Встречи/2026-07-20|2026-07-20]] — выбрали провайдера
"""

CORE_D = """---
type: ядро
вид: задача
tags: [ядро, авто]
---
# Приём платежей

## Суть
Оплата картой на сайте: подключение эквайринга

## Статус
Эквайринг ЮPay на подключении, PCI-аудит в очереди _(обновлено 2026-07-21)_

## Хроника
- [[Встречи/2026-07-21|2026-07-21]] — согласовали сроки
"""


# Два ЖИВЫХ потока одного проекта. Их ведут на одних и тех же встречах, и
# сегодняшний автостатус у них общий на вид — ровно та пара, на которой
# слияние по статусу и ошибается. Отличает её от двойников хроника.
CORE_E = """---
type: ядро
вид: задача
tags: [ядро, авто]
---
# Личный кабинет

## Статус
Собираем требования, ждём макеты от дизайна _(обновлено 2026-07-21)_

## Хроника
- [[Встречи/2026-07-20|2026-07-20]] — завели тему
- [[Встречи/2026-07-21|2026-07-21]] — уточнили объём
"""

#: Сила следования, которую отдаёт судья-подделка текущего теста. Раньше здесь
#: стояли четыре подмены модульных атрибутов; теперь способности приезжают в
#: `revise` значениями, и тесту достаточно сказать, каким он хочет судью.
_P = {"p": 0.99}


def _judge(monkeypatch, p: float):
    """Каждая пара следует друг из друга с силой p."""
    _P["p"] = p
    monkeypatch.setitem(_P, "p", p)


def _confident(monkeypatch):
    """NLI и Ollama подменены на «уверенный дубль» с обеих сторон."""
    _judge(monkeypatch, 0.99)


def _pair(tmp_path, monkeypatch, first: str, second: str, p: float) -> pathlib.Path:
    folder = tmp_path / "Ядра"
    folder.mkdir()
    for text in (first, second):
        name = re.search(r"^# (.+)$", text, re.M).group(1)
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
    _judge(monkeypatch, p)
    return tmp_path


@pytest.fixture
def graph(tmp_path, monkeypatch):
    """Пара ядер БЕЗ «## Суть» — ровно то, что пишет upsert_core."""
    folder = tmp_path / "Ядра"
    folder.mkdir()
    (folder / "Оплата картой.md").write_text(CORE_A, encoding="utf-8")
    (folder / "Приём платежей.md").write_text(CORE_B, encoding="utf-8")
    _confident(monkeypatch)
    return tmp_path


@pytest.fixture
def graph_with_essence(tmp_path, monkeypatch):
    """Пара ядер с написанной руками «## Суть»."""
    folder = tmp_path / "Ядра"
    folder.mkdir()
    (folder / "Оплата картой.md").write_text(CORE_C, encoding="utf-8")
    (folder / "Приём платежей.md").write_text(CORE_D, encoding="utf-8")
    _confident(monkeypatch)
    return tmp_path


def _snapshot(graph: pathlib.Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in (graph / "Ядра").glob("*.md")}


def test_revise_is_read_only_by_default(graph_with_essence):
    """Вызов без apply не имеет права трогать граф."""
    graph = graph_with_essence
    before = _snapshot(graph)
    report = tier3.revise(graph, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)
    assert report["dups"], "дубль не найден — тест ничего не проверяет"
    assert _snapshot(graph) == before, "revise без apply=True переписал ядра"
    assert not (graph / "Ядра" / ".tier3_backup").exists(), \
        "бэкап создан — значит автомат собирался писать"


def test_revise_still_merges_when_asked(graph_with_essence):
    """Осторожный дефолт не должен ломать сам механизм слияния."""
    report = tier3.revise(graph_with_essence, apply=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)
    assert report["log"], "с apply=True слияние обязано произойти"
    texts = _snapshot(graph_with_essence).values()
    assert any("Дубль. Смерджен" in t for t in texts)
    assert (graph_with_essence / "Ядра" / ".tier3_backup").is_dir(), "слияние без бэкапа"


def test_mark_annotates_but_never_merges(graph_with_essence):
    """Находку надо оставить в графе, даже когда сливать не разрешено.

    Пометка обратима (строка-цитата в конце файла), и именно её читает
    morning_brief: «Tier3 просит свести вручную». Без отдельного mark
    выключенный автомат = полное молчание: дубль найден, а узнать о нём
    можно только из лога того прогона, которого никто не видел.
    """
    report = tier3.revise(graph_with_essence, mark=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)
    texts = _snapshot(graph_with_essence)
    assert not any("Дубль. Смерджен" in t for t in texts.values()), \
        "mark=True не имеет права сливать — слияние необратимо для пользователя"
    assert len(texts) == 2, "оба ядра должны остаться файлами, а не redirect-заглушкой"
    assert all("возможный дубль" in t for t in texts.values()), \
        "пометка не проставлена — morning_brief не увидит находку"
    assert report["log"], "правка была, а лог пустой"


def test_mark_marks_nesting_too(tmp_path, monkeypatch):
    """Вложение — тоже обратимая правка: взаимные ссылки, без слияния."""
    folder = tmp_path / "Ядра"
    folder.mkdir()
    (folder / "Часть.md").write_text(
        "# Часть\n\n## Суть\nэпизод внутри большой темы\n", encoding="utf-8")
    (folder / "Целое.md").write_text(
        "# Целое\n\n## Суть\nбольшая сквозная тема\n", encoding="utf-8")
    # часть ⊂ целое: одна сторона уверенно следует, обратно — нет
    nesting = fake_judge(entail=lambda a, b: 0.95 if a.startswith("Часть") else 0.1)
    report = tier3.revise(tmp_path, mark=True, embedder=fake_embedder(), judge=nesting, may_continue=_open)
    assert report["nests"], "вложение не найдено — тест ничего не проверяет"
    texts = _snapshot(tmp_path)
    assert all("Tier3-NLI" in t for t in texts.values()), "ссылки-подсказки не вписаны"


def _counting_embedder(calls: list):
    """Подделка эмбеддера, которая считает, сколько текстов ей отдали."""
    from charoite_graph.model_seam import Embedder

    def run(texts, timeout):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    return Embedder(run, "test-fake")


def test_cores_with_only_autostatus_are_not_judged(graph):
    """Ядра от upsert_core (один «## Статус») не судятся вовсе (№628).

    Автостатус шаблонный, у разных тем он следует друг из друга на 0.93–1.00:
    замер 05.10 — явных дублей 0 из 10 уверенных пар. Судья здесь уверен на
    0.99, и всё равно: ни правки, ни бэкапа, ни вызова эмбеддера, а исход
    называет причину словами — не молчание и не «лежит Ollama».
    """
    calls: list = []
    before = _snapshot(graph)
    report = tier3.revise(graph, apply=True, mark=True, embedder=_counting_embedder(calls),
                          judge=fake_judge(p=0.99), may_continue=_open)
    assert report["status"] == "no_work", report
    assert "«## Суть»" in report["reason"] and "0 из 2" in report["reason"], report["reason"]
    assert not calls, "эмбеддер разбужен ради ядер, которые не судятся"
    assert _snapshot(graph) == before
    assert not (graph / "Ядра" / ".tier3_backup").exists()
    assert not report["dups"] and not report["nests"] and not report["pending_merges"]


def test_a_core_without_own_essence_never_enters_a_pair(tmp_path, monkeypatch):
    """Смешанная пара (своя суть и автостатус) не судится: судимых ядер одно."""
    graph = _pair(tmp_path, monkeypatch, CORE_C, CORE_B, 0.99)
    calls: list = []
    report = tier3.revise(graph, apply=True, embedder=_counting_embedder(calls),
                          judge=fake_judge(p=0.99), may_continue=_open)
    assert report["status"] == "no_work", report
    assert "1 из 2" in report["reason"], report["reason"]
    assert not calls


def test_only_cores_with_own_essence_reach_the_embedder(tmp_path, monkeypatch):
    """Эмбеддер получает только судимые ядра, пары строятся только из них."""
    folder = tmp_path / "Ядра"
    folder.mkdir()
    for text in (CORE_C, CORE_D, CORE_E):
        name = re.search(r"^# (.+)$", text, re.M).group(1)
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
    calls: list = []
    report = tier3.revise(tmp_path, mark=True, embedder=_counting_embedder(calls),
                          judge=fake_judge(p=0.99), may_continue=_open)
    assert [len(c) for c in calls] == [2], calls
    assert not any("Личный кабинет" in t for c in calls for t in c), calls
    assert (report["judged_cores"], report["cores_total"]) == (2, 3)
    assert "Tier3-NLI" not in (folder / "Личный кабинет.md").read_text(encoding="utf-8")


def test_focus_without_own_essence_is_no_work(tmp_path, monkeypatch):
    """После встречи фокус — её ядра; без своей сути у них судить нечего, и
    эмбеддер не будится ради ядер, которых встреча не касалась."""
    folder = tmp_path / "Ядра"
    folder.mkdir()
    for text in (CORE_C, CORE_D, CORE_E):
        name = re.search(r"^# (.+)$", text, re.M).group(1)
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
    calls: list = []
    report = tier3.revise(tmp_path, only_names=["Личный кабинет"], mark=True,
                          embedder=_counting_embedder(calls), judge=fake_judge(p=0.99),
                          may_continue=_open)
    assert report["status"] == "no_work", report
    assert not calls


def test_nothing_to_judge_wins_over_a_missing_judge(graph):
    """Граф из одних автостатусов на установке без NLI-модели — «судить нечего»,
    а не «судья отказал»: иначе ночь красилась бы в сбой там, где судить нечего
    при любом судье (n628-in1, DS C1)."""
    report = tier3.revise(graph, embedder=fake_embedder(),
                          judge=fake_judge(refused="нет модели"), may_continue=_open)
    assert report["status"] == "no_work", report


def test_written_essence_merges_at_the_common_bar(tmp_path, monkeypatch):
    """Формулировку темы писал человек — по ней 0.85 уже основание слить."""
    graph = _pair(tmp_path, monkeypatch, CORE_C, CORE_D, 0.85)
    assert tier3.revise(graph, apply=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)["log"], \
        "написанная руками суть не должна судиться строже автостатуса"


def test_apply_advice_appears_only_when_apply_would_do_something(graph_with_essence):
    """«свести — scripts/tier3_cores.py --apply» печатается по этому полю.

    Совет, который на данных пользователя ничего не делает, хуже молчания:
    человек его выполняет, ничего не происходит, доверие к автомату уходит.
    """
    graph = graph_with_essence
    assert tier3.revise(graph, mark=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)["pending_merges"], \
        "уверенная пара осталась несведённой, а поле пустое"
    assert not tier3.revise(graph, apply=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)["pending_merges"], \
        "пара слита — советовать --apply больше нечего"


def test_merging_clears_the_stale_manual_merge_request(graph_with_essence):
    """Пометка «свести вручную» отработала — она не должна пережить слияние."""
    graph = graph_with_essence
    tier3.revise(graph, mark=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)
    tier3.revise(graph, apply=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)
    canon = [t for t in _snapshot(graph).values() if "Дубль. Смерджен" not in t]
    assert canon, "слияния не было — тест ничего не проверяет"
    assert "возможный дубль" not in canon[0], \
        "morning_brief будет вечно просить свести то, что уже сведено"


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


# ─── Право на слияние: у КАЖДОГО пути к revise, не только у дневного ─────────

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_nightly_does_not_take_apply_by_itself():
    """Ночная джоба не имеет права решать за пользователя.

    tier3_auto_apply: false обязан выключать слияние и у ночного контура.
    А nightly.sh зовёт tier3_cores с захардкоженным --apply: каждый запуск
    launchd необратимо сливает ядра во всех графах vault, включая личные, —
    у пользователя, который никакого apply не давал. Дневной путь
    (graph_updater после встречи) этим же пакетом привязан к конфигу, и
    модель «слияние — решение человека» держится ровно до 04:15.
    """
    text = (ROOT / "scripts" / "nightly.sh").read_text(encoding="utf-8")
    assert "--apply" not in text, \
        "nightly.sh сливает безусловно (--apply захардкожен) — право должно браться из конфига"
    assert "--auto" in text, \
        "ночной режим обязан спрашивать конфиг: --auto (слияние только при tier3_auto_apply: true)"


def test_junk_in_auto_apply_is_not_permission():
    """Строгий is True — та же политика, что у облачных тумблеров privacy."""
    assert hasattr(tier3, "auto_apply_allowed"), \
        "нет единой точки решения tier3.auto_apply_allowed(cfg)"
    for junk in (None, "", 0, 1, "false", "true", "да", []):
        assert tier3.auto_apply_allowed({"sufler": {"tier3_auto_apply": junk}}) is False, \
            f"мусор в конфиге стал разрешением: {junk!r}"
    assert tier3.auto_apply_allowed({"sufler": {"tier3_auto_apply": True}}) is True
    assert tier3.auto_apply_allowed({"sufler": {}}) is False
    assert tier3.auto_apply_allowed({}) is False


def test_stale_core_is_skipped_instead_of_overwritten(graph_with_essence, monkeypatch):
    """Ядро, изменившееся во время прогона, не затирается снимком из памяти.

    Суд пар идёт часами, а пишем мы текст, прочитанный в начале. Правка
    конвейера встречи или человека за это время исчезала молча — копия
    оставалась только в бэкапе (аудит ночи 26.08, DS Important 3).
    """
    real_load = tier3.load_cores

    def load_and_touch(f):
        cores = real_load(f)
        for c in cores:                      # «кто-то» переписал файл после чтения
            c["path"].write_text(c["path"].read_text(encoding="utf-8")
                                 + "\n- [[2026-08-26_1200]] свежая строка\n",
                                 encoding="utf-8")
        return cores

    monkeypatch.setattr(tier3, "load_cores", load_and_touch)
    report = tier3.revise(graph_with_essence, apply=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)
    texts = _snapshot(graph_with_essence)
    # правка «чужой руки» на месте, слияния не случилось
    assert all("свежая строка" in t for t in texts.values()), texts
    assert not any("Дубль. Смерджен" in t for t in texts.values()), texts
    assert any("изменился во время прогона" in line for line in report["log"]), report["log"]


def test_handwritten_essence_of_a_duplicate_survives_the_merge(graph_with_essence):
    """Слияние не теряет рукописную «Суть» дубля: она переезжает в канон."""
    tier3.revise(graph_with_essence, apply=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)
    texts = _snapshot(graph_with_essence)
    canon = [t for t in texts.values() if "Дубль. Смерджен" not in t]
    assert any("Суть дубля" in t for t in canon), canon


def test_merge_carries_the_duplicate_name_and_aliases_into_the_canon(graph_with_essence):
    """После слияния имя дубля и его псевдонимы живут в шапке канона: иначе
    следующее упоминание короткого псевдонима заводит дубль заново (GLM,
    круг-1 #451); заглушка псевдонимов не несёт."""
    from charoite_graph import frontmatter
    graph = graph_with_essence
    for p in sorted(graph.glob("Ядра/*.md")):
        text = frontmatter.with_aliases(p.read_text(encoding="utf-8"), [f"псевдоним {p.stem}"])
        p.write_text(frontmatter.with_list_field(text, frontmatter.AUTO_ALIASES, [f"след {p.stem}"]), encoding="utf-8")
    assert tier3.revise(graph, apply=True, embedder=fake_embedder(), judge=fake_judge(p=_P["p"]), may_continue=_open)["log"]
    texts = {p: p.read_text(encoding="utf-8") for p in graph.glob("Ядра/*.md")}
    stubs = {p: t for p, t in texts.items() if "Дубль. Смерджен" in t}
    assert stubs, "слияния не было"
    for stub_path, stub_text in stubs.items():
        m = re.search(r"→ \[\[Ядра/([^\]|]+)", stub_text)
        canon = graph / "Ядра" / f"{m.group(1)}.md"
        got = frontmatter.aliases(texts[canon])
        assert stub_path.stem in got and f"псевдоним {stub_path.stem}" in got, got
        # след склейки машины едет в канон вместе с псевдонимами (№286, DS I2 по #576)
        assert f"след {stub_path.stem}" in frontmatter.list_field(texts[canon], frontmatter.AUTO_ALIASES)
        assert frontmatter.aliases(stub_text) == [], "заглушка псевдонимов не несёт"
        body = frontmatter.split(texts[canon])[1]
        assert "## Статус" in body and "## Хроника" in body, "шапка переписана, тело цело"
        assert texts[canon].count("[[Встречи/") >= 2, "хроника обеих встреч на месте"

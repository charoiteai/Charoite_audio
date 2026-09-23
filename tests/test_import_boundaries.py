"""Сторож границ слоёв и точек входа (№320, фаза 0 разбиения на пакеты).

Архитектурный круг 19.09 (DS и GLM): слои в `src/` есть по факту импортов, но
держались памятью автора и разовым замером; при движении файлов рвутся не
только импорты, а две другие связи — кто кого запускает по пути и где файл
лежит. Один источник истины — `docs/design/layout.json`; этот тест сверяет его
с реальностью как равенство множеств в обе стороны по каждой сущности (круг 2
по #594: одностороннее включение либо держит лишнее, либо молчит о потерянном):
слои, рёбра против стрелок, точки входа (исполняемые файлы), названные пути
(код и проза), свежесть карты. Тот же класс, что `test_cloud_call_sites`:
инвариант структурный, проверяется разбором кода, не запуском.

Круг 1 по #594: связи собирались регексом по прозе — фантомы из докстрингов и
комментариев, `nightly.sh` и CI вне инвентаря, дубль слоя легализовал ребро.
Круг 2: одно множество на две сущности — библиотеки из подсказок попали в
точки входа, `make_app.sh` вне целей, `__main__` подстрокой, скрытые каталоги
вне самопроверки, числа в ARCHITECTURE снова разошлись.
Круг 3: файлы читались там, где о них вспомнили — три обхода, три политики на
один `SyntaxError` (крах, пропуск, строка), битый JSON артефакта трейсбеком,
неоднозначное голое имя терялось молча, точка конца предложения прятала путь.
Теперь один инвентарь (`inventory`) и одна таблица видов (`KINDS`).
Круг 4: две таблицы об одном объекте не сверены (корневой `x.sh` — цель по
ENTRY_TARGETS и `out` по KINDS), `--regen` писал артефакт с пустой карточкой,
который загрузка отвергает. Теперь цель — всегда код, а сверка таблиц и круг
запись → чтение проверяются здесь над корпусом, не примерами.
Круг 5: корпусная сверка держала свою копию сопоставления префиксов и не
видела правило-тень. Теперь один решатель `decide()` — таблица, отсечение
каталогов, карта и этот тест читают его решение; конфликт «кандидат против
правила» — красная строка гейта, не тихий приоритет.
Круг 6: таблица проверялась только против себя самой — два правила выпали
при переписывании, суффиксный фолбэк дал правдоподобный вид, всё осталось
зелёным. Теперь таблица — утверждённые данные (копия с порядком здесь),
живость правила измеряется удалением, отсечение при обходе слабее решателя.
Круг 7 (две головы): живость мерялась только у правил области `git`, а
опечатка в префиксе правила обхода проходила зелёной; тест, закрывавший
недостижимую инструкцию про карту, не падал при откате фикса. Теперь живость
меряется пробой вида у каждого правила, снимок накрывает обе константы
политики, а тесты на закрытие проверены мутацией.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import typing

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import layout_map as lm  # noqa: E402


@pytest.fixture(scope="module")
def world():
    """Один инвентарь на модуль тестов — как обещает шапка сторожа."""
    inv = lm.inventory()
    return lm.load_layout(), lm.import_graph(inv), lm.scan(inv), lm.executables(inv), inv


def test_layout_matches_the_code(world):
    """Один гейт: расхождений между раскладкой и кодом нет. Каждая строка —
    готовое действие."""
    layout, graph, scanned, execs, inv = world
    problems = lm.check(layout, graph, scanned, execs, map_text=lm.MAP.read_text(encoding="utf-8"),
                        roots=lm.root_derivations(inv))
    assert not problems, "\n".join(problems)


def test_the_root_is_derived_by_one_module_in_every_shape(world):
    """Корень выводит один модуль — канон, и это счёт по ВСЕМ формам вывода.

    Первая редакция правила считала только чтение переменной, и этого хватало
    ровно до первой проверки: дефект, ради которого правило заводилось (модели
    искались от положения файла), чтения переменной не содержал вовсе — гейт
    оставался зелёным при возврате дефекта. Поэтому форм теперь таблица, и тест
    проверяет каждую, а не «ту, о которой вспомнили» (Critical DS выходного
    круга, воспроизведено возвратом прежней строки)."""
    layout, graph, scanned, execs, inv = world
    derivations = lm.root_derivations(inv)
    exempt = layout["root_exemptions"]

    assert {name for name, _, _ in lm.ROOT_SHAPES} == {"env", "file", "snapshot"}, (
        "формы вывода корня — утверждённый список; новая форма это правка политики, "
        "которую обязан прочитать ревьюер")
    assert lm.ENV_ROOT_OWNER in derivations, (
        "канон обязан выводить корень сам — иначе правило сторожит пустоту")

    enforced = {(rel, shape) for rel, shapes in derivations.items()
                if rel.startswith(lm.ENV_ROOT_ENFORCED) and rel != lm.ENV_ROOT_OWNER
                for shape in shapes}
    declared = {(rel, shape) for rel, shapes in exempt.items() for shape in shapes}
    assert enforced == declared, (
        f"в области {lm.ENV_ROOT_ENFORCED} корень выводит канон и объявленные ФОРМЫ "
        f"исключений, больше никто: лишние {sorted(enforced - declared)}, "
        f"исчезнувшие {sorted(declared - enforced)}")
    assert all(why for shapes in exempt.values() for why in shapes.values()), \
        "у исключения обязано быть непустое обоснование на каждую форму"

    # каждая форма обязана иметь ЖИВОЙ пример в боевом коде: матчёр, не находящий
    # ничего, проходит и пин таблицы — правило тогда сторожит пустоту, и об этом
    # не узнает ни один тест (GLM круга 2, вопрос 3). Исключение — форма, которая
    # закрыта синтетической пробой и названа в `probe_covered` ПО ИМЕНИ: №338
    # перевёл последние снапшоты на вопрос корня по вызову, у формы `snapshot`
    # живых примеров не осталось, и это итог задачи, а не регрессия. Пробой
    # ниже матчёр пинен с обеих сторон (находит снимок, не ловит вызов), поэтому
    # пустотой она не считается; список — только дополнение, молчаливо расширить
    # его нельзя: лишнее имя в множестве покрасит равенство.
    probe_covered = {"snapshot"}
    found = {shape for shapes in derivations.values() for shape in shapes}
    assert found | probe_covered == {name for name, _, _ in lm.ROOT_SHAPES}, (
        f"форма без живого примера и без синтетической пробы сторожит пустоту: "
        f"{sorted({n for n, _, _ in lm.ROOT_SHAPES} - found - probe_covered)}")

    # синтетическая проба формы snapshot — по образцу пробы формы file ниже
    # (`test_the_file_shape_catches_every_way_of_climbing_up`): живых примеров
    # у формы не осталось, и матчёр, переставший находить что-либо, обязан
    # ловиться здесь, а не молчаливым нулём в замере
    probe_src = "\n".join([
        "from charoite_paths import resolve_root",
        "",
        "SNAP_ON_IMPORT = resolve_root(__file__)",   # строка 3: снимок на импорте
        "",
        "",
        "def asks_on_call():",
        "    return resolve_root(__file__)",         # строка 7: вопрос НА ВЫЗОВЕ
    ])
    snapshot_hits = set(lm._root_snapshots(ast.parse(probe_src)))
    assert 3 in snapshot_hits, (
        f"матчёр снимков перестал находить снапшот на импорте: {snapshot_hits}")
    assert snapshot_hits == {3}, (
        "вопрос корня на вызове — рецепт правила, а не нарушение; "
        f"матчёр ловит лишнее: {sorted(snapshot_hits - {3})}")

    # долг закрыт (№338: 25 входов переведены на канон): в области правила
    # нарушителей НОЛЬ, кроме объявленных исключений. Прежняя строка требовала
    # «нарушителей больше десяти» и сторожила долг, пока он был жив, — после
    # перевода она писала бы ложь. Замер при этом ослепнуть не может: исключение
    # scripts/layout_map.py сверяется равенством выше в ОБЕ стороны — пропади
    # у замера скрипты, объявленная там форма «исчезнет» и гейт покраснет.
    assert not (enforced - declared), (
        f"в области {lm.ENV_ROOT_ENFORCED} нарушителей корня быть не должно — "
        f"все входы переведены на канон (№338), кроме объявленных исключений: "
        f"{sorted(enforced - declared)}")

    # мутация по КАЖДОЙ форме: нарушитель в области краснит гейт, канон и исключение — нет
    for shape, _, hint in lm.ROOT_SHAPES:
        probe = dict(derivations)
        probe["src/probe_derive.py"] = {shape: [7]}
        problems = lm.check(layout, graph, scanned, execs, roots=probe,
                            map_text=lm.MAP.read_text(encoding="utf-8"))
        assert any(p.startswith("src/probe_derive.py:7") and hint in p for p in problems), \
            f"форма {shape} в области обязана быть расхождением"
        assert not any(p.startswith(lm.ENV_ROOT_OWNER) for p in problems), \
            "сам канон выводит корень по определению, это не расхождение"
        assert not any(p.startswith(tuple(exempt)) for p in problems), \
            "объявленное исключение молчит, пока оно объявлено"
        assert not any(p.startswith("scripts/") for p in problems), \
            "красная строка называет пробу, а не чужой файл: scripts/ в области " \
            "правила с №338, и других нарушителей там нет"

    # обратная сторона: объявленное исключение, которое больше не выводит корень, — расхождение
    stale = lm.check(layout, graph, scanned, execs,
                     roots={k: v for k, v in derivations.items() if k not in exempt},
                     map_text=lm.MAP.read_text(encoding="utf-8"))
    assert any("больше не находит — снять" in p for p in stale), \
        "исключение сверяется в обе стороны, как всё в этом гейте"

    # прощение даётся на ФОРМУ, а не на файл: у прощённого файла появляется вторая
    # форма — она обязана краснеть. Без этой пробы откат к прощению файла целиком
    # проходит зелёным, потому что у сегодняшнего исключения форма всего одна
    # (проверено мутацией; обе головы круга 2 независимо)
    rel = sorted(exempt)[0]
    other = next(n for n, _, _ in lm.ROOT_SHAPES if n not in exempt[rel])
    widened = {**derivations, rel: {**derivations.get(rel, {}), other: [1]}}
    problems = lm.check(layout, graph, scanned, execs, roots=widened,
                        map_text=lm.MAP.read_text(encoding="utf-8"))
    assert any(p.startswith(f"{rel}:1") for p in problems), (
        f"{rel} прощён по форме {sorted(exempt[rel])}, а форма «{other}» у него новая — "
        f"прощение файла целиком молчало бы и о ней")

    # незаданный замер молчит обеими сторонами: раньше он печатал «исключение
    # больше не находится» про живое исключение — перегруженный None, за который
    # уже платили гейтом свежести карты (Important GLM круга 2, воспроизведено)
    silent = lm.check(layout, graph, scanned, execs, map_text=lm.MAP.read_text(encoding="utf-8"))
    assert not any("root_exemptions" in p or "взять корень у" in p for p in silent), \
        "без замера корней правило не судит вовсе — ни нарушителей, ни исключения"


def test_the_file_shape_catches_every_way_of_climbing_up(tmp_path):
    """Форма «модуль распоряжается своим положением» проверяется НАЗНАЧЕНИЕМ
    узла, а не написанием выражения вокруг него.

    Три редакции правила подряд пытались распознать подъём предикатом, и
    каждый следующий круг находил написание, которое предикат не видит: хелпер
    с параметром, обёртку над конструктором пути, промежуточную переменную,
    компонент `..`, цепочку длиннее бюджета предков. Предиката больше нет:
    `__file__` законен ровно в двух местах, и проба держит каждое написание из
    всех трёх кругов плюс оба законных случая, включая псевдоним канона и
    вызов по имени параметра.
    """
    probe = "\n".join([
        "# проба: все способы уйти вверх от своего файла",
        "import os",
        "import pathlib",
        "import sys",
        "from charoite_paths import resolve_root as root_of",
        "",
        "",
        "def _up(m):",
        "    return pathlib.Path(m).resolve().parent.parent",
        "",
        "",
        "def code_root(m):",                      # локальная тёзка канона, не импорт
        "    return pathlib.Path(m).resolve().parent.parent",
        "",
        "",
        "def Path(m):",                            # локальная тёзка конструктора пути
        "    return pathlib.Path(m).resolve().parent.parent",
        "",
        "",
        "CLIMB_HELPER = _up(__file__)",
        "CLIMB_INDEX = pathlib.Path(__file__).resolve().parents[1]",
        "CLIMB_DIRNAME = os.path.dirname(os.path.dirname(__file__))",
        "CLIMB_WRAPPED = _up(pathlib.Path(__file__))",
        "CLIMB_DOTDOT = pathlib.Path(__file__, '..', '..')",
        "CLIMB_VIA_VAR = pathlib.Path(__file__)",
        "CLIMB_LONG = pathlib.Path(__file__).resolve().absolute().expanduser().parent.parent",
        "CLIMB_SELF = pathlib.Path(__file__)",
        "CLIMB_INTO_CANON = root_of(pathlib.Path(__file__).parent.parent)",
        "CLIMB_TWO_STEPS = sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))",
        "CLIMB_SHADOW = code_root(__file__)",
        "CLIMB_DOTDOT_IN_BOOTSTRAP = sys.path.insert(0, str(pathlib.Path(__file__, '..', '..')))",
        "CLIMB_DOTDOT_IN_CANON = root_of(pathlib.Path(__file__, '..'))",
        "CLIMB_DOTDOT_DIVIDED = sys.path.insert(0, str(pathlib.Path(__file__).parent / '..'))",
        "CLIMB_DOTDOT_PACKED = sys.path.insert(0, str(pathlib.Path(__file__).parent / '../..'))",
        "CLIMB_DOTDOT_SLASH = sys.path.insert(0, str(pathlib.Path(__file__, '../')))",
        "CLIMB_DOTDOT_WITH_TAIL = sys.path.insert(0, str(pathlib.Path(__file__).parent / '../src'))",
        "CLIMB_ABSOLUTE = root_of(pathlib.Path(__file__, '/etc'))",
        "CLIMB_BYTES = sys.path.insert(0, str(pathlib.Path(__file__, b'..')))",
        "CLIMB_BARE_PATH = sys.path.insert(0, str(Path(__file__)))",
        "OK_CANON = root_of(__file__)",
        "OK_ALIAS = root_of(module_file=__file__)",
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))",
        "sys.path.insert(0, str(pathlib.Path(__file__).parent / 'src'))",
    ])
    lines = probe.splitlines()
    hits = set(lm._file_roots(ast.parse(probe)))
    climbing = {i for i, line in enumerate(lines, 1) if line.startswith("CLIMB_")}
    legit = {i for i, line in enumerate(lines, 1)
             if line.startswith(("OK_", "sys.path"))}
    assert climbing <= hits, (
        f"подъём вверх не пойман: {[lines[i-1] for i in sorted(climbing - hits)]}")
    assert not (legit & hits), (
        f"законное назначение объявлено нарушением: {[lines[i-1] for i in sorted(legit & hits)]}")
    # канон и вставка пути — не просто «не ловятся», а названы списком
    # законные получатели положения файла: спросить корень данных, спросить
    # корень кода и НАЗВАТЬ корень данных на входе — конструктором (№332) или
    # дверью с отказом кодом (№340)
    assert set(lm.ROOT_CANON_CALLS) == {"resolve_root", "code_root", "require_data_root",
                                       "name_data_root_or_exit"}
    assert set(lm.ROOT_BOOTSTRAP_CALLS) == {"sys.path.insert", "sys.path.append"}
    # имя канона без импорта каноном не делает: `ROOT_CANON_CALLS` — источник имён
    # для разбора импортов, а не список прощённых слов. Без этой пробы локальная
    # тёзка возвращала весь класс одним `def` (Critical обеих голов круга 4)
    assert any(l.startswith("def code_root(") for l in lines), \
        "проба обязана держать ЛОКАЛЬНУЮ функцию с точным именем канона"
    assert lm._canon_names(ast.parse("x = 1")) == set(), \
        "без импорта канона в модуле нет ни одного законного имени"


def test_the_snapshot_shape_catches_every_way_of_freezing_the_root():
    """Форма `snapshot` держит все написания пяти кругов — и свои границы.

    До этой пробы форму не пинил ни один тест: единственный живой экземпляр
    (`_CFG = _cfg()` в `mcp_server`) правка №329 убрала, гейт стал зелёным с
    нулём находок, и инверсия проверки прошла бы мимо CI — работоспособность
    держалась ручным замером брифа (круг 5 по коду №329, DS I2).

    Строки `FREEZE_` обязаны краснеть, `OK_` — нет. Граница названа списком, а
    не умолчанием: метод класса, вызванный на импорте, частичное применение и
    хелпер из чужого модуля правило пропускает СОЗНАТЕЛЬНО — их ловит
    свидетель поведения (`tests/test_backup_offload.py`), а не текст.
    """
    probe = "\n".join([
        "import functools",
        "from typing import TYPE_CHECKING",
        "import charoite_paths",
        "from charoite_paths import resolve_root",
        "from charoite_paths import resolve_root as корень",
        "from charoite_paths import code_root",
        "",
        "def _root():",
        "    return resolve_root(__file__)",
        "",
        "def _cfg():",
        "    return _root()",                     # хелпер через хелпера — транзитивно
        "",
        "def _ленивый():",
        "    return lambda: resolve_root(__file__)",
        "",
        "class Чужой:",
        "    def _root(self):",                   # тёзка модульного хелпера в классе
        "        return 'не корень'",
        "",
        "class Мой:",
        "    FREEZE_CLASS_FIELD = resolve_root(__file__)",
        "",
        "_алиас = resolve_root",
        "_частично = functools.partial(resolve_root)",
        "",
        "FREEZE_DIRECT = resolve_root(__file__)",
        "FREEZE_IMPORT_ALIAS = корень(__file__)",
        "OK_CODE_ROOT = code_root(__file__)",
        "FREEZE_DOTTED = charoite_paths.resolve_root(__file__)",
        "FREEZE_HELPER = _root()",
        "FREEZE_CHAIN = _cfg()",
        "FREEZE_ALIAS = _алиас(__file__)",
        "FREEZE_ANN: object = resolve_root(__file__)",
        "if (FREEZE_WALRUS := resolve_root(__file__)):",
        "    pass",
        "def _с_умолчанием(к=resolve_root(__file__)):",
        "    return к",
        "if False:",
        "    pass",
        "else:",
        "    FREEZE_DEAD_ELSE = resolve_root(__file__)",
        "if []:",
        "    OK_DEAD_LIST = resolve_root(__file__)",
        "if __name__ == '__main__':",
        "    OK_ENTRY_POINT = resolve_root(__file__)",
        "else:",
        "    FREEZE_ENTRY_ELSE = resolve_root(__file__)",
        "OK_LAMBDA = lambda: resolve_root(__file__)",           # noqa: E731 — в пробе это текст
        "OK_GENERATOR = (resolve_root(__file__) for _ in range(1))",
        "OK_FOREIGN_METHOD = Чужой()._root()",
        "OK_PARTIAL = _частично(__file__)",
        "OK_TYPING = TYPE_CHECKING",
    ])
    lines = probe.splitlines()
    hits = set(lm._root_snapshots(ast.parse(probe)))
    замораживают = {i for i, line in enumerate(lines, 1)
                    if line.lstrip().startswith(("FREEZE_", "if (FREEZE_", "def _с_умолчанием"))}
    законные = {i for i, line in enumerate(lines, 1) if line.lstrip().startswith("OK_")}
    assert замораживают <= hits, (
        f"снимок не пойман: {[lines[i-1] for i in sorted(замораживают - hits)]}")
    assert not (законные & hits), (
        f"ленивое или чужое объявлено снимком: {[lines[i-1] for i in sorted(законные & hits)]}")
    # границы — списком, а не умолчанием: иначе «пропускает» и «не умеет»
    # перестают различаться, и пропуск однажды примут за решение
    свои = lm._имена_корня(ast.parse(probe))
    assert свои == {"_root", "_cfg", "_алиас"}, свои
    # корень КОДА — не эта форма: код лежит там, где лежит, его снимок верен
    assert lm._имена_канона_данных(ast.parse(probe)) == {"resolve_root", "корень"}


def test_the_gate_is_actually_asked_about_the_roots(monkeypatch, capsys):
    """Правило живо только если главный тракт передаёт замер в гейт.

    Мутация здесь — не подмена кода, а подмена данных: канон объявляется
    нарушителем. Если `main` перестанет спрашивать замер, строка не появится,
    и правило умрёт молча — как умирал гейт свежести карты, пока состояние не
    сделали явным (круг 9)."""
    monkeypatch.setattr(lm, "ENV_ROOT_OWNER", "src/никто_такой_не_выводит.py")
    assert lm.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "взять корень у" in out, (
        "main обязан спросить замер о выводе корня и отдать его в check")


def test_layer_table_is_complete_and_the_arrows_point_down(world):
    layout, graph, _, _, _ = world
    assert set(layout["order"]) == set(layout["brief_layers"]) == set(layout["allowed"])
    assert not lm.unassigned(graph, layout) and not lm.stale_layers(graph, layout)
    lay = lm.layer_of(layout)
    for m, layer in lay.items():
        if layer in ("core", "cloud"):
            outside = {d for d in graph[m] if lay[d] != layer}
            assert not outside, f"{m} ({layer}) импортирует {sorted(outside)}"
        if layer == "graph":
            assert not {d for d in graph[m] if lay[d] == "meeting"}, f"{m} тянет meeting: {sorted(graph[m])}"
    # Фаза 3 закрыта: графовый слой не ходит в модели ни разрешением, ни долгом.
    # Утверждается ОТСУТСТВИЕ долга у слоя, а не его состав: пустое множество не
    # придётся править на каждом следующем куске, а возврат ребра красит тест
    # сразу. Остальной долг проверяется свойством — у каждой записи есть карточка
    # (круги 3 по №321 и 2 по куску 2б).
    assert "llm" not in layout["allowed"]["graph"]
    debt = {(a, b) for a, b in lm.allowlist_edges(layout) if lay.get(a) == "graph"}
    assert not debt, f"графовый слой снова в долгу перед моделями: {sorted(debt)}"
    for a, b in lm.allowlist_edges(layout):
        card = next((e.get("ticket", "") for e in layout["allowed_edges"]
                     if (e["from"], e["to"]) == (a, b)), "")
        assert lm.card_of(card), f"долг {a} → {b} без карточки: {card!r}"


def test_entry_points_are_executables_not_mentions(world):
    """Точки входа — исполняемые файлы: скрипты, `app/make_app.sh` (его зовёт
    релизный CI голым именем с working-directory), модули `src/` с гвардом.
    Библиотека, названная подсказкой, точкой входа не является (Critical DS и
    GLM круга 2). Путь в конце предложения документации виден (GLM круга 3)."""
    layout, _, scanned, execs, _ = world
    assert "app/make_app.sh" in execs and "scripts/nightly.sh" in execs and "src/daemon.py" in execs
    assert ".github/workflows/release-app.yml" in scanned.mentions["app/make_app.sh"]
    for lib in ("src/privacy.py", "src/graph_search.py", "src/llm_health.py"):
        assert lib not in execs and lib not in layout["manual_entry_points"]
        assert lib in scanned.mentions, f"{lib} назван подсказкой — путь обязан существовать, но точкой входа не быть"
    assert set(layout["manual_entry_points"]) <= set(execs)
    assert not set(layout["manual_entry_points"]) & set(scanned.mentions)
    assert "docs/ARCHITECTURE.md" in scanned.prose["src/llm.py"], "«src/llm.py.» в конце предложения"
    assert "replace.sh" in scanned.loose, "порождаемый скрипт обновления — голое имя без цели, не проблема"


#: Утверждённая таблица видов — копия KINDS без колонки why, с порядком (первый
#: совпавший префикс побеждает). Правка политики — правка в двух файлах, которую
#: ревьюер обязан прочитать (Critical DS круга 6: правила выпадали молча).
APPROVED_ENTRY_CANDIDATES = ("src/*.py", "scripts/*.py", "scripts/*.sh", "app/*.sh", "*.sh")

#: Грамматика замера — что он умеет распознавать. Расширение формы (новый способ
#: читать переменную, новый шов) — осознанная правка двух файлов, как у таблицы
#: видов (Important GLM круга 8: докстринг звучал как «все способы»).
APPROVED_ENV_READ_FORMS = ("os.environ.get", "os.getenv", "environ.get", "os.environ[...]", "environ[...]")
APPROVED_PROBE_SUFFIX = {"code": "probe.swift", "out": "probe.md", "history": "probe.md", "prose": "probe.dat"}
APPROVED_RUN_MODES = ("help", "refuse", "none")

#: Объявление полей артефакта: тип, класс (кто пишет) и поля записей. Новое поле
#: в `layout.json` без строки здесь невозможно — загрузка его отвергает, а
#: объявление без строки здесь краснит снимок (входной круг №325 по постановке).
APPROVED_FIELDS = {
    "generated": ("str", "measured", None),
    "order": ("list", "decision", None),
    "brief_layers": ("dict", "decision", None),
    "allowed": ("dict", "decision", None),
    "layer_overrides": ("dict", "decision", {"layer": ("str", "decision", True), "why": ("str", "decision", True)}),
    "allowed_edges": ("list", "measured", {"from": ("str", "measured", True), "to": ("str", "measured", True),
                                           "ticket": ("str", "decision", True)}),
    "manual_entry_points": ("dict", "decision", None),
    "root_exemptions": ("dict", "decision", None),
    "run_contracts": ("dict", "measured", {"mode": ("str", "seed", True), "why": ("str", "seed", True),
                                           "ticket": ("str", "decision", False)}),
}
APPROVED_PYTHON_AREAS = ("src/", "scripts/", "packages/")

APPROVED_KINDS = (
    ("docs/design/layout.md", "out", "git"),
    ("tests/", "out", "git"),
    ("app/Tests/", "out", "git"),
    ("app-ios/", "out", "git"),
    ("app-android/", "out", "git"),
    ("docs/reviews/", "history", "git"),
    ("devlog/_posts/", "history", "git"),
    ("CHANGELOG.md", "history", "git"),
    ("app/build/", "out", "walk"),
    ("app/.build/", "out", "walk"),
    (".build/", "out", "walk"),
    ("build/", "out", "walk"),
    (".venv/", "out", "walk"),
    ("node_modules/", "out", "walk"),
    (".git/", "out", "walk"),
    ("app/", "code", "git"),
    ("scripts/", "code", "git"),
    ("src/", "code", "git"),
    ("packages/", "code", "git"),
    (".github/", "code", "git"),
    (".pre-commit-config.yaml", "code", "git"),
)

#: Пути, вид которых держится на ПРАВИЛЕ: без своего правила каждый решался бы
#: иначе. Ожидание независимо от `decide` (Important DS круга 6: сверка решения
#: с самим собой не проверка).
RULE_DEPENDENT = {
    "app-ios/README.md": "out", "app-ios/project.yml": "out", "app-android/README.md": "out",
    "app-android/gradle/libs.versions.toml": "out", "app/Tests/X.swift": "out", "tests/README.md": "out",
    "docs/reviews/2026-07-30-x.md": "history", "CHANGELOG.md": "history", "devlog/_posts/2026-08-19-x.md": "history",
    "app/Sources/A.swift": "code", ".github/workflows/ci.yml": "code", ".pre-commit-config.yaml": "code",
    "docs/design/layout.md": "out",
}

#: Пути, вид которых даёт суффиксный фолбэк без всякого правила — он был злодеем
#: круга 5 (правдоподобный вид вместо выпавшей политики), поэтому прибит тоже
#: (Minor GLM круга 7: комментарий врал про четыре записи из семнадцати).
SUFFIX_PINNED = {
    "config/config.example.yaml": "prose", "README.md": "prose",
    "docs/design/ui.html": "out", "app/Package.resolved": "out",
}


def _decide_with(table, rel):
    saved = lm.KINDS
    lm.KINDS = table
    try:
        return lm.decide(rel)
    finally:
        lm.KINDS = saved


def test_the_tables_of_the_gate_agree_over_the_whole_tree(world, tmp_path, monkeypatch):
    """Таблица видов — утверждённые данные; её живость меряется над корпусом
    через единственный решатель `decide()`: правило области `git` меняет вид хотя
    бы одного файла под git при удалении; `insurance` накрывает файлы, но
    решает только через кандидатов; `walk` под git не накрывает ничего (критика
    DS круга 6: рукой поставленная область не выводит правило из-под гейта);
    кандидат — всегда код и никогда не в конфликте; что записал `--regen`, то
    читает `load_layout`. Отрицания: правило-тень перед кандидатом — конфликт в
    проблемах инвентаря; правило после более широкого — не накрывает ничего."""
    layout, graph, _, _, inv = world
    assert tuple(r[:3] for r in lm.KINDS) == APPROVED_KINDS, "таблица KINDS изменилась — обнови утверждённую копию осознанно"
    assert lm.ENTRY_CANDIDATES == APPROVED_ENTRY_CANDIDATES, "шаблоны кандидатов — та же политика, снимок обязателен"
    assert lm.ENV_READ_FORMS == APPROVED_ENV_READ_FORMS, "грамматика замера — политика, снимок обязателен"
    assert lm.PROBE_SUFFIX == APPROVED_PROBE_SUFFIX, "суффиксы проб — политика, снимок обязателен"
    assert lm.RUN_MODES == APPROVED_RUN_MODES, "режимы контрактов запуска — политика приёмки, снимок обязателен"
    объявление = {k: (f.typ.__name__, f.cls, None if f.record is None else
                      {n: (r.typ.__name__, r.cls, r.required) for n, r in f.record.items()})
                  for k, f in lm._SCHEMA.items()}
    assert объявление == APPROVED_FIELDS, "поля артефакта — политика: новое поле объявляется осознанно, двумя файлами"
    assert {f.cls for f in lm._SCHEMA.values()} | {r.cls for f in lm._SCHEMA.values() for r in (f.record or {}).values()} \
        <= set(lm.FIELD_CLASSES), "класс поля вне объявленных"
    assert lm.PYTHON_AREAS == APPROVED_PYTHON_AREAS, "области мутатора — политика, снимок обязателен"
    assert all(any(a.startswith(p) and k == "code" for p, k, _s, _w in lm.KINDS) for a in lm.PYTHON_AREAS), "область мутатора вне кода"
    # у каждого вида таблицы есть различимая проба, иначе правило нечем доказать
    for _p, kind, _s, _w in lm.KINDS:
        assert kind in lm.PROBE_SUFFIX, kind
    with pytest.raises(lm.LayoutError):
        lm.probe("x/", "невиданный вид")
    # состояние карты — закрытый список, а не любая строка
    with pytest.raises(lm.LayoutError):
        lm.check(layout, graph, lm.Scan({}, {}, {}, []), {}, repo=tmp_path, map_state="что-то")
    # состояний три, и «не спрашиваем о карте» — это map_text=None, а не четвёртое
    # значение, которое вело бы себя как present и врало докстрингом (GLM круга 9)
    # состояний ровно три: четвёртое вело бы себя как present, а докстринг обещал
    # обратное, и на этом держался главный гейт (Important GLM круга 9)
    assert lm.MAP_STATES == ("present", "missing", "skipped"), lm.MAP_STATES
    # кортеж и тип — одно и то же: иначе «один источник» куплен ценой типа (DS круга 12)
    assert typing.get_args(lm.MapState) == lm.MAP_STATES
    orders = typing.get_args(typing.get_type_hints(lm.ModuleEvents.order.fget)["return"])
    assert orders == ("ok", "read_before_insert", "insert_only_inside", "no_insert")
    # каждый исход обязан иметь фразу: иначе пятый молча не печатался бы (DS круга 13)
    assert set(lm.ORDER_NOTES) == set(orders)
    assert "unknown" not in lm.check.__doc__
    for rel, kind in RULE_DEPENDENT.items():
        assert lm.kind_of(rel) == kind, rel
        won = lm.decide(rel).rule
        assert won is not None, f"{rel} решён без правила — место ему в SUFFIX_PINNED"
        assert _decide_with(lm.KINDS[:won] + lm.KINDS[won + 1:], rel).kind != kind, \
            f"{rel} держится не на своём правиле — место ему в SUFFIX_PINNED"
    for rel, kind in SUFFIX_PINNED.items():
        assert lm.kind_of(rel) == kind, rel
    # каталоги кандидатов выводятся из шаблонов, и ни один не отсекается обходом
    assert lm.candidate_dirs() == {"", "app", "scripts", "src"}
    for pat in lm.ENTRY_CANDIDATES:
        parent = str(pathlib.PurePosixPath(pat).parent)
        assert not lm._pruned("" if parent == "." else parent), pat
    covered: dict[int, int] = {}
    decisions: dict[str, str] = {}
    for rel, info in inv.files.items():
        d = lm.decide(rel)
        assert d.kind == info.kind and d.conflict is None, f"{rel}: {d}"
        if lm._is_candidate(rel):
            assert d.by == "candidate" and d.kind == "code", f"{rel}: кандидат решён не как код ({d.by})"
        decisions[rel] = d.kind
        if d.rule is not None:
            covered[d.rule] = covered.get(d.rule, 0) + 1
    for i, (prefix, kind, scope, _why) in enumerate(lm.KINDS):
        without = lm.KINDS[:i] + lm.KINDS[i + 1:]
        # живость — проба своего вида: удаление правила обязано изменить её вид.
        # Корпус для этого не годится: правило обхода (`build/`, `.venv/`) не
        # накрывает под git ничего, и опечатка в префиксе прошла бы зелёной
        # (Important DS круга 7).
        pr = lm.probe(prefix, kind)
        assert _decide_with(without, pr).kind != lm.decide(pr).kind, \
            f"правило KINDS {prefix!r} ничего не решает: вид пробы {pr} без него тот же"
        # область — факт о корпусе: git накрывает файлы под git, walk не накрывает
        if scope == "git":
            assert covered.get(i, 0) > 0, f"правило {prefix!r} области git не накрывает ни одного файла под git"
        else:
            assert scope == "walk" and covered.get(i, 0) == 0, f"правило {prefix!r} области walk накрывает файлы под git"
    assert lm.decide("release.sh").by == "candidate", "корневой .sh — кандидат, значит код"
    # правило-тень перед кандидатом: решение остаётся «код», конфликт — строка проблемы, и в обходе без git тоже
    shadow = (("scripts/", "out", "git", "тень"),) + lm.KINDS
    monkeypatch.setattr(lm, "KINDS", shadow)
    d = lm.decide("scripts/get_models.py")
    assert d.kind == "code" and d.conflict == "scripts/"
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "get_models.py").write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    assert not lm._pruned("scripts"), "каталог кандидатов при обходе не отсекается, даже если правило зовёт его out"
    conflicts = [p for p in lm.inventory(tmp_path).problems if p.kind == "conflict"]
    assert conflicts and "кандидат в точки входа, но правило KINDS" in conflicts[0].text
    # мёртвое правило после более широкого — не накрывает никогда
    dead = lm.KINDS[1:] + (("scripts/never.py", "out", "git", "мёртвое"),)
    monkeypatch.setattr(lm, "KINDS", dead)
    assert lm.decide("scripts/never.py").rule != len(dead) - 1, "правило после более широкого не накрывает ничего"
    monkeypatch.undo()
    # отсечение слабее решателя и на глубине: тень над каталогом кандидатов не
    # прячет ни файл в нём, ни файл в его подкаталоге (Minor DS круга 7: петля по
    # корпусу была тождественно истинной)
    monkeypatch.setattr(lm, "KINDS", (("scripts/", "out", "git", "тень"),) + lm.KINDS)
    (tmp_path / "scripts" / "sub").mkdir()
    (tmp_path / "scripts" / "sub" / "deep.py").write_text("x = 1\n", encoding="utf-8")
    assert not lm._pruned("scripts"), "каталог кандидатов под тенью не отсекается"
    assert lm._pruned("scripts/sub"), "каталог без кандидатов отсекается законно — там нечего прятать"
    files = lm.inventory(tmp_path).files
    assert "scripts/get_models.py" in files, "кандидат виден, конфликт дойдёт до гейта"
    assert "scripts/sub/deep.py" not in files
    # свойство: отсечённый каталог не может содержать кандидата ни по одному шаблону
    for pat in lm.ENTRY_CANDIDATES:
        assert not lm._pruned(str(pathlib.PurePosixPath(pat).parent / "x").rsplit("/", 1)[0] or "")
    monkeypatch.undo()
    # круг запись → чтение
    regenerated, unticketed = lm.regen(json.loads(json.dumps(layout)), graph)
    assert unticketed == []
    out = tmp_path / "layout.json"
    out.write_text(json.dumps(regenerated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert lm.load_layout(out)["allowed_edges"] == layout["allowed_edges"]


def test_regen_rewrites_measured_fields_and_carries_the_rest(tmp_path):
    """Что `--regen` делает с полем, решает класс поля в объявлении: замер
    переписывается, решение переносится дословно, чужого поля нет вовсе — схема
    закрыта. Прежний тест требовал обратного: подкладывал в ребро `until` и
    `owner` и ждал, что реген их сохранит, — то есть утверждал открытость,
    которую загрузка теперь отвергает. Два противоположных правила были бы
    зелёными одновременно, потому что результат регена через загрузчик не
    проходил (входной круг №325 по постановке, Opus C1)."""
    layout = lm.load_layout(lm.LAYOUT)
    graph = lm.import_graph(lm.inventory())
    assert layout["allowed_edges"], "артефакт без рёбер — тесту нечего переносить"
    assert lm.MEASURED_EDGE_FIELDS == ("from", "to"), "замер владеет только парой модулей"
    edge = layout["allowed_edges"][0]
    решения = [n for n in lm._SCHEMA["allowed_edges"].record if n not in lm.MEASURED_EDGE_FIELDS]
    assert решения == ["ticket"], решения
    изменённый = {**edge, "ticket": edge["ticket"] + " — решение человека после ревью"}
    layout["allowed_edges"][0] = изменённый

    fresh, unticketed = lm.regen(json.loads(json.dumps(layout)), graph)
    kept = {(e["from"], e["to"]): e for e in fresh["allowed_edges"]}[(edge["from"], edge["to"])]
    assert unticketed == []
    assert kept == изменённый, "решение переносится дословно, замер своего не добавляет"
    # запись → чтение: что дал реген, то принимает загрузка
    lm.validate_layout(json.loads(json.dumps(fresh)))
    # чужое поле — отказ загрузки, а не молчаливый перенос
    layout["allowed_edges"][0] = {**edge, "until": "2026-10-31"}
    with pytest.raises(lm.LayoutError, match="не объявлено в _SCHEMA"):
        lm.validate_layout(json.loads(json.dumps(layout)))


def test_regen_writes_seed_fields_only_at_birth(tmp_path):
    """Класс seed: контракт запуска машина пишет один раз, при рождении записи
    (догадка по коду), а существующий не трогает — даже если догадка по коду
    сегодня дала бы другое. Иначе реген переписывал бы решение человека
    (Sonnet C1 входного круга №325 по постановке: «однократный замер,
    застывающий в решение»)."""
    (tmp_path / "src").mkdir()
    for имя in ("tool.py", "old.py"):
        (tmp_path / "src" / имя).write_text(
            "import argparse\nif __name__ == '__main__':\n    argparse.ArgumentParser().parse_args()\n",
            encoding="utf-8")
    inv = lm.inventory(tmp_path)
    layout = _layout(run_contracts={"src/old.py": {"mode": "none", "why": "решено руками", "ticket": "№0"}})
    layout["brief_layers"]["low"] += ["tool", "old"]
    filled, _ = lm.regen(json.loads(json.dumps(layout)), lm.import_graph(inv), inv, [])
    assert filled["run_contracts"]["src/old.py"] == {"mode": "none", "why": "решено руками", "ticket": "№0"}
    assert set(filled["run_contracts"]["src/tool.py"]) == set(lm.record_fields("run_contracts", "seed"))
    lm.validate_layout(filled)


def test_regen_refuses_to_write_an_artifact_the_loader_rejects(monkeypatch, tmp_path, capsys):
    """`--regen` с ребром без карточки не пишет ни артефакт, ни карту — гейт
    блокирующий, «напечатать и продолжить» не проверка (Critical DS круга 4);
    но отчёт о прочих расхождениях печатается тем же прогоном, а не после
    правки карточки (Important DS круга 5). `LAYOUT` подменяется целиком:
    и чтение, и запись идут в копию (Minor DS круга 5)."""
    lay = tmp_path / "layout.json"
    stale_map = tmp_path / "layout.md"
    stale_map.write_text("устаревшая карта\n", encoding="utf-8")
    data = json.loads(lm.LAYOUT.read_text(encoding="utf-8"))
    data["manual_entry_points"]["scripts/phantom_manual.py"] = "ручная точка, которой нет — второе расхождение"
    data["allowed_edges"].append({"from": "x_mod", "to": "y_mod", "ticket": "№0"})   # устаревшая запись allowlist
    lay.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = lay.read_text(encoding="utf-8")
    monkeypatch.setattr(lm, "LAYOUT", lay)
    monkeypatch.setattr(lm, "MAP", stale_map)

    def fake_regen(layout, graph, inv=None, notes=None):
        layout["allowed_edges"] = [e for e in layout["allowed_edges"] if e["from"] != "x_mod"]   # черновик «чинит» запись
        return layout, [("low_mod", "top_mod")]
    monkeypatch.setattr(lm, "regen", fake_regen)
    assert lm.main(["--regen"]) == 1
    assert lay.read_text(encoding="utf-8") == before, "артефакт с пустой карточкой не должен быть записан"
    assert stale_map.read_text(encoding="utf-8") == "устаревшая карта\n", "карта не переписана"
    out = capsys.readouterr().out
    assert "ребро low_mod → top_mod без карточки" in out
    assert "scripts/phantom_manual.py, но это не исполняемый файл" in out, "отчёт тем же прогоном, не после правки"
    assert "x_mod → y_mod, но такого ребра" in out, "отчёт по раскладке на диске, а не по несохранённому черновику (DS круга 6)"
    # мутация: карта на диске заведомо устаревшая, но при блокировке о ней не судят —
    # убери `not blocked` из main, и строка появится (обе головы круга 7)
    assert "отстал от кода" not in out, "карта не писалась — строка о её свежести недостижима"
    # без блокировки та же устаревшая карта краснеет, а пропавшая — тоже
    monkeypatch.setattr(lm, "regen", lambda layout, graph, inv=None, notes=None: (layout, []))
    lm.main(["--check"])
    assert "отстал от кода" in capsys.readouterr().out, "вне блокировки устаревшая карта — расхождение"
    stale_map.unlink()
    lm.main(["--check"])
    assert "нет — перегенерировать" in capsys.readouterr().out, "пропавшая карта — расхождение, а не зелёный гейт"


def test_regen_does_not_write_what_the_loader_would_reject(monkeypatch, tmp_path, capsys):
    """Реген, выдавший артефакт, который загрузка отвергнет, не пишет ни артефакт,
    ни карту — не только в случае ребра без карточки, а в любом: проверка та же,
    что у загрузки (входной круг №325 по постановке, Opus C1). Дельта долга по
    карточкам печатается тем же прогоном."""
    lay = tmp_path / "layout.json"
    карта = tmp_path / "layout.md"
    карта.write_text("карта до регена\n", encoding="utf-8")
    lay.write_text(lm.LAYOUT.read_text(encoding="utf-8"), encoding="utf-8")
    before = lay.read_text(encoding="utf-8")
    monkeypatch.setattr(lm, "LAYOUT", lay)
    monkeypatch.setattr(lm, "MAP", карта)

    def regen_with_a_stray_field(layout, graph, inv=None, notes=None):
        layout["allowed_edges"] = layout["allowed_edges"][1:]
        layout["allowed_edges"][0]["until"] = "2026-10-31"
        return layout, []
    monkeypatch.setattr(lm, "regen", regen_with_a_stray_field)
    assert lm.main(["--regen"]) == 1
    assert lay.read_text(encoding="utf-8") == before, "артефакт, который загрузка отвергнет, записан"
    assert карта.read_text(encoding="utf-8") == "карта до регена\n"
    out = capsys.readouterr().out
    assert "загрузка отвергает" in out and "until" in out
    assert "долг №322: было 3, стало 2" in out, out


def test_debt_is_derived_from_both_carriers_by_one_card_parser():
    """Долг по карточкам — производная из обоих носителей: ребро против стрелок и
    вход без пробы (`none`); карточка — поле `ticket`, разобранное одним
    `card_of`. Хвост после номера — пояснение, а не другая карточка."""
    layout = _layout(allowed_edges=[{"from": "low_mod", "to": "top_mod", "ticket": "№7 (пояснение, №9 — не в счёт)"},
                                    {"from": "core_mod", "to": "top_mod", "ticket": "№12"}],
                     run_contracts={"src/cli.py": {"mode": "none", "why": "нет пробы", "ticket": "№7"},
                                    "src/tool.py": {"mode": "help", "why": "по коду: argparse"}})
    долг = lm.debt_by_card(layout)
    assert list(долг) == ["№7", "№12"], "порядок — по номеру карточки, а не по строке"
    assert долг["№7"] == ["ребро `low_mod` → `top_mod`", "вход `src/cli.py` без пробы"]
    assert lm.debt_delta(layout, _layout(allowed_edges=layout["allowed_edges"][:1],
                                         run_contracts=layout["run_contracts"])) == ["долг №12: было 1, стало 0"]
    assert lm.debt_delta(layout, layout) == []
    # в реальном артефакте долг покрывает каждое ребро и каждый вход без пробы
    real = lm.load_layout()
    всего = sum(len(v) for v in lm.debt_by_card(real).values())
    none = sum(1 for c in real["run_contracts"].values() if c["mode"] == "none")
    assert всего == len(real["allowed_edges"]) + none
    assert "без карточки" not in lm.debt_by_card(real)


@pytest.mark.parametrize("ticket, card", [("№322", "№322"), ("№322 (LLM.complete — вызов)", "№322"),
                                          ("№364; пояснение", "№364"), ("№", None), ("№abc", None),
                                          ("№322abc", None), ("см. №322", None), ("", None), ("322", None)])
def test_one_card_format_for_the_loader_the_map_and_the_tests(ticket, card):
    assert lm.card_of(ticket) == card


def test_every_declared_field_is_enforced_by_the_loader(tmp_path):
    """Порча по объявлению, а не списком мутаций руками: для каждого поля из
    `_SCHEMA` — пропажа обязательного, чужой тип, пустая строка, чужое поле рядом
    — и загрузка обязана отказать. Новое поле попадает под проверку, как только
    его объявили. Граница честная: так ловится форма, а не правда — неверное
    обоснование или число в `why` порчей не поймать (Opus, ответ на вопрос 3)."""
    base = lm.load_layout()
    чужой_тип = {list: {}, dict: [], str: 0}

    def отказ(mutate, что):
        data = json.loads(json.dumps(base))
        mutate(data)
        with pytest.raises(lm.LayoutError):
            lm.validate_layout(data)
        return что

    проверено = []
    for key, f in lm._SCHEMA.items():
        проверено.append(отказ(lambda d, k=key: d.pop(k), f"нет {key}"))
        проверено.append(отказ(lambda d, k=key, t=f.typ: d.__setitem__(k, чужой_тип[t]), f"тип {key}"))
        if f.typ is str:
            проверено.append(отказ(lambda d, k=key: d.__setitem__(k, " "), f"пусто {key}"))
        if f.record is None:
            continue
        записи = base[key]
        assert записи, f"{key}: в артефакте нет записи — порчу поля записи не на чем проверить"
        ключ = next(iter(записи)) if isinstance(записи, dict) else 0

        def запись(d, k=key, i=ключ):
            return d[k][i]
        проверено.append(отказ(lambda d, z=запись: z(d).__setitem__("лишнее", "x"), f"чужое поле в {key}"))
        for имя, r in f.record.items():
            if имя not in записи[ключ]:
                continue
            if r.required:
                проверено.append(отказ(lambda d, z=запись, n=имя: z(d).pop(n), f"нет {key}.{имя}"))
            проверено.append(отказ(lambda d, z=запись, n=имя, t=r.typ: z(d).__setitem__(n, чужой_тип[t]),
                                   f"тип {key}.{имя}"))
            проверено.append(отказ(lambda d, z=запись, n=имя: z(d).__setitem__(n, ""), f"пусто {key}.{имя}"))
    проверено.append(отказ(lambda d: d.__setitem__("notes", {}), "чужой ключ верхнего уровня"))
    assert len(проверено) > 40, проверено


def test_the_artifact_is_loaded_strictly(tmp_path):
    """Инварианты артефакта — при загрузке, и только `LayoutError`: битый JSON,
    пропавший ключ, ключ не того типа, повтор слоя в order (Critical DS и Minor
    GLM круга 3); дубль модуля в двух слоях, стрелка вверх или в неизвестный
    слой, поправка без обоснования, ребро без карточки, ручная точка входа без
    обоснования или не по шаблону."""
    base = lm.load_layout()

    def write(mutate):
        data = json.loads(json.dumps(base))
        mutate(data)
        p = tmp_path / "layout.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def dup(d): d["brief_layers"]["app"].append(d["brief_layers"]["core"][0])
    def up(d): d["allowed"]["core"] = ["app"]
    def typo(d): d["allowed"]["core"] = ["ap"]
    def no_why(d): d["layer_overrides"]["tier3"] = {"layer": "graph", "why": ""}
    def no_ticket(d): d["allowed_edges"][0]["ticket"] = ""
    def bad_manual(d): d["manual_entry_points"]["docs/x.md"] = "почему-то"
    def empty_manual(d): d["manual_entry_points"]["scripts/x.py"] = ""
    def no_key(d): del d["manual_entry_points"]
    def wrong_type(d): d["allowed_edges"] = {}
    def dup_order(d): d["order"].append(d["order"][0])
    def edge_shape(d): d["allowed_edges"].append({"ticket": "№0"})
    def bad_stamp(d): d["generated"] = "x"
    def bad_mode(d): d["run_contracts"]["src/daemon.py"] = {"mode": "smoke", "why": "x"}
    def none_no_why(d): d["run_contracts"]["src/daemon.py"] = {"mode": "none", "why": "", "ticket": "№0"}
    def none_no_card(d): d["run_contracts"]["src/daemon.py"] = {"mode": "none", "why": "просто так"}
    def none_card_in_prose(d): d["run_contracts"]["src/daemon.py"] = {"mode": "none", "why": "долг №0"}
    def none_bad_card(d): d["run_contracts"]["src/daemon.py"] = {"mode": "none", "why": "x", "ticket": "см. №0"}
    def help_with_card(d): d["run_contracts"]["src/daemon.py"] = {"mode": "help", "why": "x", "ticket": "№0"}
    # карточка есть: краснеть обязан именно «+none»
    def none_plus(d): d["run_contracts"]["src/daemon.py"] = {"mode": "none+help", "why": "x", "ticket": "№0"}
    def contract_shape(d): d["run_contracts"]["src/daemon.py"] = "help"
    def contract_path(d): d["run_contracts"]["docs/x.md"] = {"mode": "help", "why": "x"}
    def edge_card(d): d["allowed_edges"][0]["ticket"] = "№"
    for bad in (dup, up, typo, no_why, no_ticket, bad_manual, empty_manual, no_key, wrong_type, dup_order, edge_shape,
                bad_stamp, bad_mode, none_no_why, none_no_card, none_card_in_prose, none_bad_card, help_with_card,
                none_plus, contract_shape, contract_path, edge_card):
        with pytest.raises(lm.LayoutError):
            lm.load_layout(write(bad))
    broken = tmp_path / "broken.json"
    broken.write_text("{\n  \"order\": [", encoding="utf-8")
    with pytest.raises(lm.LayoutError):
        lm.load_layout(broken)
    with pytest.raises(lm.LayoutError):
        lm.load_layout(tmp_path / "missing.json")
    assert lm.load_layout(write(lambda d: None))


def _layout(**over) -> dict:
    d = {"order": ["low", "high"], "allowed": {"low": [], "high": ["low"]},
         "brief_layers": {"low": ["core_mod", "low_mod"], "high": ["top_mod"]}, "layer_overrides": {},
         "allowed_edges": [], "manual_entry_points": {}, "root_exemptions": {},
         "generated": "2026-09-19T00:00Z", "run_contracts": {}}
    d.update(over)
    return d


def test_the_gate_sees_lazy_imports_and_new_upward_edges(tmp_path):
    """Отрицательные проверки на синтетическом дереве без git: ленивый импорт
    виден; новое ребро вверх / устаревшая запись allowlist / исполняемый без
    вызывающих и без manual / manual на библиотеку / manual при живом вызове /
    названный путь без файла / карта отстала — всё расхождения."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "core_mod.py").write_text("x = 1\n", encoding="utf-8")
    (src / "top_mod.py").write_text("import core_mod\n", encoding="utf-8")
    (src / "low_mod.py").write_text("def f():\n    import top_mod\n    return top_mod\n", encoding="utf-8")
    layout = _layout()
    graph = lm.import_graph(lm.inventory(tmp_path))
    assert graph["low_mod"] == {"top_mod"}, "ленивый импорт внутри функции обязан быть виден"
    assert lm.violations(graph, layout) == [("low_mod", "top_mod")]
    empty = lm.Scan({}, {}, {}, [])
    problems = lm.check(layout, graph, empty, {}, repo=tmp_path)
    assert any("новое ребро против стрелок: low_mod" in p for p in problems)
    layout["allowed_edges"] = [{"from": "low_mod", "to": "top_mod", "ticket": "№0"},
                               {"from": "core_mod", "to": "top_mod", "ticket": "№0"}]
    problems = lm.check(layout, graph, empty, {}, repo=tmp_path)
    assert any("core_mod → top_mod, но такого ребра" in p for p in problems)
    assert not any("новое ребро" in p for p in problems)
    layout["allowed_edges"] = layout["allowed_edges"][:1]
    # точки входа: гвард верхнего уровня — да; внутри функции (Minor DS круга 3) и
    # `!=` — защита от прямого запуска (Minor DS круга 4) — нет
    (src / "cli.py").write_text("if __name__ == '__main__':\n    pass\n", encoding="utf-8")
    (src / "nested.py").write_text("def main():\n    if __name__ == '__main__':\n        pass\n", encoding="utf-8")
    (src / "lib_only.py").write_text('if __name__ != "__main__":\n    pass\n', encoding="utf-8")
    inv = lm.inventory(tmp_path)
    graph = lm.import_graph(inv)
    layout["brief_layers"]["low"] += ["cli", "nested", "lib_only"]
    execs = lm.executables(inv)
    assert execs == {"src/cli.py": "python с гвардом __main__"}, "гвард в одинарных кавычках — по AST"
    problems = lm.check(layout, graph, empty, execs, repo=tmp_path)
    assert any("исполняемый файл src/cli.py никто не зовёт" in p for p in problems)
    layout["manual_entry_points"] = {"src/cli.py": "руками", "src/top_mod.py": "ошибка: библиотека"}
    problems = lm.check(layout, graph, empty, execs, repo=tmp_path)
    assert any("src/top_mod.py, но это не исполняемый файл" in p for p in problems)
    layout["manual_entry_points"] = {"src/cli.py": "руками"}
    # контракт запуска — на каждую точку входа и только на неё (входной круг №339)
    problems = lm.check(layout, graph, empty, execs, repo=tmp_path)
    assert any("src/cli.py без контракта запуска" in p for p in problems)
    layout["run_contracts"] = {"src/cli.py": {"mode": "none", "why": "тест", "ticket": "№0"},
                               "src/top_mod.py": {"mode": "help", "why": "тест"}}
    problems = lm.check(layout, graph, empty, execs, repo=tmp_path)
    assert any("run_contracts объявляет src/top_mod.py, но это не исполняемый файл" in p for p in problems)
    layout["run_contracts"] = {"src/cli.py": {"mode": "none", "why": "тест", "ticket": "№0"}}
    problems = lm.check(layout, graph, empty, execs, repo=tmp_path)
    assert problems == [] or all("ни одной пробы" in p for p in problems), problems
    layout["run_contracts"] = {"src/cli.py": {"mode": "help", "why": "тест"}}
    assert lm.check(layout, graph, empty, execs, repo=tmp_path) == []
    called = lm.Scan({"src/cli.py": {"app/X.swift"}, "src/gone.py": {"app/X.swift"}},
                     {"src/doc_gone.py": {"README.md"}}, {"deploy.sh": {"README.md"}}, [])
    problems = lm.check(layout, graph, called, execs, repo=tmp_path)
    assert any("src/cli.py объявлен ручным, но его зовёт код" in p for p in problems)
    assert any("путь src/gone.py назван в коде (app/X.swift), а файла нет" in p for p in problems)
    assert any("src/doc_gone.py назван в документации" in p for p in problems)
    assert not any("deploy.sh" in p for p in problems), "голое имя без цели — справка на карте, не гейт"
    layout["manual_entry_points"] = {}
    ok = lm.Scan({"src/cli.py": {"app/X.swift"}}, {}, {}, [])
    assert lm.check(layout, graph, ok, execs, repo=tmp_path) == []
    fresh = lm.render_map(layout, graph, ok, execs)
    assert lm.check(layout, graph, ok, execs, repo=tmp_path, map_text=fresh) == []
    assert any("отстал" in p for p in lm.check(layout, graph, ok, execs, repo=tmp_path, map_text=fresh + "x"))
    # regen: штамп не меняется, пока allowlist тот же
    same, unticketed = lm.regen(json.loads(json.dumps(layout)), graph)
    assert same["generated"] == "2026-09-19T00:00Z" and unticketed == []
    layout["allowed_edges"] = []
    changed, unticketed = lm.regen(json.loads(json.dumps(layout)), graph)
    assert changed["generated"] != "2026-09-19T00:00Z" and unticketed == [("low_mod", "top_mod")]
    # regen с инвентарём: новой точке входа — контракт по коду, решение человека — дословно,
    # исчезнувшей — снять
    layout["run_contracts"] = {"src/cli.py": {"mode": "none", "why": "решено руками", "ticket": "№0"},
                               "src/gone.py": {"mode": "help", "why": "тест"}}
    (src / "tool.py").write_text("import argparse\nif __name__ == '__main__':\n    argparse.ArgumentParser().parse_args()\n",
                                 encoding="utf-8")
    (src / "quiet.py").write_text("if __name__ == '__main__':\n    print(1)\n", encoding="utf-8")
    inv = lm.inventory(tmp_path)
    report: list[str] = []
    filled, _ = lm.regen(json.loads(json.dumps(layout)), lm.import_graph(inv), inv, report)
    assert filled["run_contracts"] == {"src/cli.py": {"mode": "none", "why": "решено руками", "ticket": "№0"},
                                       "src/tool.py": {"mode": "help", "why": "по коду: argparse"}}
    # реген говорит вслух, что сделал и чего делать не стал: none пишет только человек
    assert any("снят: src/gone.py" in r for r in report) and any("src/tool.py → help" in r for r in report)
    assert any("src/quiet.py: пробника по коду нет" in r for r in report), report


def test_one_inventory_one_policy_for_every_file(tmp_path):
    """Инвентарь — единственный источник фактов о файлах (Critical DS круга 3):
    битый python в `src/` — строка в problems, не трейсбек и не молчаливый
    пропуск; тот же файл без дерева не даёт ни рёбер, ни точки входа; вид
    файла решает таблица KINDS, а не ветка в сканере."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("import broken\n", encoding="utf-8")
    (tmp_path / "src" / "broken.py").write_text('if __name__ == "__main__":\n    def (:\n', encoding="utf-8")
    inv = lm.inventory(tmp_path)
    assert len(inv.problems) == 1 and inv.problems[0].kind == "parse"
    assert inv.problems[0].text.startswith("src/broken.py не разбирается")
    graph = lm.import_graph(inv)
    assert graph == {"ok": {"broken"}, "broken": set()}
    assert lm.executables(inv) == {}
    scanned = lm.scan(inv)
    assert scanned.problems == [p.text for p in inv.problems]
    layout = _layout(brief_layers={"low": ["ok", "broken"], "high": []})
    assert inv.problems[0].text in lm.check(layout, graph, scanned, {}, repo=tmp_path)
    # таблица видов: одно решение в одном месте
    assert lm.kind_of("app/Sources/A.swift") == "code"
    assert lm.kind_of("app/README.md") == "prose"
    assert lm.kind_of("app/Package.resolved") == "out"
    assert lm.kind_of("app-ios/Sources/A.swift") == "out"
    assert lm.kind_of("app-android/app/build.gradle.kts") == "out"
    assert lm.kind_of("tests/test_x.py") == "out"
    assert lm.kind_of("docs/reviews/2026-07-30-x.md") == "history"
    assert lm.kind_of("CHANGELOG.md") == "history" and lm.kind_of("devlog/_posts/2026-08-19-x.md") == "history"
    assert lm.kind_of("release.sh") == "code", "корневой .sh — цель ENTRY_TARGETS, значит код"
    assert lm.kind_of("docs/design/layout.md") == "out"
    assert lm.kind_of("docs/design/ui.html") == "out"
    assert lm.kind_of(".github/workflows/ci.yml") == "code"
    assert lm.kind_of("config/config.example.yaml") == "prose"
    assert lm.kind_of(".pre-commit-config.yaml") == "code"


def test_the_report_measures_seams_instead_of_the_author_remembering_them(tmp_path, capsys):
    """`--report` печатает факты для постановки фазы: кто читает переменную
    корня (и стоит ли чтение выше вставки в `sys.path` — тогда переход на модуль
    корней требует переноса строки), кто выводит корень из положения файла, кто
    зовёт шов. Три круга постановки фазы 3 дали Critical на расхождении ручного
    перечня с фактом — список работ обязан быть выводом замера."""
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src" / "lib.py").write_text(
        'import os, pathlib\n'
        'ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or pathlib.Path(__file__).resolve().parent.parent)\n'
        'HERE = pathlib.Path(__file__).resolve().parent\n'            # одна ступень — не корень
        'HELP = "по умолчанию CHAROITE_ROOT, как у демона"\n'          # строка в справке — не чтение
        'def f(cfg):\n    return LLM(cfg)\n', encoding="utf-8")
    (tmp_path / "scripts" / "early.py").write_text(
        'import os, sys, pathlib\n'
        'ROOT = os.getenv("CHAROITE_ROOT") or "."\n'
        'sys.path.insert(0, "src")\n'
        'import llm\n'
        'x = llm.LLM({})\n'
        'if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    (tmp_path / "scripts" / "late.py").write_text(
        'import os, sys\n'
        'sys.path.insert(0, "src")\n'
        'ROOT = os.environ["CHAROITE_ROOT"]\n'
        'if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    text = lm.report(lm.inventory(tmp_path))
    # заголовки секций замера идут из таблицы форм: имя формы и её фраза — те же,
    # по которым судит гейт (Important GLM круга 2)
    hint = {name: h for name, _, h in lm.ROOT_SHAPES}
    assert f"## Кто выводит корень сам, форма «env» — {hint['env']} (3)" in text
    assert "`scripts/early.py`:2; чтение в строке 2 ВЫШЕ вставки sys.path на импорте (3)" in text
    assert "`scripts/late.py`:3" in text and "ВЫШЕ вставки" not in text.split("late.py")[1].split("\n")[0]
    assert "`src/lib.py`:2" in text
    roots_block = text.split("форма «file»")[1].split("## Точки сборки")[0]
    assert roots_block.splitlines()[0].endswith("(1)"), roots_block.splitlines()[0]
    assert "`src/lib.py`:2" in roots_block and "early.py" not in roots_block
    assert "**LLM** (2)" in text, "шов считается и по голому имени, и по `llm.LLM`"
    assert "**GraphSearch** (0): нет" in text
    # справка argparse и одна ступень `.parent` — не факты
    assert "HELP" not in text and "`src/lib.py`:3" not in text


def test_the_report_never_hides_what_it_could_not_read(tmp_path, capsys):
    """Замер не молчит о непрочитанном: файл с ошибкой разбора — строка раздела
    «Не вошло в замер» и код выхода 1 (Critical DS и GLM круга 8, независимо:
    замер, построенный убрать расхождение перечня с фактом, сам был ему уязвим).
    Шапка считает то, что называет; `--report` не перебивает `--check`."""
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "ok.py").write_text('import os\nROOT = os.environ.get("CHAROITE_ROOT")\n', encoding="utf-8")
    (tmp_path / "scripts" / "broken.py").write_text('import os\nos.environ["CHAROITE_ROOT"]\ndef (:\n', encoding="utf-8")
    (tmp_path / "tests" / "test_x.py").write_text('import os\nos.getenv("CHAROITE_ROOT")\n', encoding="utf-8")
    inv = lm.inventory(tmp_path)
    text = lm.report(inv)
    assert "python-модулей в области 1" in text.splitlines()[0], text.splitlines()[0]
    assert "## Не прочитано — этих файлов в замере нет (1)" in text
    assert "scripts/broken.py не разбирается" in text
    assert "`src/ok.py`:2" in text
    assert "broken.py`:2" not in text, "битый файл не может числиться читателем — он не разобран"
    assert "tests/test_x.py" not in text, "тесты вне области замера по той же политике, что у гейта"
    # код выхода честный: замер на неполном корпусе — не замер
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    try:
        monkeypatch.setattr(lm, "REPO", tmp_path)
        assert lm.main(["--report"]) == 1, "непрочитанный файл обязан краснить код выхода"
        (tmp_path / "scripts" / "broken.py").write_text("x = 1\n", encoding="utf-8")
        assert lm.main(["--report"]) == 0
    finally:
        monkeypatch.undo()
    capsys.readouterr()


def test_the_report_reads_the_forms_it_declares(tmp_path):
    """Грамматика замера: `os.environ.get`, `os.getenv`, `os.environ[...]` и обе
    формы после `from os import environ` (Important GLM круга 8). Минимальная
    строка вставки в `sys.path`, а не первый узел обхода."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "forms.py").write_text(
        'import os\nfrom os import environ\n'
        'A = environ.get("CHAROITE_ROOT")\n'               # чтение раньше любой вставки
        'def _later():\n    sys.path.insert(0, "inside")\n'   # внутри функции: на импорте не срабатывает
        'sys.path.insert(0, "src")\n'                      # вот она делает src импортируемым
        'B = environ["CHAROITE_ROOT"]\n'
        'C = os.getenv("CHAROITE_ROOT")\n', encoding="utf-8")
    inv = lm.inventory(tmp_path)
    ev = lm.module_events(inv.files["scripts/forms.py"].tree, "CHAROITE_ROOT")
    assert ev.top_insert == 6 and ev.inner_insert == 5, \
        "вставка внутри функции на импорте не срабатывает — её строка не первая (Critical DS круга 9)"
    assert ev.top_reads == [3, 7, 8] and ev.inner_reads == []
    assert ev.order == "read_before_insert"
    text = lm.report(inv)
    assert "`scripts/forms.py`:3,7,8" in text, text
    assert "ВЫШЕ вставки sys.path на импорте (6)" in text
    # исход — значение, а не фраза: вставка только внутри функции и чтение при вызове
    # различаются классами, а не текстом (Critical DS круга 10, третий заход по месту)
    (tmp_path / "scripts" / "lazy.py").write_text(
        'import os, sys\n'
        'def main():\n    sys.path.insert(0, "src")\n    return os.environ.get("CHAROITE_ROOT")\n', encoding="utf-8")
    ev2 = lm.module_events(lm.inventory(tmp_path).files["scripts/lazy.py"].tree, "CHAROITE_ROOT")
    assert ev2.top_insert is None and ev2.inner_insert == 3 and ev2.inner_reads == [4]
    assert ev2.order == "insert_only_inside", "«вставки нет» было бы ложью — она есть, но не на импорте"
    out = lm.report(lm.inventory(tmp_path))
    assert "вставки sys.path на импорте нет, есть внутри функции (3)" in out
    # «вставка обязательна» спрашивается у вида файла: библиотека без гварда молчит,
    # исполняемый скрипт — нет (Important DS круга 12: критерий не держала ни одна проверка)
    (tmp_path / "scripts" / "helper.py").write_text('import os\nR = os.environ.get("CHAROITE_ROOT")\n',
                                                    encoding="utf-8")
    (tmp_path / "scripts" / "tool2.py").write_text(
        'import os\nR = os.environ.get("CHAROITE_ROOT")\nif __name__ == "__main__":\n    pass\n', encoding="utf-8")
    text2 = lm.report(lm.inventory(tmp_path))
    assert "`scripts/tool2.py`:2; вставки sys.path в файле нет" in text2
    assert "`scripts/helper.py`:2\n" in text2, "у не-исполняемого файла заметки о вставке нет"
    # вторая половина критерия: файл вне шаблонов кандидатов тоже молчит, даже с гвардом
    (tmp_path / "scripts" / "sub").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "sub" / "deep2.py").write_text(
        'import os\nR = os.environ.get("CHAROITE_ROOT")\nif __name__ == "__main__":\n    pass\n', encoding="utf-8")
    text3 = lm.report(lm.inventory(tmp_path))
    assert "deep2.py" not in text3 or "вставки sys.path в файле нет" not in text3.split("deep2.py")[1].split("\n")[0]
    assert "читается при вызове (строки 4), не на импорте" in out
    assert "`scripts/lazy.py`:4; вставки sys.path в файле нет" not in out


def test_scanner_reads_code_not_prose(tmp_path):
    """Swift/shell/yml — без комментариев; python — литералы по AST без
    докстрингов, путь как подстрока (подсказка человеку) и как склейка через
    «/»; голое имя `.sh` резолвится по имени своего скрипта (`./make_app.sh`
    из CI с working-directory), чужое голое имя — на карту, неоднозначное — в
    problems (Important DS и GLM круга 3), `.py` без каталога — нет; проза
    (md, toml) — только существование, точка конца предложения путь не прячет,
    голое имя без цели из прозы — на карту; код вне области (телефон), тесты
    и датированные снимки (ревью, CHANGELOG) не читаются; битый python — в
    problems."""
    (tmp_path / "app" / "Sources").mkdir(parents=True)
    (tmp_path / "app-ios" / "Sources").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs" / "reviews").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "app" / "Sources" / "S.swift").write_text(
        '/// см. src/comment.py\n// и src/comment2.py\n/* src/block.py */\n'
        'p.arguments = ["src/daemon.py"] // src/trail.py\nlet u = "https://x" + "src/after_url.py"\n'
        'let gen = "replace.sh"\n', encoding="utf-8")
    (tmp_path / "app-ios" / "Sources" / "T.swift").write_text('let p = "scripts/phone.py"\n', encoding="utf-8")
    (tmp_path / "app" / "make_app.sh").write_text("# scripts/hidden.sh\n$PY scripts/get_models.py\n", encoding="utf-8")
    (tmp_path / "scripts" / "n.sh").write_text('python src/dossier.py && echo "scripts/echoed.py" # scripts/x.py.bak\n'
                                                "$ROOT/scripts/nightly.sh\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "run: python scripts/check.py  # scripts/no.py\nrun: ./make_app.sh\nrun: ./foreign.sh\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text(
        '"""Докстринг: File "scripts/phantom.py", line 1."""\n'
        'run([sys.executable, str(CODE / "src" / "b.py")])\n'
        'subprocess.run(["scripts/memory_bench.py"])\n'
        'hint = f"запусти python3 scripts/doctor.py {flag}"\n'
        'note = "модуль audio.py не путь"\n'
        '# см. src/comment_only.py\n'
        'def f():\n    """scripts/inner_doc.py"""\n    return 1\n', encoding="utf-8")
    (tmp_path / "src" / "broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "tests" / "test_t.py").write_text('run(["scripts/get_models.py", "src/synthetic.py"])\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("Запуск: `scripts/setup.sh` или ./deploy.sh, см. src/lib.py. "
                                        "Не бэкап src/lib.py.bak\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## 1.0\n- src/released.py\n", encoding="utf-8")
    (tmp_path / "docs" / "reviews" / "2026-07-30-x.md").write_text("тогда был src/old.py\n", encoding="utf-8")
    (tmp_path / "scripts" / "get_models.py").write_text("x = 1\n", encoding="utf-8")
    inv = lm.inventory(tmp_path)
    scanned = lm.scan(inv)
    assert set(scanned.mentions) == {"src/daemon.py", "src/after_url.py", "scripts/get_models.py", "src/dossier.py",
                                     "scripts/echoed.py", "scripts/nightly.sh", "scripts/check.py", "app/make_app.sh",
                                     "src/b.py", "scripts/memory_bench.py", "scripts/doctor.py"}, sorted(scanned.mentions)
    assert scanned.mentions["src/daemon.py"] == {"app/Sources/S.swift"}
    assert scanned.mentions["app/make_app.sh"] == {".github/workflows/ci.yml"}
    for phantom in ("src/comment.py", "src/comment2.py", "src/block.py", "src/trail.py", "scripts/hidden.sh",
                    "scripts/x.py", "scripts/no.py", "scripts/phantom.py", "src/comment_only.py",
                    "scripts/inner_doc.py", "audio.py", "foreign.sh", "scripts/phone.py", "src/old.py",
                    "src/synthetic.py", "src/released.py"):
        assert phantom not in scanned.mentions and phantom not in scanned.prose, phantom
    assert scanned.prose == {"scripts/setup.sh": {"README.md"}, "src/lib.py": {"README.md"}}
    assert scanned.loose == {"replace.sh": {"app/Sources/S.swift"}, "foreign.sh": {".github/workflows/ci.yml"},
                             "deploy.sh": {"README.md"}}
    assert scanned.problems == [p.text for p in inv.problems] and "src/broken.py не разбирается" in scanned.problems[0]
    assert lm.executables(inv) == {"app/make_app.sh": "shell-скрипт", "scripts/n.sh": "shell-скрипт"}, \
        "scripts/get_models.py без гварда — хелпер, не точка входа (круг 5)"
    # второй скрипт с тем же именем: голое имя из CI — проблема, а не тихий выбор
    (tmp_path / "scripts" / "make_app.sh").write_text("x=1\n", encoding="utf-8")
    scanned = lm.scan(lm.inventory(tmp_path))
    assert "app/make_app.sh" not in scanned.mentions
    assert any("голое имя make_app.sh неоднозначно (app/make_app.sh, scripts/make_app.sh)" in p for p in scanned.problems)


def test_the_report_survives_a_broken_artifact_and_names_what_it_measures(tmp_path, monkeypatch, capsys):
    """Замер читает только код: битый артефакт его не хоронит (Critical GLM и
    Important DS круга 9 — в середине переделки артефакт правят руками). Спорный
    вид корпус не сокращает: такой файл в замере ЕСТЬ, у него свой раздел и код
    выхода 0. Python, до которого правила не дотянулись (`extras/…` — каталога
    нет в KINDS), не числится «вне области по политике»: о нём сказано отдельно.
    Пример сменился с `packages/` на `extras/` — у пакетов правило появилось
    вместе с гейтом, который их видит (№328)."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "extras").mkdir()
    (tmp_path / "docs" / "design").mkdir(parents=True)
    (tmp_path / "scripts" / "tool.py").write_text(
        'import os\nROOT = os.environ.get("CHAROITE_ROOT")\nif __name__ == "__main__":\n    pass\n', encoding="utf-8")
    (tmp_path / "extras" / "later.py").write_text(
        'import os\nROOT = os.environ.get("CHAROITE_ROOT")\n', encoding="utf-8")
    bad = tmp_path / "docs" / "design" / "layout.json"
    bad.write_text('{"order": ["a", "a"]}', encoding="utf-8")
    monkeypatch.setattr(lm, "REPO", tmp_path)
    monkeypatch.setattr(lm, "LAYOUT", bad)
    monkeypatch.setattr(lm, "MAP", tmp_path / "docs" / "design" / "layout.md")
    assert lm.main(["--report"]) == 0, "битый артефакт замеру не нужен и не должен его валить"
    out = capsys.readouterr().out
    assert "`scripts/tool.py`:2" in out
    assert "без правила 1" in out and "`extras/later.py`" in out, "файл вне правил назван, а не зачтён политике"
    # тот же прогон с --check платит за артефакт, как и раньше
    assert lm.main(["--check"]) == 1
    assert "layout.json" in capsys.readouterr().out
    # спорный вид: файл в замере есть, раздел свой, код выхода 0
    monkeypatch.setattr(lm, "KINDS", (("scripts/", "out", "git", "тень"),) + lm.KINDS)
    assert lm.main(["--report"]) == 0, "спорный вид корпус не сокращает"
    out = capsys.readouterr().out
    assert "## Спорный вид" in out and "`scripts/tool.py`:2" in out, "файл и спорный, и измеренный — оба факта видны"


#: Формы языка и уровень исполнения: что считается «на импорте», а что «при вызове».
#: Корпус рядом с классификатором — добавить форму, не объявив ожидание, нельзя
#: (Important DS и GLM круга 11: таблица типов операторов была обречена).
LEVEL_SHAPES = (
    ("x = os.environ.get('CHAROITE_ROOT')", "top"),
    ("if True:\n    x = os.environ.get('CHAROITE_ROOT')", "top"),
    ("try:\n    x = os.environ.get('CHAROITE_ROOT')\nexcept Exception:\n    pass", "top"),
    ("with open('f') as fh:\n    x = os.environ.get('CHAROITE_ROOT')", "top"),
    ("for i in []:\n    x = os.environ.get('CHAROITE_ROOT')", "top"),
    ("while False:\n    x = os.environ.get('CHAROITE_ROOT')", "top"),
    ("class C:\n    X = os.environ.get('CHAROITE_ROOT')", "top"),
    ("def f(d=os.environ.get('CHAROITE_ROOT')):\n    pass", "top"),
    ("@deco(os.environ.get('CHAROITE_ROOT'))\ndef f():\n    pass", "top"),
    ("def f() -> os.environ['CHAROITE_ROOT']:\n    pass", "top"),
    ("def f(a: os.environ['CHAROITE_ROOT']):\n    pass", "top"),
    ("def f(*args: os.environ['CHAROITE_ROOT']):\n    pass", "top"),
    ("def f(*, k: os.environ['CHAROITE_ROOT'] = 1):\n    pass", "top"),
    ("async def f(a: os.environ['CHAROITE_ROOT']):\n    pass", "top"),
    ("async def f():\n    x = os.environ.get('CHAROITE_ROOT')", "inner"),
    ("def f():\n    x = os.environ.get('CHAROITE_ROOT')", "inner"),
    ("class C:\n    def m(self):\n        x = os.environ.get('CHAROITE_ROOT')", "inner"),
    ("f = lambda: os.environ.get('CHAROITE_ROOT')", "inner"),
    ("def f(cb=lambda: os.environ.get('CHAROITE_ROOT')):\n    pass", "inner"),   # лямбда в заголовке
    ("def outer():\n    def inner(d=os.environ.get('CHAROITE_ROOT')):\n        pass", "inner"),
    ("g = (os.environ.get('CHAROITE_ROOT') for _ in [1])", "inner"),             # генератор ленив
    ("g = (x for x in os.environ.get('CHAROITE_ROOT'))", "top"),                 # источник первого for — сразу
    ("L = [os.environ.get('CHAROITE_ROOT') for _ in [1]]", "top"),               # включение вычисляется целиком
)


def test_the_level_of_a_node_is_measured_by_scope_not_by_statement_type():
    """Уровень исполнения — свойство узла, а не модульного оператора (Critical DS
    круга 11: всё под `class` объявлялось «при вызове», хотя тело класса,
    декораторы и умолчания исполняются на импорте). Корпус форм рядом с
    классификатором: забыть новую форму языка нельзя."""
    for src, level in LEVEL_SHAPES:
        ev = lm.module_events(ast.parse("import os\n" + src), "CHAROITE_ROOT")
        got = "top" if ev.top_reads else ("inner" if ev.inner_reads else "нет чтения")
        assert got == level, f"{src!r}: ждали {level}, получили {got}"
    # заголовок функции обходится целиком, а не поимённым списком: аннотации и
    # возвращаемый тип вычисляются на импорте (Critical DS круга 12)
    hdr = lm.module_events(ast.parse("import os\ndef f(a: os.environ['CHAROITE_ROOT']) -> None:\n    pass\n"),
                           "CHAROITE_ROOT")
    assert hdr.top_reads == [2] and hdr.inner_reads == []
    # вставка внутри функции — не на импорте, даже если функция объявлена выше
    ev = lm.module_events(ast.parse("import sys\ndef f():\n    sys.path.insert(0, 'x')\n"), "CHAROITE_ROOT")
    assert ev.top_insert is None and ev.inner_insert == 3 and ev.order == "insert_only_inside"
    # исход read_before_insert печатается без падения — словарь фраз вычислял все ветки
    ev2 = lm.module_events(ast.parse("import os, sys\nR = os.environ.get('CHAROITE_ROOT')\nsys.path.insert(0, 'x')\n"),
                           "CHAROITE_ROOT")
    assert ev2.order == "read_before_insert" and ev2.top_reads == [2]


#: Формы пути: роль, дистрибутив, пакет и имя модуля, которые им соответствуют.
#: Корпус рядом с `form`: после упаковки форм становится две, и забыть одну из
#: них — значит выключить охрану для половины продукта (входной круг №328).
#: Корпус обязан покрывать КАЖДУЮ ветку `form` — это проверяется трассировкой
#: ниже, потому что ветка без строки корпуса откатывается зелёной (круг 2 по
#: коду №328, DS M5; круг 3, DS I6 — пиннинга по списку ролей мало).
MODULE_SHAPES = (
    # путь,                                              роль,            дист,  пакет,            модуль
    ("src/graphs.py",                                    "module",        "",    "",               "graphs"),
    ("src/charoite_paths.py",                            "module",        "",    "",               "charoite_paths"),
    ("src/__init__.py",                                  "stray_init",    "",    "",               None),
    ("packages/cg/src/charoite_graph/graphs.py",         "module",        "cg",  "charoite_graph", "charoite_graph.graphs"),
    ("packages/cg/src/charoite_graph/__init__.py",       "package_init",  "cg",  "charoite_graph", "charoite_graph"),
    ("packages/cg/src/charoite_graph/inner/deep.py",     "module",        "cg",  "charoite_graph.inner", "charoite_graph.inner.deep"),
    ("packages/cg/src/charoite_graph/inner/__init__.py", "package_init",  "cg",  "charoite_graph.inner", "charoite_graph.inner"),
    ("packages/cg/src/__init__.py",                      "stray_init",    "cg",  "",               None),
    ("packages/cg/tests/test_x.py",                      "package_tests", "cg",  "",               None),
    ("packages/cg/tests/fixtures/data.json",             "package_tests", "cg",  "",               None),
    ("packages/cg/pyproject.toml",                       "outside",       "cg",  "",               None),
    ("packages/README.md",                               "outside",       "",    "",               None),
    # имя дистрибутива — имя пакета на PyPI: `form` режет путь по «/» и дефис
    # принимает, значит его обязана принимать и грамматика упоминаний (круг 5,
    # DS I3 = GLM I2) — одна строка пиннит обе
    ("packages/charoite-graph/src/charoite_graph/cli.py", "module",       "charoite-graph", "charoite_graph", "charoite_graph.cli"),
    ("packages/cg/src/charoite_graph/py.typed",          "outside",       "cg",  "",               None),
    ("scripts/doctor.py",                                "outside",       "",    "",               None),
    ("tests/test_x.py",                                  "outside",       "",    "",               None),
    ("src/inner/deep.py",                                "outside",       "",    "",               None),
    ("README.md",                                        "outside",       "",    "",               None),
)


def test_the_shape_of_the_layout_is_known_in_one_place():
    """Форму пути выводит `form`, и только он; `module_of` и `package_of` —
    его проекции.

    Раньше форма была записана литералом дважды — в `modules()` и в
    `import_graph()`, — то есть два независимых вывода об одном и том же.
    Переезд в `packages/` делал их несогласованными молча: имени нет,
    рёбер нет, гейт зелёный (входной круг №328, DS C3 = GLM 2).
    """
    for rel, role, dist, package, module in MODULE_SHAPES:
        f = lm.form(rel)
        assert (f.role, f.dist, f.package, f.module) == (role, dist, package, module), f"{rel}: получили {f}"
        # проекции обязаны отвечать то же самое, иначе потребители разойдутся
        assert lm.package_of(rel) == package, f"{rel}: package_of разошёлся с формой"
        ждём = module if lm.decide(rel).kind == "code" else None
        assert lm.module_of(rel) == ждём, f"{rel}: module_of разошёлся с формой"


def test_every_line_of_the_shape_is_reached_by_the_corpus():
    """Корпус достаёт КАЖДУЮ строку `form` — измерено трассировкой.

    Имя честное: трассировка меряет строки, а не ветки. Новое условие на уже
    покрытой строке она не увидит — его держат равенства корпуса
    (`MODULE_SHAPES`), и это разделение названо здесь, а не подразумевается
    (круг 5 по коду №328, DS I4).

    Пиннинг по ролям («каждая роль названа в корпусе») пропускал новую ветку,
    возвращающую уже существующую роль: её поведение не держал никто, и откат
    оставлял прогон зелёным (круг 3 по коду №328, DS I6). Трассировка
    отвечает на тот же вопрос структурно: строка кода без строки корпуса —
    красное.
    """
    исходник = (ROOT / "scripts" / "layout_map.py").read_text(encoding="utf-8")
    свой = next(n for n in ast.walk(ast.parse(исходник))
                if isinstance(n, ast.FunctionDef) and n.name == lm.SHAPE_OWNER)
    # Заголовок и докстринг попадают в co_lines, но `line`-событий не дают.
    # Порог не вычисляется по позиции в AST: «body[1] — первый оператор» верно,
    # только пока докстринг на месте, и его снятие молча сужало проверяемое
    # множество (круг 4 по коду №328, DS I4 = GLM I2, обе головы независимо).
    док = свой.body[0] if (isinstance(свой.body[0], ast.Expr)
                           and isinstance(свой.body[0].value, ast.Constant)) else None
    первая = (док.end_lineno or док.lineno) + 1 if док is not None else свой.body[0].lineno

    def код_и_потомки(code):
        yield code
        for c in code.co_consts:                      # лямбды и вложенные функции живут
            if hasattr(c, "co_lines"):                # в своих code object'ах, и их строк
                yield from код_и_потомки(c)           # в co_lines родителя нет
    объекты = list(код_и_потомки(lm.form.__code__))
    строки_кода = {n for code in объекты for _s, _e, n in code.co_lines()
                   if n is not None and n >= первая}
    пройдены: set[int] = set()
    наши = {id(c) for c in объекты}

    def трасса(frame, event, arg):
        if id(frame.f_code) in наши:
            if event == "line":
                пройдены.add(frame.f_lineno)
            return трасса
        return None

    прежний = sys.gettrace()
    sys.settrace(трасса)
    try:
        for rel, *_ in MODULE_SHAPES:
            lm.form(rel)
    finally:
        sys.settrace(прежний)
    assert пройдены, "предпосылка: трассировка работает (иначе тест ничего не проверяет)"
    непокрытые = строки_кода - пройдены
    assert not непокрытые, (f"строки `form` без пути в корпусе: {sorted(непокрытые)} "
                            f"в {lm.__file__} — добавить путь такой формы в MODULE_SHAPES")
    # и в обратную сторону: строка, которую трассировка ИСПОЛНЯЕТ, а порог
    # выбросил, делала бы проверку тише, а не громче (круг 5, DS I4)
    assert not (пройдены - строки_кода), f"порог съел исполняемый код: {sorted(пройдены - строки_кода)}"


def test_a_flat_module_cannot_shadow_a_packaged_one(tmp_path):
    """Плоский модуль и пакет с тем же корнем рядом не ставятся.

    `src/a.py` и `packages/x/src/a/b.py` дают РАЗНЫЕ имена (`a` и `a.b`),
    поэтому проверка точного совпадения молчит; общий корень `a` в рантайме
    достаётся тому, кто раньше в `sys.path`. Проверка корня знала только
    сторону дистрибутивов и плоскую половину выбрасывала — ровно то дерево,
    которое бывает в середине переезда №323 (круг 3 по коду №328, DS C1).
    """
    беды = lm.packaging_conflicts({"src/a.py": "a", "packages/x/src/a/b.py": "a.b"})
    assert [b.kind for b in беды] == ["collision"], беды
    assert "src/" in беды[0].text and "x" in беды[0].text
    # точное совпадение имён не должно печататься дважды — одно событие, одна строка
    пара = lm.packaging_conflicts({"packages/x/src/a/b.py": "a.b", "packages/y/src/a/b.py": "a.b"})
    assert len(пара) == 1 and "имя модуля a.b" in пара[0].text
    # но ТРЕТЬЯ сторона на том же корне — другое событие, и гашение по корню его
    # прятало навсегда (круг 4 по коду №328, DS C1)
    трое = lm.packaging_conflicts({"src/a.py": "a", "packages/x/src/a/b.py": "a.b",
                                   "packages/y/src/a/b.py": "a.b"})
    корневые = [b for b in трое if "импортируемый корень a" in b.text]
    assert len(корневые) == 1 and "src/" in корневые[0].text, трое


def test_an_entry_point_survives_the_move_into_a_package(tmp_path):
    """Точка входа, уехавшая в пакет, остаётся кандидатом и объявляемой вручную.

    Кандидатность задавали только глобы (`src/*.py`), а модуль дистрибутива
    лежит на любой глубине: после `git mv` файл с гвардом `__main__` выпадал
    из точек входа молча, и объявить его ручным было НЕЛЬЗЯ — `load_layout`
    отказывал «не путь к исполняемому файлу» (круг 3 по коду №328, GLM I2).
    """
    упакованный = "packages/cg/src/charoite_graph/cli.py"
    assert lm._is_candidate(упакованный) and lm._is_candidate("src/daemon.py")
    assert not lm._is_candidate("packages/cg/tests/test_x.py")
    assert not lm._is_candidate("packages/cg/pyproject.toml")
    (tmp_path / "packages" / "cg" / "src" / "charoite_graph").mkdir(parents=True)
    (tmp_path / "packages" / "cg" / "src" / "charoite_graph" / "cli.py").write_text(
        'if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    assert упакованный in lm.executables(lm.inventory(tmp_path))


def test_a_packaged_entry_point_can_be_named_in_text(tmp_path):
    """Пакетный путь собирается токенизатором — иначе точку входа нечем назвать.

    Кандидатом модуль дистрибутива стал, а грамматика упоминаний знала только
    `src|scripts|app`: инвариант «точка входа названа кодом или объявлена
    ручной» вырождался в «всегда ручная», и красное «никто не зовёт» нельзя
    было погасить ни правкой кода, ни правкой документации (круг 4 по коду
    №328, GLM I1).
    """
    путь = "packages/cg/src/charoite_graph/cli.py"
    assert lm._tokens(f"запусти {путь} из корня") == {путь}
    assert путь in lm._tokens(f"см. `{путь}`.")
    # плоская и скриптовая формы не потерялись
    assert lm._tokens("src/daemon.py и scripts/doctor.py") == {"src/daemon.py", "scripts/doctor.py"}
    # имя дистрибутива — имя пакета на PyPI: точка и дефис там законны, и без
    # них такой дистрибутив неименуем вовсе (круг 5 по коду №328, GLM I2)
    for имя in ("my-dist", "cg.v2", "2to3", "charoite-graph"):
        путь = f"packages/{имя}/src/pkg/cli.py"
        assert lm._tokens(f"запусти {путь}") == {путь}, f"дистрибутив {имя} неименуем"


def test_a_shape_already_decided_is_not_a_hole_in_the_table(tmp_path):
    """Роль, решённая формой, гейту не дыра.

    `packages/<д>/src/__init__.py` форма решает явно (`stray_init`), но гейт
    собирал вопрос «наш ли это python» из `module_of` + `_is_candidate` и
    краснел советом, который нечем выполнить: правилом-префиксом этот случай
    невыразим — дистрибутив стоит в середине пути (круг 3 по коду №328, GLM I1).
    """
    (tmp_path / "packages" / "d" / "src").mkdir(parents=True)
    (tmp_path / "packages" / "d" / "src" / "__init__.py").write_text("", encoding="utf-8")
    беды = lm.scan(lm.inventory(tmp_path)).problems
    assert not [b for b in беды if "__init__" in b], беды


#: Импорт внутри пакета `p.sub.mod` и имена, которые из него следуют. Второй
#: корпус: грамматику импорта тоже знает ровно одно место (`imports_of`).
IMPORT_SHAPES = (
    ("import llm", {"llm"}),
    ("import charoite_graph.graphs", {"charoite_graph.graphs"}),
    ("from charoite_graph import graphs", {"charoite_graph", "charoite_graph.graphs"}),
    ("from . import graph_names", {"p.sub", "p.sub.graph_names"}),
    ("from .graph_names import X", {"p.sub.graph_names", "p.sub.graph_names.X"}),
    ("from .. import other", {"p", "p.other"}),
    ("from ...too_high import X", set()),       # выше корня пакета — имя не наше
)


def test_the_grammar_of_imports_is_known_in_one_place(tmp_path):
    """Относительные импорты и точечные имена — не экзотика, а основной стиль
    внутри пакета; раньше терялись оба.

    `node.level == 0` отбрасывал `from . import graph_names` целиком, а
    `alias.name.split(".")[0]` обрезал `charoite_graph.graphs` до
    `charoite_graph` — имени, которого нет среди модулей, поэтому ребро
    `daemon → graphs` исчезало молча (входной круг №328).
    """
    rel = "packages/p/src/p/sub/mod.py"
    assert lm.module_of(rel) == "p.sub.mod", "предпосылка корпуса: файл лежит в пакете"
    for src, want in IMPORT_SHAPES:
        got = lm.imports_of(rel, ast.parse(src))
        assert got == want, f"{src!r}: ждали {want}, получили {got}"


def test_the_gate_sees_a_module_that_moved_into_a_package(tmp_path):
    """Переезд в `packages/` виден гейту целиком: модуль, рёбра, нарушение слоя.

    До этой правки дерево ниже давало `modules() == []` и пустой граф: файл
    классифицировался `out` по УМОЛЧАНИЮ (правила `packages/` в таблице не
    было), читать его инвентарь не шёл, и ребро вверх никто не считал.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "packages" / "charoite_graph" / "src" / "charoite_graph").mkdir(parents=True)
    (tmp_path / "src" / "core_mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "top_mod.py").write_text("from charoite_graph import graphs\n", encoding="utf-8")
    пакет = tmp_path / "packages" / "charoite_graph" / "src" / "charoite_graph"
    (пакет / "graphs.py").write_text("from . import names\nimport top_mod\n", encoding="utf-8")
    (пакет / "names.py").write_text("x = 1\n", encoding="utf-8")

    inv = lm.inventory(tmp_path)
    graph = lm.import_graph(inv)
    assert lm.modules(inv) == {"core_mod", "top_mod", "charoite_graph.graphs", "charoite_graph.names"}
    assert graph["top_mod"] == {"charoite_graph.graphs"}, "ребро src → пакет точечным именем"
    assert graph["charoite_graph.graphs"] == {"charoite_graph.names", "top_mod"}, \
        "внутрипакетное ребро относительным импортом и ребро наружу"

    layout = _layout(brief_layers={"low": ["core_mod", "charoite_graph.graphs", "charoite_graph.names"],
                                   "high": ["top_mod"]})
    assert lm.violations(graph, layout) == [("charoite_graph.graphs", "top_mod")], \
        "нарушение слоя ВНУТРИ пакета обязано краснеть так же, как в src/"


def test_a_missing_key_is_never_told_to_just_drop_its_guard(tmp_path):
    """Имени из таблицы нет в дереве — это либо переезд, либо удаление, и
    сторож не знает, что именно. Совет обязан называть ОБА пути.

    Прежнее сообщение знало один ответ — «убрать из layout.json», — и этот
    ответ гасил красное, снимая охрану с переехавшего кода (входной круг
    №328, обе головы). Сопоставление по хвосту имени переездом называть
    нельзя: оно врёт при переименовании, двусмысленности и при удалении
    одного модуля с появлением другого с тем же хвостом (круг 1 по коду
    №328, GLM I2). Поэтому кандидаты — подсказка, а не вердикт.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "packages" / "charoite_graph" / "src" / "charoite_graph").mkdir(parents=True)
    (tmp_path / "src" / "core_mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "packages" / "charoite_graph" / "src" / "charoite_graph" / "graphs.py").write_text(
        "x = 1\n", encoding="utf-8")
    graph = lm.import_graph(lm.inventory(tmp_path))
    layout = _layout(brief_layers={"low": ["core_mod", "graphs"], "high": []})
    assert lm.move_candidates(graph, layout) == {"graphs": ["charoite_graph.graphs"]}
    problems = lm.check(layout, graph, lm.Scan({}, {}, {}, []), {}, repo=tmp_path)
    # факт, подсказка и действие — тремя строками: при переезде пакета имён десяток,
    # и ворох придаточных в одной строке хоронит остальные красные (DS M7 круга 2)
    стало = [p for p in problems if p.startswith("в таблице слоёв есть graphs")]
    подсказки = [p for p in problems if p.startswith("  похоже на переезд")]
    действия = [p for p in problems if p.startswith("  удалён — снять строку")]
    assert len(стало) == 1 and len(подсказки) == 1 and len(действия) == 1
    assert "charoite_graph.graphs" in подсказки[0] and "слой не наследуется" in подсказки[0]
    assert "перенести ключ" in действия[0] and "записать решение о слое" in действия[0]
    # а новый модуль по-прежнему требует слоя: кандидат — подсказка, и гейт не вправе
    # быть увереннее своего источника (DS I4 круга 2)
    assert any("модуль charoite_graph.graphs не отнесён ни к одному слою" in p for p in problems)

    # переименование при переезде: кандидатов нет, но совет всё равно НЕ «убрать»
    (tmp_path / "packages" / "charoite_graph" / "src" / "charoite_graph" / "graphs.py").rename(
        tmp_path / "packages" / "charoite_graph" / "src" / "charoite_graph" / "bundle.py")
    graph2 = lm.import_graph(lm.inventory(tmp_path))
    problems2 = lm.check(layout, graph2, lm.Scan({}, {}, {}, []), {}, repo=tmp_path)
    про_graphs = [p for p in problems2 if p.startswith("в таблице слоёв есть graphs")]
    assert len(про_graphs) == 1 and not any(p.startswith("  похоже на переезд") for p in problems2)
    assert any(p.startswith("  удалён — снять строку") and "перенести ключ" in p for p in problems2)

    # двусмысленность: два кандидата названы оба, ни один не объявлен ответом
    (tmp_path / "packages" / "other" / "src" / "other").mkdir(parents=True)
    (tmp_path / "packages" / "other" / "src" / "other" / "bundle.py").write_text("x = 1\n", encoding="utf-8")
    layout2 = _layout(brief_layers={"low": ["core_mod", "bundle"], "high": []})
    graph3 = lm.import_graph(lm.inventory(tmp_path))
    assert lm.move_candidates(graph3, layout2) == {
        "bundle": ["charoite_graph.bundle", "other.bundle"]}


def test_python_without_a_decision_is_a_hole_in_the_table(tmp_path):
    """Файл, до которого правила не дотянулись, — дыра в таблице, а не политика.

    Инструмент знал этот случай и печатал его в разделе фактов («Python вне
    области, но и без правила»), но гейту о нём не говорил: ни один тест не
    требовал, чтобы список был пуст. Первая редакция инварианта смотрела на
    `kind == "code"` и не видела как раз его: до такого файла не дотянулось
    ни одно правило, и `_by_suffix` тихо отдаёт `out` (круг 1 по коду №328,
    DS C1).
    """
    (tmp_path / "extras").mkdir()
    (tmp_path / "extras" / "later.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "packages" / "p").mkdir(parents=True)
    (tmp_path / "packages" / "p" / "loose.py").write_text("x = 1\n", encoding="utf-8")
    problems = lm.scan(lm.inventory(tmp_path)).problems
    assert any("extras/later.py" in p and "не дотянулось ни одно правило" in p for p in problems), \
        "каталог вне таблицы обязан краснеть, а не числиться политикой"
    assert any("packages/p/loose.py" in p and "ни кандидат в точки входа" in p for p in problems), \
        "правило есть, но файл не по форме — тоже решение человека"

    # а модуль пакета, тесты пакета и скрипт-точка входа молчат
    (tmp_path / "packages" / "p" / "src" / "p").mkdir(parents=True)
    (tmp_path / "packages" / "p" / "src" / "p" / "graphs.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "packages" / "p" / "tests").mkdir()
    (tmp_path / "packages" / "p" / "tests" / "test_x.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "doctor.py").write_text("x = 1\n", encoding="utf-8")
    свежие = lm.scan(lm.inventory(tmp_path)).problems
    новые = [p for p in свежие if "packages/p/src/p/graphs.py" in p
             or "packages/p/tests/test_x.py" in p or "scripts/doctor.py" in p]
    assert новые == [], f"модуль пакета, его тесты и точка входа не краснеют: {новые}"


def test_two_distributions_cannot_share_an_import_name(tmp_path):
    """Одно импортируемое имя у двух файлов — конфликт упаковки, и сторож
    обязан сказать это до релиза.

    Такие дистрибутивы не ставятся рядом, а у сторожа они схлопывались в один
    узел: `modules()` — множество имён, `import_graph` вливал рёбра обоих
    файлов в один ключ, слой у них был один на двоих, и рёбра файла из одного
    слоя судились по слою другого. Ни одна строка об этом не говорила
    (круг 1 по коду №328, GLM I1).
    """
    for d in ("A", "B"):
        (tmp_path / "packages" / d / "src" / "g").mkdir(parents=True)
        (tmp_path / "packages" / d / "src" / "g" / "m.py").write_text("x = 1\n", encoding="utf-8")
    inv = lm.inventory(tmp_path)
    assert any(p.kind == "collision" and "g.m" in p.text for p in inv.problems)
    assert any("имя модуля g.m у 2 файлов" in p for p in lm.scan(inv).problems), \
        "проблема инвентаря обязана доезжать до гейта тем же трактом, что parse и read"


def test_a_package_init_resolves_relative_imports_against_itself(tmp_path):
    """`__init__.py` — файл САМОГО пакета, и относительные имена в нём
    считаются от него, а не от родителя.

    `module_of` нормализует `p/__init__.py` в `p`, и по одному имени уже не
    отличить пакет от модуля внутри чего-то. На этой потере `from . import
    names` в `p/sub/__init__.py` давал `p.names` вместо `p.sub.names` —
    ложное ребро на чужой модуль, а в `p/__init__.py` терялся целиком
    (круг 1 по коду №328, GLM C1).
    """
    assert lm.package_of("packages/p/src/p/__init__.py") == "p"
    assert lm.package_of("packages/p/src/p/sub/__init__.py") == "p.sub"
    assert lm.package_of("packages/p/src/p/graphs.py") == "p"
    assert lm.package_of("src/graphs.py") == ""
    корень = lm.imports_of("packages/p/src/p/__init__.py", ast.parse("from . import names"))
    assert корень == {"p", "p.names"}, "в __init__ пакета точка — это он сам"
    вложенный = lm.imports_of("packages/p/src/p/sub/__init__.py", ast.parse("from . import names"))
    assert вложенный == {"p.sub", "p.sub.names"}, "и у вложенного пакета — он сам, не родитель"


def test_a_layout_directory_is_not_spelled_inside_a_function():
    """Каталоги раскладки названы в объявлении модуля — и ни в одной функции.

    Корпус форм пиннит ПОВЕДЕНИЕ `form`, но не мешает завтра появиться второму
    предикату пути рядом: именно так и возник дефект №328 — дословная копия
    `startswith("src/") and rel.count("/") == 1` жила в `modules()` и в
    `import_graph()`, и переезд сделал их несогласованными молча (круг 1 по
    коду №328, DS M4).

    Написания берутся из `LAYOUT_DIRS`, а не из копии списка в тесте: прежняя
    редакция держала свой набор из шести строк и молчала на глобе `"src/*.py"`
    — идиоме, которой написан сам модуль (`ENTRY_CANDIDATES`), то есть на том
    способе, который человек скопирует первым (круг 3 по коду №328, GLM C1).

    Это дешёвый барьер против прямой копии, а не гарантия. Он смотрит ТЕЛА
    функций и ловит только литерал: обход через модульную константу
    (`_SRC = FLAT_DIR + "/"` и её использование в функции), склейку строк и
    регулярку он не видит (круг 3, DS I7; круг 4, DS C2 — тот же класс третий
    круг подряд). Латать его четвёртой эвристикой смысла нет: гарантию даёт
    тест ниже — ломаем форму и требуем, чтобы сломались ВСЕ потребители.
    Объявления уровня модуля (`LAYOUT_DIRS`, `ENTRY_CANDIDATES`, `_PATH`,
    `TOKEN_PREFIXES`) каталоги называют намеренно: они и есть источник.
    """
    src = (ROOT / "scripts" / "layout_map.py").read_text(encoding="utf-8")
    написания = {w for d in lm.LAYOUT_DIRS for w in (d, f"{d}/", f"{d}/*", f"{d}/*.py", f"{d}/**")}
    assert f"{lm.FLAT_DIR}/*.py" in написания, "глоб точек входа обязан попадать в сторож"
    чужие = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and inner.value in написания:
                чужие.append(f"{node.name}:{inner.lineno} — {inner.value!r}")
    assert not чужие, ("каталог раскладки назван внутри функции — имена живут в LAYOUT_DIRS, "
                       f"и второй предикат пути рассинхронизируется молча: {', '.join(чужие)}")


def test_every_consumer_takes_its_answer_from_the_shape(monkeypatch, tmp_path):
    """Сломай форму — обязаны сломаться ВСЕ потребители.

    Проверка по значению, а не по исходнику: потребитель со своим предикатом
    пути ответит по-старому и на подделанной форме — и покраснеет здесь.
    Текстовый сторож выше три круга подряд обходился новым способом записи
    (круг 2 DS M8, круг 3 GLM C1, круг 4 DS C2); пинить надо решение, а не
    написание.

    Чего и этот тест НЕ ловит (названо честно): совершенно нового потребителя,
    который заведёт свой разбор пути и форму не спросит вовсе. Это видит
    ревью и корпус форм, а не предикат в тесте.
    """
    # список потребителей не руками: кто зовёт `form`, тот обязан быть проверен
    # здесь — иначе следующий потребитель добавляется молча (круг 5, GLM I1)
    исходник = (ROOT / "scripts" / "layout_map.py").read_text(encoding="utf-8")
    зовут = set()
    for узел in ast.walk(ast.parse(исходник)):
        if not isinstance(узел, (ast.FunctionDef, ast.AsyncFunctionDef)) or узел.name == lm.SHAPE_OWNER:
            continue
        if any(isinstance(в, ast.Call) and isinstance(в.func, ast.Name) and в.func.id == lm.SHAPE_OWNER
               for в in ast.walk(узел)):
            зовут.add(узел.name)
    проверены = {"module_of", "package_of", "_is_candidate", "decide", "_owner", "scan", "inventory"}
    assert зовут <= проверены, f"потребитель формы без проверки ниже: {зовут - проверены}"

    дерево = tmp_path / "packages" / "d" / "src" / "p"
    дерево.mkdir(parents=True)
    (дерево / "x.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "p.py").write_text("x = 1\n", encoding="utf-8")   # корень `p` на двоих
    честный = lm.inventory(tmp_path)
    assert [b.kind for b in честный.problems] == ["collision"], "предпосылка: на честной форме конфликт есть"

    # заглушка не только ломает ответ, но и ЗАПИСЫВАЕТ вопрос: половина
    # утверждений ниже отрицательные, и копия формы, знающая только плоскую
    # раскладку, прошла бы их своим «ничего не знаю» (круг 5, DS I2)
    видел: list[str] = []
    monkeypatch.setattr(lm, "form", lambda rel: (видел.append(rel), lm.Form("outside", "", "", None))[1])
    assert lm.module_of("src/graphs.py") is None, "module_of держит свою копию формы"
    assert lm.module_of("packages/cg/src/charoite_graph/graphs.py") is None
    assert lm.package_of("packages/cg/src/charoite_graph/inner/__init__.py") == ""
    assert not lm._is_candidate("packages/cg/src/charoite_graph/cli.py"), "кандидатность мимо формы"
    assert lm.decide("packages/cg/tests/test_x.py").by != "shape", "decide решает без формы"
    assert lm.packaging_conflicts({"src/a.py": "a", "packages/x/src/a/b.py": "a.b"}) == [], \
        "конфликт упаковки считает владельца мимо формы"
    assert lm._owner("packages/x/src/a/b.py") == f"{lm.FLAT_DIR}/", "владелец мимо формы"
    # инвентарь кормит конфликт именами от формы — с подделкой имён нет
    assert lm.inventory(tmp_path).problems == [], "inventory берёт имена мимо формы"
    # гейт спрашивает роль: под подделкой пакетный модуль обязан стать «дырой»
    беды = lm.scan(lm.inventory(tmp_path)).problems
    assert any("packages/d/src/p/x.py" in b for b in беды), "scan решает роль мимо формы"
    спрошено = set(видел)
    for путь in ("src/graphs.py", "packages/cg/src/charoite_graph/inner/__init__.py",
                 "packages/cg/src/charoite_graph/cli.py", "packages/cg/tests/test_x.py",
                 "packages/x/src/a/b.py", "packages/d/src/p/x.py"):
        assert путь in спрошено, f"о {путь} форму никто не спросил — потребитель решает сам"


def test_every_kind_of_problem_has_a_section_and_a_verdict():
    """Вид проблемы нельзя завести, забыв потребителя.

    `collision` завели в круге 1, а `report()` и код выхода `--report`
    перечисляли виды литералами — гейт коллизию видел, замер молчал и
    возвращал 0 (круг 2 по коду №328, DS I3).
    """
    src = (ROOT / "scripts" / "layout_map.py").read_text(encoding="utf-8")
    заведённые = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Problem" and node.args
                and isinstance(node.args[0], ast.Constant)):
            заведённые.add(node.args[0].value)
    assert заведённые, "предпосылка: проблемы создаются вызовом Problem(вид, текст)"
    assert заведённые <= set(lm.PROBLEM_KINDS), f"вид без объявления: {заведённые - set(lm.PROBLEM_KINDS)}"
    with pytest.raises(lm.LayoutError):
        lm.Problem("новый_вид", "вид, которого нет в таблице")
    # каждый объявленный вид виден в замере и учтён в решении о коде выхода
    для_каждого = [lm.Problem(k, f"проба {k}") for k in lm.PROBLEM_KINDS]
    разделы = lm.sections(для_каждого)
    показаны = {p.text for _z, свои in разделы for p in свои}
    assert показаны == {p.text for p in для_каждого}, "вид без раздела замера"
    # виды, после которых замеру верить нельзя, идут ПЕРВЫМИ: человек обязан
    # узнать это раньше, чем прочтёт цифры (круг 3 по коду №328, DS I3)
    недостоверные = {lm.PROBLEM_KINDS[k].section for k in lm.PROBLEM_KINDS if lm.PROBLEM_KINDS[k].invalidates}
    порядок = [з in недостоверные for з, _с in разделы]
    assert порядок == sorted(порядок, reverse=True), f"разделы вперемешку: {[з for з, _с in разделы]}"
    # и раздел о достоверности звучит даже когда всё хорошо
    assert lm.sections([]) and all(not свои for _з, свои in lm.sections([]))


def test_a_bare_init_never_becomes_a_module(tmp_path):
    """`__init__.py` без пакета вокруг себя модулем не называется.

    Обе формы давали призрак: `packages/<дист>/src/__init__.py` → имя
    `__init__` (суффиксный срез не срабатывал на сегменте без точки), а
    `src/__init__.py` — то же самое плюс «пакет» с тем же именем, от которого
    считались бы относительные импорты (круг 1 и круг 2 по коду №328, обе
    головы независимо, каждая про свою ветку).
    """
    assert lm.module_of("packages/d/src/__init__.py") is None
    assert lm.module_of("src/__init__.py") is None
    assert lm.package_of("src/__init__.py") == ""
    # а настоящий пакет по-прежнему называется собой
    assert lm.module_of("packages/d/src/p/__init__.py") == "p"


def test_two_distributions_cannot_share_an_import_root(tmp_path):
    """Конфликт упаковки шире точного совпадения имени модуля.

    `a.py` в одном дистрибутиве и `a/b.py` в другом дают РАЗНЫЕ имена
    (`a` и `a.b`), поэтому проверка на совпадение имён молчит — а
    импортируемый корень `a` у них общий, и рядом такие дистрибутивы не
    ставятся (круг 2 по коду №328, GLM Minor 3).
    """
    (tmp_path / "packages" / "x" / "src").mkdir(parents=True)
    (tmp_path / "packages" / "x" / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "packages" / "y" / "src" / "a").mkdir(parents=True)
    (tmp_path / "packages" / "y" / "src" / "a" / "b.py").write_text("x = 1\n", encoding="utf-8")
    проблемы = lm.inventory(tmp_path).problems
    assert any(p.kind == "collision" and "импортируемый корень a" in p.text for p in проблемы)
    assert not any("имя модуля" in p.text for p in проблемы), "имена разные — первая проверка молчит"


def test_package_tests_are_not_a_source_of_path_mentions(tmp_path):
    """Вид пакетных тестов решает ФОРМА, а не правило `packages/`.

    Правило объявляло весь каталог кодом, и пути, выдуманные в тестах пакета,
    считались бы связями «кто зовёт» — ровно то, от чего корневой `tests/`
    закрыт своим правилом (круг 2 по коду №328, GLM Minor 4).
    """
    assert lm.decide("packages/d/tests/test_x.py").kind == "out"
    d = lm.decide("packages/d/tests/test_x.py")
    assert d.by == "shape", "решила форма; выдать её ответ за правило нельзя — правило хочет code"
    assert d.by in lm.DECIDED_BY and lm.KINDS[d.rule][1] == "code"
    assert lm.decide("packages/d/src/p/m.py").kind == "code"

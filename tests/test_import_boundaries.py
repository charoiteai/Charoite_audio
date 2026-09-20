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

import json
import pathlib
import sys

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
    layout, graph, scanned, execs, _ = world
    problems = lm.check(layout, graph, scanned, execs, map_text=lm.MAP.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


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
    # долг фазы 3 виден на доске: graph → llm не разрешён стрелками, а живёт в allowlist с карточкой
    assert "llm" not in layout["allowed"]["graph"]
    assert ("graph_search", "llm") in lm.allowlist_edges(layout)


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
    assert any("кандидат в точки входа, но правило KINDS" in p for p in lm.inventory(tmp_path).problems)
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

    def fake_regen(layout, graph):
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
    monkeypatch.setattr(lm, "regen", lambda layout, graph: (layout, []))
    lm.main(["--check"])
    assert "отстал от кода" in capsys.readouterr().out, "вне блокировки устаревшая карта — расхождение"
    stale_map.unlink()
    lm.main(["--check"])
    assert "нет — перегенерировать" in capsys.readouterr().out, "пропавшая карта — расхождение, а не зелёный гейт"


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
    for bad in (dup, up, typo, no_why, no_ticket, bad_manual, empty_manual, no_key, wrong_type, dup_order, edge_shape,
                bad_stamp):
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
         "allowed_edges": [], "manual_entry_points": {}, "generated": "2026-09-19T00:00Z"}
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


def test_one_inventory_one_policy_for_every_file(tmp_path):
    """Инвентарь — единственный источник фактов о файлах (Critical DS круга 3):
    битый python в `src/` — строка в problems, не трейсбек и не молчаливый
    пропуск; тот же файл без дерева не даёт ни рёбер, ни точки входа; вид
    файла решает таблица KINDS, а не ветка в сканере."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("import broken\n", encoding="utf-8")
    (tmp_path / "src" / "broken.py").write_text('if __name__ == "__main__":\n    def (:\n', encoding="utf-8")
    inv = lm.inventory(tmp_path)
    assert len(inv.problems) == 1 and inv.problems[0].startswith("src/broken.py не разбирается")
    graph = lm.import_graph(inv)
    assert graph == {"ok": {"broken"}, "broken": set()}
    assert lm.executables(inv) == {}
    scanned = lm.scan(inv)
    assert scanned.problems == inv.problems
    layout = _layout(brief_layers={"low": ["ok", "broken"], "high": []})
    assert inv.problems[0] in lm.check(layout, graph, scanned, {}, repo=tmp_path)
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
    assert "## Читатели переменной CHAROITE_ROOT (3)" in text
    assert "`scripts/early.py`:2; чтение в строке 2 ВЫШЕ первой вставки sys.path (3)" in text
    assert "`scripts/late.py`:3" in text and "ВЫШЕ первой вставки" not in text.split("late.py")[1].split("\n")[0]
    assert "`src/lib.py`:2" in text
    roots_block = text.split("## Корень из положения файла")[1].split("## Точки сборки")[0]
    assert roots_block.splitlines()[0].endswith("(1)"), roots_block.splitlines()[0]
    assert "`src/lib.py`:2" in roots_block and "early.py" not in roots_block
    assert "**LLM** (2)" in text, "шов считается и по голому имени, и по `llm.LLM`"
    assert "**GraphSearch** (0): нет" in text
    # справка argparse и одна ступень `.parent` — не факты
    assert "HELP" not in text and "`src/lib.py`:3" not in text


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
    assert scanned.problems == inv.problems and "src/broken.py не разбирается" in scanned.problems[0]
    assert lm.executables(inv) == {"app/make_app.sh": "shell-скрипт", "scripts/n.sh": "shell-скрипт"}, \
        "scripts/get_models.py без гварда — хелпер, не точка входа (круг 5)"
    # второй скрипт с тем же именем: голое имя из CI — проблема, а не тихий выбор
    (tmp_path / "scripts" / "make_app.sh").write_text("x=1\n", encoding="utf-8")
    scanned = lm.scan(lm.inventory(tmp_path))
    assert "app/make_app.sh" not in scanned.mentions
    assert any("голое имя make_app.sh неоднозначно (app/make_app.sh, scripts/make_app.sh)" in p for p in scanned.problems)

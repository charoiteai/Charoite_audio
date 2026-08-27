"""Правки по аудиту зоны «граф и досье» (26.08, DeepSeek + GLM).

Каждый тест закрывает находку, которую головы описали механикой, а не
подозрением: цитата с обратным слэшем, тема без своего же ядра в промпте,
очередь досье, китайский провенанс.
"""
import pathlib
import re
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SCRIPTS))

import dossier  # noqa: E402
import graph_updater  # noqa: E402
import tier3  # noqa: E402


def test_backslash_in_a_quote_does_not_break_the_merge():
    """Цитата — дословный срез стенограммы, в ней бывает «C:\\1С\\base».

    re.sub разбирает обратные слэши в СТРОКЕ ЗАМЕНЫ: такая пара роняет
    ночную ревизию (invalid group reference) или тихо вставляет группу
    посреди текста. Тот же класс уже чинили в graph_updater — в tier3 он
    оставался. Проверяем по AST: замена в _merge — либо lambda, либо наш
    собственный строковый литерал, но никогда не подставленный контент.
    """
    import ast

    tree = ast.parse((SRC / "tier3.py").read_text(encoding="utf-8"))
    merge = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_merge")
    subs = [n for n in ast.walk(merge) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "sub"]
    assert subs, "в _merge не осталось re.sub — проверь тест"
    for call in subs:
        repl = call.args[1]
        assert isinstance(repl, (ast.Lambda, ast.Constant)), (
            f"tier3.py:{repl.lineno}: замена re.sub считается шаблоном — "
            "контент с обратным слэшем сломает слияние")
        if isinstance(repl, ast.Constant):
            assert "\\" not in repl.value, "литерал замены со слэшем"

    # и сама механика бага, чтобы тест не выродился в проверку стиля
    status = r"миграция C:\1С\base готова"
    text = "## Статус\nстарый\n\n## Хроника\n"
    try:
        re.sub(r"## Статус\n.*?(?=\n## |\Z)", f"## Статус\n{status}\n\n",
               text, count=1, flags=re.S)
        raise AssertionError("образец бага перестал воспроизводиться — проверь тест")
    except re.error:
        pass
    out = re.sub(r"## Статус\n.*?(?=\n## |\Z)", lambda _: f"## Статус\n{status}\n\n",
                 text, count=1, flags=re.S)
    assert status in out


def test_the_theme_core_itself_reaches_the_prompt():
    """Хаб — первый источник темы: без него сводка пишется вслепую.

    build_prompt берёт первые MAX_SOURCES участников, а сортировка ставила
    ядро в конец — в кластере из 14+ встреч сама тема в промпт не попадала.
    """
    files = {"Тема": {"kind": "Ядра", "text": "## Статус\nидёт", "title": "Тема"}}
    for i in range(20):
        files[f"2026-08-{i + 1:02d}_1000"] = {"kind": "Встречи", "text": "[[Тема]]",
                                              "title": f"2026-08-{i + 1:02d}_1000"}
    back = {"Тема": {k for k in files if k != "Тема"}}
    members = dossier.clusters(files, back)["Тема"]
    assert members[0] == "Тема", "ядро темы должно идти первым"
    assert "Тема" in members[:dossier.MAX_SOURCES]


def test_unbuilt_dossiers_go_first_in_the_queue(tmp_path):
    """Иначе большие «несвежие» темы забирают все слоты ночи навсегда."""
    folder = tmp_path / "Досье"
    folder.mkdir()
    (folder / "Большая.md").write_text("есть", encoding="utf-8")
    cl = {"Большая": ["a", "b", "c", "d"], "Новая": ["e", "f", "g"]}
    order = sorted(cl.items(),
                   key=lambda kv: ((folder / f"{kv[0]}.md").exists(), -len(kv[1])))
    assert [t for t, _ in order] == ["Новая", "Большая"]


def test_full_rebuild_is_not_capped_by_the_nightly_limit():
    """«--full» обещало «пересобрать все темы», а упиралось в потолок 12."""
    src = (SCRIPTS / "nightly_dossier.py").read_text(encoding="utf-8")
    assert "if not full and built >= limit:" in src, \
        "полный прогон снова ограничен лимитом ночи"


def test_nightly_runs_a_full_dossier_pass_weekly():
    """У ревизии ядер воскресный полный прогон был, у досье — нет."""
    sh = (SCRIPTS / "nightly.sh").read_text(encoding="utf-8")
    assert "DOSSIER_MODE" in sh and "--full" in sh
    assert 'date +%u' in sh


def test_chinese_quote_is_verified_not_dropped():
    """В zh-режиме второй уровень сверки не работал вовсе: токенизация
    ловила только кириллицу и латиницу, q_words выходил пустым."""
    found = graph_updater._closest_span(
        "我们 决定 采用 新 方案", "10:15 德米特里: 我们 决定 采用 新 方案 ,下周开始")
    assert found, "близкий китайский пересказ должен находиться в стенограмме"
    assert not graph_updater._closest_span(
        "完全 不同 的 内容 无关", "10:15 德米特里: 我们 决定 采用 新 方案"), \
        "выдумка обязана отбрасываться и в zh"


def test_prefilter_threshold_is_documented_as_is():
    """Порог отбора кандидатов — 0.55, а не 0.60: замер по неверной
    константе едва не привёл к правке, ломающей поиск вложений."""
    assert tier3.EMB_PREFILTER == 0.55
    assert tier3.NEST_LO == 0.5 and tier3.DUP_T == 0.72

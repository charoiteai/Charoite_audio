"""Гигиена графа и временных файлов — то, что портится тихо.

Ни один из этих дефектов не давал ошибки: узел просто оставался несвязанным,
провенанс подтверждал выдумку, а копия часовой записи лежала в /var/folders
до перезагрузки, о чём ретеншн приватности не знал.
"""
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import graph_updater as g  # noqa: E402


def test_safe_name_escapes_wiki_syntax():
    """Имя узла уезжает в [[ссылку]] — квадратные скобки её обрывают."""
    for bad in ("Витрина [v2]", "Релиз #17", "Блок ^abc"):
        out = g.safe_name(bad)
        assert not (set(out) & set("[]#^")), f"«{bad}» → «{out}»: ссылка сломается"


def test_safe_name_never_collapses_to_nothing():
    """Пустое имя дало бы скрытый файл «.md» и совпадало бы с любым узлом."""
    for empty in ("...", "   ", "///", "[]"):
        assert g.safe_name(empty).strip(), f"«{empty}» схлопнулось в пустоту"


def test_quote_check_rejects_unverifiable_chinese():
    """В zh-режиме пустая нормализация пропускала выдумки как подтверждённые.

    Старый шаблон искал только [а-яёa-z0-9]: у китайской цитаты слов не
    находилось, norm(quote) выходил пустым, а пустая строка входит в любую —
    и провенанс «кто и когда это сказал» подтверждал то, чего в стенограмме
    не было.
    """
    # Фильтр «меньше трёх слов» китайскую фразу отбросил бы и без нормализации,
    # поэтому берём цитату, которая до него доходит: пробелы в ней есть.
    core = {"цитата": "我们 决定 采用 方案", "кто": "德米特里", "время": "10:15"}
    out = g.core_anchor(core, "совершенно другой разговор про погоду")
    assert out == "", "выдуманная китайская цитата прошла как подтверждённая"

    # И обратная сторона: настоящая китайская цитата обязана подтверждаться.
    real = {"цитата": "我们 决定 采用 方案", "кто": "德米特里", "время": "10:15"}
    ok = g.core_anchor(real, "10:15 德米特里: 我们 决定 采用 方案 ,下周开始")
    assert ok, "дословная китайская цитата не прошла проверку"


def test_scratch_dir_is_removed_with_the_process():
    """Копия полного аудио не должна пережить процесс."""
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "import transcribe_file as tf;"
        "d = tf._scratch_dir(); (d / 'probe.wav').write_bytes(b'x'); print(d)"
    )
    root = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=root)
    path = pathlib.Path(out.stdout.strip())
    assert str(path), f"скрипт не отработал: {out.stderr[-300:]}"
    assert not path.exists(), f"временная копия аудио осталась в {path}"


def test_canonical_does_not_glue_short_names_to_long_nodes(tmp_path):
    """«Ян» не должен приклеиваться к «Январский релиз».

    Двухбуквенные имена из распознавания входят подстрокой в десятки узлов.
    Пока проверка была без ограничения длины, единственное совпадение
    возвращалось как канонический узел — и встреча дописывалась в чужой файл.
    """
    people = tmp_path / "Люди"
    people.mkdir()
    (people / "Январский релиз.md").write_text("# Январский релиз", encoding="utf-8")

    assert g.find_canonical(tmp_path, "Ян") is None, "короткое имя приклеилось к длинному узлу"


def test_canonical_still_matches_real_variants(tmp_path):
    """Но настоящие варианты одного имени по-прежнему схлопываются."""
    systems = tmp_path / "Системы"
    systems.mkdir()
    (systems / "Витрина продаж.md").write_text("# Витрина продаж", encoding="utf-8")

    found = g.find_canonical(tmp_path, "витрина продаж")
    assert found is not None and found.stem == "Витрина продаж"


def test_graph_logs_expire(tmp_path, monkeypatch):
    """Логи графа с содержимым встреч не должны копиться годами."""
    import daemon as d

    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "graph_2020-01-01_1200.log"
    old.write_text("Дмитрий: обсудили миграцию", encoding="utf-8")
    import os
    stale = old.stat().st_mtime - 30 * 86400
    os.utime(old, (stale, stale))
    fresh = logs / "graph_now.log"
    fresh.write_text("сегодняшняя встреча", encoding="utf-8")

    # retry_<штамп>.log — stdout повторной пересборки с именами участников;
    # третий класс логов, который ретеншн не видел (аудит DeepSeek 16.08)
    retry_old = logs / "retry_2020-01-01_1200.log"
    retry_old.write_text("имена: Дмитрий", encoding="utf-8")
    os.utime(retry_old, (stale, stale))

    monkeypatch.setattr(d, "ROOT", tmp_path)
    d._prune_graph_logs({"audio": {"record_keep_days": 2}})

    assert not old.exists(), "старый лог с содержимым встречи остался"
    assert not retry_old.exists(), "старый retry-лог с именами участников остался"
    assert fresh.exists(), "свежий лог удалён — диагностику потеряли"

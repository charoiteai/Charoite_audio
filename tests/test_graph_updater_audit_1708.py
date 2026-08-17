"""Правки graph_updater по аудиту ночного конвейера 17.08 (DeepSeek + GLM).

- запуск без аргумента брал производный файл за стенограмму;
- разбор длинной встречи видел только первые 11000 знаков — решения из
  конца терялись;
- статус ядра писался в redirect-заглушку tier3, а не в канон.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import graph_updater as gu  # noqa: E402


def test_latest_transcript_ignores_derived_files(tmp_path, monkeypatch):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    import os
    live = tdir / "2026-08-03_113012.md"
    live.write_text("стенограмма", encoding="utf-8")
    os.utime(live, (100, 100))
    for name in ("2026-08-03_1130_Тема_разбор.md", "2026-08-03_1130_Тема_ревизия_claude.md",
                 "2026-08-03_113012_minutes.md", "2026-08-03_113012_hints.md"):
        f = tdir / name
        f.write_text("производный", encoding="utf-8")
        os.utime(f, (200, 200))       # моложе стенограммы
    monkeypatch.setattr(gu, "ROOT", tmp_path)

    assert gu.latest_transcript() == live


def test_debrief_excerpt_keeps_the_end_of_a_long_meeting():
    text = "начало " * 1000 + "СЕРЕДИНА " * 2000 + "договорились: релиз пятнадцатого " * 300
    cut = gu.debrief_excerpt(text, limit=11000)
    assert len(cut) < 11200
    assert cut.startswith("начало ")
    assert "релиз пятнадцатого" in cut, "решения из конца встречи должны дойти до разбора"
    assert "опущена" in cut, "пропуск середины помечен явно"
    short = "коротко"
    assert gu.debrief_excerpt(short) == short


def test_core_status_goes_to_the_canonical_core_not_the_redirect_stub(tmp_path):
    graph = tmp_path
    d = graph / "Ядра"
    d.mkdir()
    canon = d / "Платёжный провайдер.md"
    canon.write_text("---\ntype: ядро\n---\n# Платёжный провайдер\n\n## Статус\n"
                     "выбираем _(обновлено 2026-07-10)_\n\n## Хроника\n- [[Встречи/2026-07-10_1000]]\n",
                     encoding="utf-8")
    dup = d / "Провайдер платежей.md"
    dup.write_text("---\ntype: ядро\ntags: [дубль, redirect, tier3-nli]\n---\n"
                   "# Провайдер платежей → [[Ядра/Платёжный провайдер]]\n\n"
                   "⚠️ **Дубль. Смерджен Tier3-NLI.** Хроника перенесена в "
                   "[[Ядра/Платёжный провайдер|Платёжный провайдер]].\n", encoding="utf-8")

    gu.upsert_core(graph, {"имя": "Провайдер платежей", "статус": "выбран ЮPay",
                           "обновление": "подписали"}, "Встречи/2026-07-15_1400", "2026-07-15_1400")

    canon_text = canon.read_text(encoding="utf-8")
    assert "выбран ЮPay" in canon_text, "статус ушёл не в канон"
    assert "[[Встречи/2026-07-15_1400]]" in canon_text, "хроника ушла не в канон"
    dup_text = dup.read_text(encoding="utf-8")
    assert "Дубль. Смерджен" in dup_text and "2026-07-15_1400" not in dup_text, \
        "заглушка должна остаться заглушкой"


def test_resolve_core_path_stops_on_broken_or_circular_redirect(tmp_path):
    d = tmp_path / "Ядра"
    d.mkdir()
    a = d / "А.md"
    a.write_text("# А → [[Ядра/Б]]\n\nДубль. Смерджен Tier3-NLI.\n", encoding="utf-8")
    # цели нет — остаёмся на заглушке (лучше видимая заглушка, чем потерянный файл)
    assert gu.resolve_core_path(d, "А") == a
    b = d / "Б.md"
    b.write_text("# Б → [[Ядра/А]]\n\nДубль. Смерджен Tier3-NLI.\n", encoding="utf-8")
    assert gu.resolve_core_path(d, "А") in (a, b)   # цикл: конечное число шагов
    assert gu.resolve_core_path(d, "Новое") == d / "Новое.md"

#!/usr/bin/env python3
"""Ретро-генерация артефактов для встреч, где суфлёр не работал вживую.

Встречи 15.07 восстановлены из записей задним числом — минуток/тезисов/
разборов у них не существовало. Генерим по стенограмме (Ollama qwen):
  - {stamp}_minutes.md и {stamp}_разбор.md → в transcripts (штатные имена,
    конвейер и recall их видят), архив подхватит как Минутки/Разбор;
  - Тезисы.md → сразу в папку архива (в живой стенограмме их дом —
    секция «Ко-мышление», ретроспективно её не подделываем).

Запуск: .venv/bin/python src/retro_fill.py   # только недостающее, идемпотентно
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from llm import LLM, LLMHTTPError  # noqa: E402
import live_sidecar  # noqa: E402
import meeting_source  # noqa: E402
import meeting_stamp  # noqa: E402
from meeting_archive import (SummaryMode, SummaryOutcome, archive_meeting,  # noqa: E402
                             canon_tally_line, cothinking_notes)
from meeting_processing import find_final_transcript  # noqa: E402

from charoite_paths import harden_umask, resolve_root
from config_loader import load_user_or_example


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте.

    Снимок на уровне модуля считался при импорте, то есть раньше, чем точка
    входа успевала назвать корень: половина процесса жила в названном корне,
    половина — в выведенном из положения файла, и расхождение было немым
    (замер 21.09, №329).
    """
    return resolve_root(__file__)
import datetime as _dt
import graphs
NOTE = f"<!-- восстановлено ретроспективно по стенограмме, {_dt.date.today()} -->\n"

MINUTES_PROMPT = (
    "Составь минутки в markdown строго по шаблону:\n"
    "# Минутки встречи\n"
    "**Дата/время:** … **Участники:** …\n"
    "## Темы\n## Решения\n## Поручения\n## Открытые вопросы\n## Риски\n\n"
    "Правила: только то, что прозвучало; пункт — одна строка; никаких "
    "markdown-таблиц; поручение чекбоксом «- [ ] **Имя** — что — срок»; "
    "решение «- **что решили** — кто внедряет»; пустой раздел — «нет»; "
    "весь документ до 900 знаков."
)
DEBRIEF_PROMPT = (
    "Составь разбор строго по разделам:\n"
    "# Разбор встречи\n"
    "## Вопросы встречи и ответы\n(каждый прозвучавший вопрос → ответ, если прозвучал; если нет — «открыт»)\n"
    "## Задачи\n(кто/что/срок)\n"
    "## Возможные решения открытых вопросов\n(варианты с плюсами/минусами, кратко)\n"
    "## Рекомендации: что проработать до следующей встречи\n(конкретные шаги)"
)
THESES_PROMPT = (
    "Выдели из стенограммы всё по-настоящему ценное, каждое с новой строки со строгим префиксом:\n"
    "📌 — контрольная точка: решение, договорённость, срок, поручение (кто/что/когда)\n"
    "💎 — ценная информация: цифра, имя, обещание, условие, риск\n"
    "💭 — мысль (до трёх): противоречие, упущенный вопрос, скрытый риск\n"
    "Телеграфно, по-русски, без вступлений."
)


def gen(cfg: dict, system: str, transcript: str, task: str, note: str | None = None) -> str:
    """Один вызов модели по речи; оговорка о записи — блоком ПОСЛЕ обрезки
    речи, снаружи её (№317)."""
    try:
        client = LLM(cfg)
        return client.complete(
            f"Стенограмма встречи:\n\n{transcript[:24000]}\n\n" + client.recording_block(note) + task,
            system=system, model=cfg["llm"]["model"], think=False,
            temperature=0.3, num_ctx=16384, timeout=900)
    except LLMHTTPError as e:
        # как и раньше: ошибка сервера не валит весь ретро-прогон,
        # файл этого артефакта просто не создаётся
        print(f"ретро: {e}", file=sys.stderr)
        return ""


def _theses_path(folder: pathlib.Path) -> pathlib.Path:
    return folder / "Тезисы.md"


def process(f: pathlib.Path, cfg: dict, graph: pathlib.Path, tdir: pathlib.Path,
            summary: str | None = None, tally: collections.Counter | None = None,
            canon_tally: collections.Counter | None = None) -> list[str]:
    """Производные одной стенограммы по паспорту (№309), не «если файла нет».

    Минутки — тем же конвейером, что пересборка (`finalize_minutes` +
    `record_minutes_passport`): ретро-генерация своим промптом была третьим
    конвейером — без среза «Ко-мышления», без нормализации поручений, канона и
    `.prev` (Critical GLM входного круга). Разбор и тезисы — по состоянию
    паспорта и политике ретро-обхода (`POLICY_RETRO`): MISSING/STALE → собрать
    (прежняя версия в `.prev/` рядом со стенограммой), FRESH → модель не
    звать, HUMAN и UNKNOWN → не трогать (у старого корпуса паспорта нет — это
    не знание о человеке, а его отсутствие; бэкфилла по решению нет).

    Тезисы: у встречи с живым ко-мышлением (`> HH:MM 📌/💭 …` в стенограмме)
    файл тезисов собирает архив из этих строк, и модель за них не платит —
    живые тезисы контура встречи старше ретро-сводки (раньше то же выходило
    случайно, порядком «архив раньше проверки» — Critical DS выходного круга;
    теперь это правило названо). Ретро-тезисы модели — только у встреч без
    живого ко-мышления: импорт записи, восстановление задним числом.

    Саммари — производная с паспортом в том же сайдкаре (№314), пишет его
    `archive_meeting` в режиме `SummaryMode` (значение перечисления от CLI до
    шва — не пара «политика + флаг», где пустая политика ложна; Critical DS и
    GLM круга 3). AUTO: MISSING/STALE строим, UNKNOWN не трогаем; ADOPT —
    паспорт исправному легаси без модели по всему канону и ничего не строить;
    REBUILD — то же присвоение, потом собрать остальное моделью. Без порядка
    «сначала присвоить» rebuild переписал бы 224 исправных саммари за час
    модели — присвоение идёт внутри `archive_meeting` перед решением о сборке,
    по той же папке и тому же снимку канона (Critical DS кругов 1 и 2). Исход
    саммари приходит возвратом (`Archived.summary`), отчёт его печатает, а не
    выводит из состояния диска (Critical DS и Important GLM круга 2); `tally`
    считает исходы по значению `action`, не по словам (Minor DS круга 3).

    Возвращает список собранного; одна строка stdout на встречу: что собрано и
    что пропущено с состоянием — «полная» больше не прячет HUMAN/UNKNOWN
    (Important DS)."""
    import rebuild_transcript as rt

    bare = meeting_stamp.stamp_of(f.stem)
    if bare is None:              # публичная точка входа — своё предусловие (Minor DS)
        print(f"ретро: {f.name}: имя без штампа встречи — пропуск", file=sys.stderr)
        return []
    stamp = meeting_stamp.graph_key(tdir, f.stem, graph)
    slug = f.stem[len(bare) + 1:] if f.stem != bare else ""
    text = f.read_text(encoding="utf-8")
    # речь + оговорка о записи (№317) — по ПРЯМОМУ сайдкару, как у живого пути:
    # со штампом `bare` после наката темы сайдкара нет, и хеш расходился с
    # graph_updater (Critical DS и GLM выходного круга)
    source = meeting_source.of(f, text)
    source_sha = source.sha()          # речь + оговорка (Minor GLM круга 2: не «хеш речи»)
    meta = live_sidecar.read(f) or {}
    made: list[str] = []
    skipped: list[str] = []

    mpath = meeting_stamp.derivative_path(f, "minutes", graph)
    state = live_sidecar.derivative_state(mpath, meta, "minutes", source_sha)
    if live_sidecar.wants_build(state, live_sidecar.POLICY_RETRO):
        outcome = rt.finalize_minutes(f, text, meta, cfg, rt.minutes_names(meta))
        rt.record_minutes_passport(f, mpath, outcome, text, cfg)
        if outcome == "regenerated":
            made.append("минутки")
        else:
            skipped.append(f"минутки {outcome}")
    else:
        skipped.append(f"минутки {state}")

    dpath = meeting_stamp.derivative_path(f, "debrief", graph)
    state = live_sidecar.derivative_state(dpath, meta, "debrief", source_sha)
    if live_sidecar.wants_build(state, live_sidecar.POLICY_RETRO):
        out = gen(cfg, "Ты аналитик после рабочей встречи. Пиши по-русски, сухо, markdown. "
                       "Не выдумывай факты.", source.speech, DEBRIEF_PROMPT, note=source.recording_note)
        out = meeting_source.with_note(out, source.recording_note) if out else out
        _built(made, skipped, "разбор", out,
               lambda: _write_derivative(f, dpath, "debrief", NOTE + out + "\n", source_sha))
    else:
        skipped.append(f"разбор {state}")

    archived = archive_meeting(graph, tdir, stamp, slug, files_key=f.stem,
                               mode=SummaryMode(summary) if summary else SummaryMode.AUTO)
    folder = archived.folder if archived is not None else None
    if archived is not None:
        if tally is not None:
            tally[archived.summary.action] += 1
            if archived.summary.action == SummaryOutcome.SKIPPED and archived.summary.reason:
                tally[f"причина: {archived.summary.reason.split(':')[0]}"] += 1
        if line := archived.summary.line():
            (made if archived.summary.made else skipped).append(line)
        # канон минуток (№366): не тронутый или не прочитанный — в отчёт встречи с
        # причиной; действия — в свой счётчик, не в `tally` саммари: та сводка
        # печатается только под --summary и под своим заголовком
        if archived.canon is not None:
            if canon_tally is not None:
                canon_tally[archived.canon.action] += 1
            if archived.canon.alarming:
                skipped.append(archived.canon.line())
    if folder is not None:
        tpath = _theses_path(folder)
        # «живые» — по факту: архив собирает файл из КОПИИ стенограммы, и если
        # копию перебила легаси-производная без строк ко-мышления, файла нет —
        # тогда к модели, как у встречи без живого контура (Important DS круга 2)
        if cothinking_notes(text) and tpath.exists():
            skipped.append("тезисы живые")        # файл собрал архив из строк ко-мышления
        else:
            state = live_sidecar.derivative_state(tpath, meta, "theses", source_sha)
            if live_sidecar.wants_build(state, live_sidecar.POLICY_RETRO):
                out = gen(cfg, "Ты выделяешь ценное из стенограмм. Телеграфно, по-русски.",
                          source.speech, THESES_PROMPT, note=source.recording_note)
                # строка о неполной записи — и у тезисов: паспорт говорит «собрано по
                # источнику с оговоркой», документ обязан это показывать (Important DS круга 2)
                out = meeting_source.with_note(out, source.recording_note) if out else out
                _built(made, skipped, "тезисы", out,
                       lambda: _write_derivative(f, tpath, "theses",
                                                 "# Тезисы встречи (📌 КТ · 💎 факты · 💭 мысли)\n" + NOTE + "\n"
                                                 + out + "\n", source_sha))
            else:
                skipped.append(f"тезисы {state}")
    parts = []
    if made:
        parts.append("собрано: " + ", ".join(made))
    if skipped:
        parts.append("пропущено: " + ", ".join(skipped))
    print(f"{stamp}: {'; '.join(parts) if parts else 'ничего не менялось'}")
    return made


def _built(made: list[str], skipped: list[str], kind: str, out: str, write) -> None:
    """Исход генерации — в отчёт: пустой ответ модели раньше не попадал ни в
    «собрано», ни в «пропущено», и строка говорила «полная» без разбора
    (Important GLM круга 2 по №309; у облачной модели 20 таймаутов из 75 прогонов
    за август–сентябрь, №189)."""
    if not out:
        skipped.append(f"{kind} — модель не ответила")
    elif write():
        made.append(kind)
    else:
        skipped.append(f"{kind} — запись отклонена")


def prev_path(live: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """Прежнее имя — шов живёт в `live_sidecar` (единый писатель производных, №314)."""
    return live_sidecar.prev_path(live, path)


def _write_derivative(live: pathlib.Path, path: pathlib.Path, kind: str, body: str,
                      source_sha: str) -> bool:
    """Записалось ли: шов возвращает состояние после записи или None (№314);
    ретро-отчёту нужен только факт записи — состояние он печатает отдельно."""
    return live_sidecar.write_derivative(live, path, kind, body, source_sha,
                                         log=lambda msg: print(f"ретро: {msg}", file=sys.stderr)).written


def _minute(stem: str) -> str | None:
    bare = meeting_stamp.stamp_of(stem)
    return meeting_stamp.minute_of(bare) if bare else None


def main(argv: list[str] | None = None):
    harden_umask()   # минутки, разбор, архив — данные встреч, только владельцу
    ap = argparse.ArgumentParser(
        description="Производные стенограмм по паспорту: минутки, разбор, тезисы, архив встречи.")
    ap.add_argument("paths", nargs="*", help="стенограммы; без них — обход всего каталога")
    ap.add_argument("--summary", choices=("adopt", "rebuild"),
                    help="легаси-саммари без паспорта: adopt — присвоить исправные без модели; "
                         "rebuild — присвоить исправные и пересобрать остальные моделью")
    ns = ap.parse_args(sys.argv[1:] if argv is None else argv)
    cfg = load_user_or_example(_root())
    graph = graphs.graph_dir(cfg) or sys.exit("sufler.graph_dir не задан")
    tdir = _root() / cfg["log"]["transcripts_dir"]
    args = ns.paths
    missing: list[str] = []
    if args:
        # хвост импорта — только своя стенограмма: обход всех 302 звал
        # archive_meeting (и переиндексацию архива) на каждую (DS I4 по №309).
        # Путь приходит от импорта ДО ретитла: graph_updater в своём процессе
        # уже переименовал файл под тему — ищем встречу тем же правилом, что
        # статус и forget (`find_final_transcript`), иначе хвост падал на
        # `stat()` и импорт объявлялся проваленным (Critical GLM выходного
        # круга). Путь вне каталога стенограмм — не встреча: архив завёл бы
        # пустую папку в графе (Minor GLM).
        files = []
        for a in args:
            given = pathlib.Path(a)
            f = find_final_transcript(given)
            # резолвер ищет по каталогу; своя ли это встреча — по минуте штампа
            # (ретитл меняет секунды на минуту, минуту — никогда); чужая минута
            # или чужой каталог — не «пропуск в stderr», а отказ хвоста кодом 1:
            # иначе импорт показал бы «ready» без единой производной
            # (Important DS и Minor GLM круга 2)
            if not f.is_file():
                missing.append(a)
            elif f.parent != tdir.resolve():
                missing.append(f"{a} (вне каталога стенограмм)")
            elif _minute(f.stem) != _minute(given.stem):
                missing.append(f"{a} (резолвер нашёл встречу другой минуты: {f.name})")
            else:
                files.append(f)
    else:
        files = sorted(tdir.glob("*.md"))
    done = 0
    tally: collections.Counter = collections.Counter()
    canon_tally: collections.Counter = collections.Counter()
    for f in files:
        if any(f.stem.endswith(s) for s in meeting_stamp.AUX_SUFFIXES):
            continue     # производные, копии — один список хвостов на проект (GLM I3 по №309)
        bare = meeting_stamp.stamp_of(f.stem)
        if bare is None or f.stat().st_size < 600:
            continue
        process(f, cfg, graph, tdir, summary=ns.summary, tally=tally, canon_tally=canon_tally)
        done += 1
    if not args:
        print(f"ретро: обход {tdir.name}: встреч обработано {done}")
        if ns.summary:
            # сводка миграции легаси — по исходам, не по словам отчёта (критика 1 GLM
            # круга 3: без сводки про 298 легаси забывают на месяцы)
            print("ретро: саммари — " + ", ".join(
                f"{k} {v}" for k, v in sorted(tally.items())))
    # вне `if not args`: хвост импорта с явными путями тоже раскладывает канон
    print(f"ретро: {canon_tally_line(canon_tally)}")
    if missing:
        sys.exit("ретро: стенограммы нет: " + ", ".join(missing))


if __name__ == "__main__":
    main()

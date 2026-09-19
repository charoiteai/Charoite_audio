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

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from llm import LLM, LLMHTTPError  # noqa: E402
import live_sidecar  # noqa: E402
import meeting_source  # noqa: E402
import meeting_stamp  # noqa: E402
import safe_write  # noqa: E402
from meeting_archive import archive_meeting, cothinking_notes  # noqa: E402
from meeting_processing import find_final_transcript  # noqa: E402

from charoite_paths import harden_umask, resolve_root
from config_loader import load_user_or_example

ROOT = resolve_root(__file__)
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


def process(f: pathlib.Path, cfg: dict, graph: pathlib.Path, tdir: pathlib.Path) -> list[str]:
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
    source = meeting_source.of(f, text, bare)          # речь + оговорка о записи (№317)
    speech_sha = source.sha()
    meta = live_sidecar.read(f) or {}
    made: list[str] = []
    skipped: list[str] = []

    mpath = meeting_stamp.derivative_path(f, "minutes", graph)
    state = live_sidecar.derivative_state(mpath, meta, "minutes", speech_sha)
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
    state = live_sidecar.derivative_state(dpath, meta, "debrief", speech_sha)
    if live_sidecar.wants_build(state, live_sidecar.POLICY_RETRO):
        out = gen(cfg, "Ты аналитик после рабочей встречи. Пиши по-русски, сухо, markdown. "
                       "Не выдумывай факты.", source.speech, DEBRIEF_PROMPT, note=source.recording_note)
        out = meeting_source.with_note(out, source.recording_note) if out else out
        _built(made, skipped, "разбор", out,
               lambda: _write_derivative(f, dpath, "debrief", NOTE + out + "\n", speech_sha))
    else:
        skipped.append(f"разбор {state}")

    folder = archive_meeting(graph, tdir, stamp, slug, files_key=f.stem)
    if folder is not None:
        tpath = _theses_path(folder)
        # «живые» — по факту: архив собирает файл из КОПИИ стенограммы, и если
        # копию перебила легаси-производная без строк ко-мышления, файла нет —
        # тогда к модели, как у встречи без живого контура (Important DS круга 2)
        if cothinking_notes(text) and tpath.exists():
            skipped.append("тезисы живые")        # файл собрал архив из строк ко-мышления
        else:
            state = live_sidecar.derivative_state(tpath, meta, "theses", speech_sha)
            if live_sidecar.wants_build(state, live_sidecar.POLICY_RETRO):
                out = gen(cfg, "Ты выделяешь ценное из стенограмм. Телеграфно, по-русски.",
                          source.speech, THESES_PROMPT, note=source.recording_note)
                _built(made, skipped, "тезисы", out,
                       lambda: _write_derivative(f, tpath, "theses",
                                                 "# Тезисы встречи (📌 КТ · 💎 факты · 💭 мысли)\n" + NOTE + "\n"
                                                 + out + "\n", speech_sha))
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
    """Куда ложится прежняя версия производной: `.prev/` рядом со СТЕНОГРАММОЙ у
    всех видов — и у тезисов, чей файл живёт в папке архива внутри графа:
    скрытый каталог в графе синкался бы iCloud и попадал под `_unhide` архива
    (Important DS выходного круга по №309). Файл из чужой папки получает
    префикс стема стенограммы — иначе «Тезисы.md» всех встреч легли бы в одно имя."""
    # имя чужой папки — от голого штампа, не от стема: ретитл меняет стем, и
    # каждое поколение получало бы своё имя навсегда (Minor GLM круга 2)
    bare = meeting_stamp.stamp_of(live.stem) or live.stem
    name = path.name if path.parent == live.parent else f"{bare}__{path.name}"
    return live.parent / ".prev" / name


def _write_derivative(live: pathlib.Path, path: pathlib.Path, kind: str, body: str,
                      speech_sha: str) -> bool:
    """Записать производную и выдать ей паспорт. Прежняя версия — в `.prev/`
    рядом со стенограммой (`prev_path`): уверенная, но неверная генерация не
    должна быть невозвратной (как у минуток)."""
    before = safe_write.stat_snapshot(path)
    if before is not None:
        try:
            prev = prev_path(live, path)
            prev.parent.mkdir(exist_ok=True)
            safe_write.write_text(prev, path.read_text(encoding="utf-8"))
        except OSError as e:
            print(f"ретро: прежняя версия {path.name} не сохранена ({e}) — не перезаписываю", file=sys.stderr)
            return False
    # запись под гейтом «файл не менялся под рукой»: минута генерации — окно
    # для редактора; обрыв не оставит «готовый» битый файл (аудит 13.09, GLM M6)
    if not safe_write.write_text(path, body, expect=before, expect_absent=before is None):
        print(f"ретро: {path.name} изменился под рукой — не перезаписываю", file=sys.stderr)
        return False
    if not live_sidecar.attest(live, kind, body, speech_sha):
        print(f"ретро: паспорт {kind} не записан — следующая пересборка сочтёт файл чужим", file=sys.stderr)
    return True


def _minute(stem: str) -> str | None:
    bare = meeting_stamp.stamp_of(stem)
    return meeting_stamp.minute_of(bare) if bare else None


def main(argv: list[str] | None = None):
    harden_umask()   # минутки, разбор, архив — данные встреч, только владельцу
    cfg = load_user_or_example(ROOT)
    graph = graphs.graph_dir(cfg) or sys.exit("sufler.graph_dir не задан")
    tdir = ROOT / cfg["log"]["transcripts_dir"]
    args = sys.argv[1:] if argv is None else argv
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
    for f in files:
        if any(f.stem.endswith(s) for s in meeting_stamp.AUX_SUFFIXES):
            continue     # производные, копии — один список хвостов на проект (GLM I3 по №309)
        bare = meeting_stamp.stamp_of(f.stem)
        if bare is None or f.stat().st_size < 600:
            continue
        process(f, cfg, graph, tdir)
        done += 1
    if not args:
        print(f"ретро: обход {tdir.name}: встреч обработано {done}")
    if missing:
        sys.exit("ретро: стенограммы нет: " + ", ".join(missing))


if __name__ == "__main__":
    main()

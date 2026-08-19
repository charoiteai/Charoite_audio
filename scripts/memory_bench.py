#!/usr/bin/env python3
"""Мини-бенч памяти: эталонные вопросы по архиву → проверка фактов в ответе.

Регрессионные тесты, но для КАЧЕСТВА ПАМЯТИ, а не кода: пороги NLI, длина
сниппетов, промпты синтеза крутятся часто — и каждый твик может незаметно
уронить ответы по архиву. Бенч гоняет реальный RAG-контур (поиск по графу →
синтез локальной моделью) на вопросах из config/memory_bench.yaml и
проверяет, что обязательные факты (`must`) присутствуют в ответе.

Формат memory_bench.yaml:
    - q: "Что решили по доступу к API?"
      must: ["токен", "403"]

Сверка подстрокой: нормализация регистра и ё→е. Итог N/M и список провалов;
exit code 1, если провалов больше трети — заметная деградация.

    .venv/bin/python scripts/memory_bench.py            # весь бенч
    .venv/bin/python scripts/memory_bench.py --limit 3  # быстрый смок
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import yaml  # noqa: E402
from llm import LLM  # noqa: E402

SNIPPET = 1200   # как в боевом RAG приложения
LIMIT_FILES = 5


# Полноширинный ASCII (U+FF01–U+FF5E) — обычный: китайские модели пишут «９月»
# и «９月１日» наравне с «9月», и строгая сверка давала ложный провал на верном
# ответе (ревью 19.08, второй круг).
FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}


def norm(s: str) -> str:
    return s.lower().replace("ё", "е").translate(FULLWIDTH)


# Иероглифы пробелами не разделяются, поэтому «слова» из них не нарезать:
# запрос 支付服务商最后定了哪一家？ давал ПУСТОЙ список слов, поиск скатывался
# на поиск всей фразы целиком и не находил ничего (замер 19.08: 0/3 на
# китайском демо-графе против 2/3 на английском). Берём скользящие биграммы —
# стандартный приём для языков без пробелов: 支付服务商 → 支付, 付服, 服务, 务商.
CJK = (r"\u4e00-\u9fff"      # китайский, основной блок
       r"\u3400-\u4dbf"      # расширение A
       r"\uf900-\ufaff"      # совместимость (иероглифы из старых кодировок)
       r"\u3040-\u30ff"      # японские каны
       r"\uff66-\uff9f"      # полуширинная катакана
       r"\uac00-\ud7af"      # корейский хангыль
       r"\U00020000-\U0002ee5f"      # расширения B–I
       r"\U0002f800-\U0002fa1f")  # совместимость, дополнение


# Пробел рядом с иероглифом. Сжимать всю строку было нельзя: тогда
# contains("YuPay 支付", "Yu Pay 支付") давал True — латиница склеивалась заодно,
# и один случайный иероглиф рядом менял вердикт по совсем другому факту
# (ревью 19.08, второй круг).
CJK_SPACE = re.compile(f"(?<=[{CJK}])\\s+|\\s+(?=[{CJK}])")


def needles(query: str, stop: set[str]) -> tuple[list[str], list[str]]:
    """Иглы запроса: слова и биграммы иероглифов, каждая по одному разу.

    Дедуп обязателен: повтор удваивал вклад иглы в счёт («服务服务商» даёт
    «服务» дважды), и файл с одной частой биграммой обгонял релевантный.
    Пересечься списки не могут по построению — слова собираются из латиницы,
    кириллицы и цифр, граммы только из иероглифов.
    """
    words = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z0-9_-]{3,}", query)
             if norm(w) not in stop]
    return list(dict.fromkeys(words)), list(dict.fromkeys(cjk_grams(query)))


def contains(needle: str, text: str) -> bool:
    """Есть ли ожидаемый факт в ответе.

    Прямое вхождение — как раньше. Для иероглифов добавлена сверка по сжатой
    форме: модель расставляет пробелы между знаками произвольно («9 月 1 日»
    против «9月1日»), в китайском они не значимы, и строгая сверка давала
    ложный провал на верном ответе (замер 19.08). Русский и английский путь
    не меняется: сжатие включается только когда в ожидании есть иероглифы.
    """
    if norm(needle) in norm(text):
        return True
    if re.search(f"[{CJK}]", needle):
        return CJK_SPACE.sub("", norm(needle)) in CJK_SPACE.sub("", norm(text))
    return False


def cjk_grams(query: str) -> list[str]:
    """Биграммы из иероглифических кусков запроса (китайский, японский, корейский)."""
    grams: list[str] = []
    for run in re.findall(f"[{CJK}]+", query):
        if len(run) == 1:
            grams.append(run)
        else:
            grams += [run[i:i + 2] for i in range(len(run) - 1)]
    return grams


# Промпт синтеза на языке кейсов. Раньше он был только русским, и на
# английском/китайском демо-графе модель отвечала по-русски: кейс с «September»
# падал не потому, что факт потерян, а потому, что в ответе стояло «1 сентября»
# (замер 19.08 — по одному ложному провалу на каждом нерусском графе). Бенч
# обязан мерить то, что увидит пользователь на СВОЁМ языке.
SYNTH = {
    "ru": (
        "Вопрос: {q}\n\nФрагменты из архива встреч:\n{found}\n\n"
        "Ответь на вопрос по фрагментам: кратко, с конкретными фактами "
        "(имена, числа, идентификаторы) из фрагментов. Ничего не выдумывай.",
        "Ты — ассистент по архиву рабочих встреч. Только факты из фрагментов.",
    ),
    "en": (
        "Question: {q}\n\nFragments from the meeting archive:\n{found}\n\n"
        "Answer the question from the fragments: briefly, with the concrete "
        "facts (names, numbers, identifiers) they contain. Invent nothing. "
        "Answer in English.",
        "You are an assistant over an archive of work meetings. "
        "Only facts from the fragments. Answer in English.",
    ),
    "zh": (
        "问题：{q}\n\n会议档案片段：\n{found}\n\n"
        "请根据片段回答问题：简洁，并给出片段中的具体事实"
        "（姓名、数字、编号）。不要编造。请用中文回答。",
        "你是会议档案助手。只使用片段中的事实，请用中文回答。",
    ),
}


def resolve_lang(cfg, *, demo_zh: bool, demo_en: bool, demo: bool) -> str:
    """Язык промпта синтеза.

    Каждый демо-флаг называет язык своего графа сам: спрашивать русский
    демо-граф английским промптом бессмысленно, даже если в конфиге стоит `en`.
    В обычном прогоне язык берётся ОТТУДА ЖЕ, откуда его берёт приложение —
    `sufler.language`. Иначе владелец нерусского vault получал русский промпт,
    ответ на русском и ложные провалы ночной джобы (ревью 19.08, DeepSeek).

    `cfg` бывает `None`: пустой config.yaml проходит `yaml.safe_load` молча,
    и обращение к нему падало бы AttributeError вместо честной работы по
    умолчанию (ревью 19.08, второй круг, локальная голова).
    """
    if demo_zh:
        return "zh"
    if demo_en:
        return "en"
    if demo:
        return "ru"
    value = ((cfg or {}).get("sufler") or {}).get("language", "ru")
    lang = str(value).strip().lower()
    return lang if lang in SYNTH else "ru"


def search_brain(graph: pathlib.Path, query: str) -> str | None:
    """Боевой контур: vault_search на brain :8100 (тот же, что в приложении).

    Бенч обязан мерить то, что видит пользователь, а не свою копию
    алгоритма — иначе улучшения ранжирования в brain остаются незамеренными.
    None → сервер лежит, вызывающий уходит на локальный фолбэк.
    """
    import json
    import urllib.request

    folder = graph.name  # стандартная раскладка: vault/<граф>/…
    # nosemgrep — адрес локального brain/Ollama из конфига, не внешний ввод
    req = urllib.request.Request(
        "http://127.0.0.1:8100/vault_search",
        data=json.dumps({"query": query, "folder": folder,
                         "limit": LIMIT_FILES, "snippet_chars": SNIPPET},
                        ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        # nosemgrep — адрес локального brain/Ollama из конфига, не внешний ввод
        with urllib.request.urlopen(req, timeout=25) as resp:
            text = json.load(resp).get("text", "")
    except OSError:
        return None
    if text.startswith("Ничего не найдено"):
        return ""
    # граф вне vault brain-сервера (демо, другой диск): честный фолбэк
    # на локальный поиск, а не сообщение об ошибке в роли «сырья»
    if text.startswith("Папка не найдена") or text.startswith("Недопустимый путь"):
        return None
    # срезаем шапку «Найдено в vault (N из M):»
    _, _, body = text.partition("\n\n")
    return body or text


def search(graph: pathlib.Path, query: str) -> str:
    """Локальный фолбэк: та же механика, что vault_search: слова → скоринг файлов → сниппеты."""
    stop = {"что", "как", "где", "когда", "это", "нас", "есть", "про", "для",
            "или", "чем", "кто", "было", "быть", "по", "мы", "решили"}
    words, grams = needles(query, stop)
    words = words + grams
    rx = re.compile("|".join(re.escape(w) for w in words), re.I) if words else re.compile(re.escape(query), re.I)
    scored: list[tuple[int, str]] = []
    for p in graph.rglob("*.md"):
        if any(part.startswith(".") for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = rx.search(text)
        if not m:
            continue
        low = norm(text)
        rel = str(p.relative_to(graph))
        score = sum(1 for w in words if norm(w) in low)
        # Буст за попадание в ПУТЬ у биграмм слабее: двух иероглифов слишком
        # мало, чтобы считать совпадение с именем файла осмысленным — частая
        # биграмма («现在», «我们») иначе перевешивает редкое точное слово
        # (ревью 19.08, DeepSeek).
        score += sum(3 for w in words if w not in grams and norm(w) in norm(rel))
        score += sum(1 for w in grams if norm(w) in norm(rel))
        start = max(0, m.start() - 150)
        frag = " ".join(text[start:m.end() + SNIPPET].split())
        scored.append((score, f"• {rel}\n  …{frag}…"))
    scored.sort(key=lambda x: -x[0])
    return "\n\n".join(h for _, h in scored[:LIMIT_FILES])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="только первые N вопросов")
    ap.add_argument("--demo", action="store_true",
                    help="демо-граф из репозитория вместо вашего: проверка контура без встреч")
    ap.add_argument("--demo-en", action="store_true",
                    help="английский демо-граф (demo/graph_en) и английские кейсы")
    ap.add_argument("--demo-zh", action="store_true",
                    help="китайский демо-граф (demo/graph_zh) и китайские кейсы")
    args = ap.parse_args()

    cfg_path = ROOT / "config" / "config.yaml"
    if not cfg_path.exists() and (args.demo or args.demo_en or args.demo_zh):
        # демо-режим работает и до настройки: дефолтная модель Ollama
        cfg = {"llm": {"base_url": "http://127.0.0.1:11434", "model": "qwen3.5:4b"},
               "sufler": {"role": "Ассистент по архиву встреч."}}
    elif not cfg_path.exists():
        # Свежий клон репозитория: config.yaml личный и в git не лежит.
        # Раньше здесь падал FileNotFoundError, и ночная джоба показывала
        # трейсбек вместо «не настроено». Не настроено — не поломка.
        print(f"Чароит не настроен: нет {cfg_path.name}. "
              f"Скопируйте config.example.yaml и заполните свои значения, "
              f"либо запустите бенч на демо-графе: --demo")
        return
    else:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    lang = resolve_lang(cfg, demo_zh=args.demo_zh, demo_en=args.demo_en, demo=args.demo)
    if args.demo_zh:
        args.demo = True
        graph = ROOT / "demo" / "graph_zh"
        bench_file = ROOT / "config" / "memory_bench_demo_zh.yaml"
    elif args.demo_en:
        args.demo = True
        graph = ROOT / "demo" / "graph_en"
        bench_file = ROOT / "config" / "memory_bench_demo_en.yaml"
    elif args.demo:
        graph = ROOT / "demo" / "graph"
        bench_file = ROOT / "config" / "memory_bench_demo.yaml"
    else:
        graph = pathlib.Path(os.environ.get("SUFLER_GRAPH_DIR") or cfg["sufler"]["graph_dir"]).expanduser()
        bench_file = ROOT / "config" / "memory_bench.yaml"  # см. memory_bench.example.yaml
    if not bench_file.exists():
        # Не настроен — не то же самое, что провален. Раньше здесь был выход с
        # ошибкой, и ночная джоба каждую ночь печатала «БЕНЧ ПАМЯТИ ПРОСЕЛ» у
        # всех, кто бенч не заводил. Вечно горящее предупреждение приучает не
        # смотреть на предупреждения — той же болезнью болел CI до аудита.
        print(f"бенч памяти не настроен: нет {bench_file.name}. "
              f"Чтобы включить — скопируйте {bench_file.with_name('memory_bench.example.yaml').name} "
              f"и впишите свои вопросы")
        return
    cases = yaml.safe_load(bench_file.read_text(encoding="utf-8")) or []
    if args.limit:
        cases = cases[:args.limit]

    llm = LLM(cfg)
    passed, failures = 0, []
    # демо-граф живёт в репозитории, вне vault brain-сервера — только локальный
    brain_alive = (not args.demo) and search_brain(graph, "проверка") is not None
    print(f"контур поиска: {'brain :8100 (боевой)' if brain_alive else 'локальный фолбэк (brain лежит)'}")
    for i, case in enumerate(cases, 1):
        q, must = case["q"], case.get("must", [])
        found = (search_brain(graph, q) if brain_alive else None)
        if found is None:
            found = search(graph, q)
        if not found:
            failures.append((q, must, "поиск ничего не нашёл"))
            print(f"[{i}/{len(cases)}] ✗ {q} — поиск пуст")
            continue
        # диагностика: чей провал — ПОИСКА (факт не в выдаче) или СИНТЕЗА
        # (факт в выдаче, LLM не включил в ответ). Лечатся по-разному.
        retr_missing = [m for m in must if not contains(m, found)]
        prompt_tpl, system_msg = SYNTH[lang]
        answer = "".join(llm.stream(
            prompt_tpl.format(q=q, found=found),
            system=system_msg,
            temperature=0.0,  # бенч — регрессия, не творчество: убираем флап
        ))
        missing = [m for m in must if not contains(m, answer)]
        if missing:
            stage = "ПОИСК" if retr_missing else "синтез"
            failures.append((q, missing, f"[{stage}] " + answer[:160]))
            print(f"[{i}/{len(cases)}] ✗ {q} — нет: {missing} (этап: {stage})")
        else:
            passed += 1
            note = f" (в выдаче не было: {retr_missing})" if retr_missing else ""
            print(f"[{i}/{len(cases)}] ✓ {q}{note}")

    total = len(cases)
    print(f"\nИТОГ: {passed}/{total}")
    for q, missing, ctx in failures:
        print(f"  ✗ «{q}»: не найдено {missing}\n    ответ: {ctx}…")
    if total and passed < total * 2 / 3:
        sys.exit(1)   # деградация больше трети — сигнал в ночном логе


if __name__ == "__main__":
    main()

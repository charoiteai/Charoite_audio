#!/usr/bin/env python3
"""Диагностика Charoite: одна команда вместо гадания «почему молчит».

Две половины. Первая — установка: конфиг и его ключи, папка графа, Ollama и
нужные модели (включая bge-m3 для семантики), STT-модели, диаризация,
зависимости. Вторая — рабочее состояние: отвечает ли модель на самом деле,
не застряли ли встречи, сколько ждёт в папке импорта, есть ли место на диске.

Вторая половина появилась после 03.08. В тот день встречи перестали
раскладываться по папкам, и на выяснение причины ушёл час: Ollama отвечала на
`/api/tags` мгновенно, модель числилась загруженной, а инференс стоял — запрос
висел десять минут и уходил с таймаутом. Все проверки для этого уже были
написаны, но лежали по разным местам, и ни одна не собиралась в один ответ.

Ничего не чинит сам — печатает точный следующий шаг для каждой проблемы.

    .venv/bin/python scripts/doctor.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import platform
import shlex
import subprocess
import sys
import time

# Байткод — до первого импорта своих модулей: доктор обещан любому Python, в том
# числе python бандла из Терминала, мимо PYTHONPYCACHEPREFIX приложения, — и
# `__pycache__` лёг бы в подписанный `.app` (предрелизный прогон 0.88.1, Opus C1).
sys.dont_write_bytecode = True
# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py. Вставка —
# только чтобы импортировать сам канон.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from charoite_paths import code_root, free_bytes, harden_umask, resolve_root  # noqa: E402
import diarize_nemotron  # noqa: E402 — только проба движка его интерпретатором, mlx сюда не попадает

CODE = code_root(__file__)


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте.

    Подмена в тестах — через окружение (`CHAROITE_ROOT`) или публичную дверь
    канона `use_data_root`, не через глобал модуля: второго ответа на вопрос
    «где корень» здесь быть не должно (№338).
    """
    return resolve_root(__file__)


OK, WARN, FAIL = "✓", "–", "✗"
issues = 0
llm_url_refused = False   # об отказе политики говорим один раз, а не в каждой секции


def line(mark: str, what: str, hint: str = "") -> None:
    global issues
    if mark == FAIL:
        issues += 1
    print(f" {mark} {what}" + (f"\n     → {hint}" if hint and mark != OK else ""))


def llm_url(cfg: dict) -> str | None:
    """Адрес LLM — только через privacy.llm_base_url, как и везде в проекте.

    Диагностика читала `llm.base_url` из конфига сама и слала на этот адрес
    запрос. Дыра та же, что закрывал privacy.py: чужой адрес в конфиге —
    и `doctor` уходит наружу при выключенном облаке и под рубильником, да
    ещё и рапортует «✓ Ollama», подтверждая владельцу, что всё локально.
    Сторож `test_no_reader_bypasses_privacy` этого не видел: он смотрел
    только `src/`, а `scripts/` не смотрел вовсе.

    Отказ политики здесь — не крах, а диагноз: у `doctor` работа как раз
    в том, чтобы назвать проблему и следующий шаг. Причина одна, поэтому и
    строка одна: адрес спрашивают две секции, и без памятки один запрет
    печатался бы дважды, а счётчик проблем показывал бы две.
    """
    global llm_url_refused
    sys.path.insert(0, str(CODE / "src"))
    try:
        import privacy
    except Exception as e:  # noqa: BLE001
        line(FAIL, f"src/privacy.py не читается ({type(e).__name__})",
             "без него нельзя решить, законен ли адрес LLM — проверьте src/privacy.py")
        return None
    try:
        engine = privacy.llm_engine(cfg)
        if privacy.cloud_engine_active(cfg):
            # На облачной установке локальной чат-модели может не быть вовсе:
            # проверять адрес Ollama и советовать «ollama pull» — вредный
            # совет, он ведёт перезапускать сервис, который держит эмбеддер
            # (круг-2: GLM I5, DS M1).
            return privacy.cloud_llm_url(cfg)
        if engine == "mlx-server":
            return privacy.mlx_base_url(cfg)
        return privacy.llm_base_url(cfg)
    except RuntimeError as e:
        if not llm_url_refused:
            llm_url_refused = True
            line(FAIL, "адрес LLM запрещён политикой приватности", str(e))
        return None


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 11):
        line(OK, f"Python {v.major}.{v.minor}")
    else:
        line(FAIL, f"Python {v.major}.{v.minor}", "нужен 3.11+ (README → Requirements)")


def check_config() -> dict:
    cfg_path = _root() / "config" / "config.yaml"
    if not cfg_path.exists():
        line(FAIL, "config/config.yaml отсутствует",
             "cp config/config.example.yaml config/config.yaml и заполните user_name/graph_dir")
        return {}
    try:
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        line(FAIL, f"config.yaml не читается: {e}", "проверьте YAML-синтаксис")
        return {}
    line(OK, "config/config.yaml")
    suf = cfg.get("sufler", {})
    if not suf.get("user_name"):
        line(WARN, "sufler.user_name пуст", "суфлёр не сможет отличать вас от собеседников")
    sys.path.insert(0, str(CODE / "src"))
    import graphs
    raw = str(suf.get("graph_dir", "") or "").strip()
    gdir = graphs.graph_dir(cfg)
    override = graphs.env_override()
    if override:
        line(WARN, f"graph_dir перекрыт переменной окружения: {gdir}",
             "конвейер пишет туда, а не в sufler.graph_dir из конфига")
    elif gdir is None:
        line(WARN, "sufler.graph_dir пуст",
             "граф не будет писаться; для пробы: demo/graph, а проверить весь "
             "контур — scripts/memory_bench.py --demo")
    elif not gdir.exists():
        line(FAIL, f"graph_dir не существует: {gdir}", "создайте папку или поправьте путь")
    else:
        n = sum(1 for _ in gdir.rglob("*.md"))
        # Относительный путь и приложение, и Python считают от папки данных
        # (единая точка src/graphs.py, карточка №36) — показываем, от чего.
        note = f" (относительно {graphs.data_root()})" if not pathlib.Path(raw).expanduser().is_absolute() else ""
        line(OK, f"graph_dir: {gdir} ({n} заметок){note}")
    return cfg


def _tagged(name: str) -> str:
    """Полное имя модели с тегом: Ollama держит `nomic` и `nomic:latest` за
    разные имена, и сверка по префиксу хвалила бы модель, которой сервер
    ответит 404 (круг 1 по коду, GLM M5; круг 2 — то же для чат-модели)."""
    return name if ":" in name else f"{name}:latest"


def check_ollama(cfg: dict) -> None:
    # На облачном движке чат-модели локально нет по замыслу, но Ollama всё
    # равно нужна: на ней живут эмбеддинги. Проверяем её напрямую и не
    # требуем llm.model — иначе доктор советует докачать 20 ГБ, от которых
    # облачный режим и должен был избавить (круг-2: GLM I5, DS M1).
    sys.path.insert(0, str(CODE / "src"))
    import privacy as _privacy
    from charoite_graph.net import open_url
    cloud = _privacy.cloud_engine_active(cfg)
    base = _privacy.llm_base_url(cfg) if cloud else llm_url(cfg)
    if base is None:
        return
    try:
        # base выдан privacy.llm_base_url: либо loopback, либо явно
        # разрешённый владельцем адрес; не пользовательский ввод.
        # nosemgrep
        with open_url(f"{base}/api/tags", timeout=4) as r:
            models = [m.get("name", "") for m in json.load(r).get("models", [])]
    except OSError:
        line(FAIL, f"Ollama не отвечает ({base})",
             "установите с ollama.com и запустите; либо поправьте llm.base_url")
        return
    line(OK, f"Ollama ({len(models)} моделей)")
    main = "" if cloud else str(cfg.get("llm", {}).get("model", ""))
    if cloud:
        line(OK, "чат идёт через облачный шлюз — локальная чат-модель не нужна")
    if main and _tagged(main) not in models:
        line(FAIL, f"модель llm.model «{main}» не найдена", f"ollama pull {main}")
    elif main:
        line(OK, f"основная модель: {main}")
    # Имя спрашиваем у шва способности, а не у литерала: владелец вправе
    # поставить другую модель в `sufler.embed_model`, и доктор про неё обязан
    # знать — иначе он ругается на отсутствие той, которой никто не пользуется,
    # и хвалит ту, которой не считает. Модуль шва без зависимостей: доктор
    # обязан печатать рецепт и на машине, где ещё нечем ходить в сеть.
    from charoite_graph import model_seam as _seam
    want = _seam.embed_model_name(cfg)
    # Сверяем с тегом: `nomic` и `nomic-embed-text:latest` — разные имена для
    # Ollama, и по префиксу доктор похвалил бы модель, которой `/api/embed`
    # ответит 404 (круг 1 по коду, GLM M5).
    if _tagged(want) in models:
        line(OK, f"{want} (семантический поиск)")
    else:
        line(WARN, f"{want} не установлена — поиск будет чисто лексическим",
             f"ollama pull {want}")


def check_stt(cfg: dict) -> None:
    """Выбранный бэкенд распознавания должен иметь чем распознавать.

    gigaam, parakeet и whisper тянут веса сами при первом запуске, а
    SenseVoice — файл, который ставится отдельной командой. Без этой проверки
    человек, выбравший `sensevoice` по совету docs/MODELS.md, узнавал о
    недостающей модели от демона в момент старта встречи.
    """
    backend = str((cfg.get("stt") or {}).get("backend", "")).strip()
    if backend != "sensevoice":
        return
    model = pathlib.Path((cfg.get("stt") or {}).get(
        "sensevoice_model", "models/stt/sensevoice.onnx"))
    if not model.is_absolute():
        model = _root() / model
    if model.exists() and model.with_name("tokens.txt").exists():
        line(OK, f"распознавание: {model.name} (SenseVoice)")
    else:
        line(FAIL, "stt.backend: sensevoice, но модели нет",
             ".venv/bin/python scripts/get_models.py --stt sensevoice — "
             "228 МБ, качается один раз")


def check_models() -> None:
    """Набор голосов — два файла. Одних эмбеддингов мало.

    Без сегментации живая разметка ещё работает в упрощённом режиме, а
    пересборка после встречи не размечает голоса заново. Размер здесь не
    смотрим: доктор, как и раньше, отвечает на «файл есть».
    """
    folder = _root() / "models" / "diar"
    emb = folder / "embedding.onnx"
    seg = folder / "segmentation.onnx"
    recipe = ".venv/bin/python scripts/get_models.py --diar"
    if emb.exists() and seg.exists():
        line(OK, "диаризация: models/diar/embedding.onnx, models/diar/segmentation.onnx")
        return
    if emb.exists():
        line(WARN,
             "диаризация: нет models/diar/segmentation.onnx — "
             "голоса после встречи не размечаются заново",
             recipe)
        return
    if seg.exists():
        line(WARN, "диаризация: нет models/diar/embedding.onnx", recipe)
        return
    line(WARN,
         "диаризации нет (метки «Собеседник N» будут по каналам): "
         "нет models/diar/embedding.onnx и models/diar/segmentation.onnx",
         recipe)


def check_engine(cfg: dict) -> None:
    """Движок диаризации — строкой из `engine_state` (№474, №622 B2).

    Без сети и без mlx в процессе доктора. Выбран Nemotron — проба его же
    интерпретатором, как раньше. Выбран sherpa на Apple Silicon — информационная
    строка: окружение и веса на месте, просто не включён, или их нет, и тогда с
    командой. Неизвестный `diarize_backend` — своя строка без совета ставить.
    Живой поток не `off`, а движку нечем работать — предупреждение: он молча не
    поднимется. Не готов — не авария: пересборка размечает голоса sherpa и пишет
    причину в шапку стенограммы, поэтому «–», а не «✗»."""
    root = _root()
    state = diarize_nemotron.engine_state(root, cfg)
    if state["backend"] == "nemotron":
        _check_nemotron_engine(cfg, root, state)
    elif state["backend"] in diarize_nemotron.ENGINE_BACKENDS:
        _check_sherpa_engine(root, state)
    else:
        # Неизвестный ключ (опечатка) — своя строка без совета ставить движок:
        # ставить нечего, пока ключ не исправлен; размечает всё равно sherpa.
        line(WARN, f"ключ sufler.diarize_backend: {state['backend']!r} неизвестен — голоса после "
                   f"встречи размечает sherpa; допустимые: {', '.join(diarize_nemotron.ENGINE_BACKENDS)}")
    if state["live_mode"] != "off" and not _engine_ready(state):
        line(WARN, f"живой поток Nemotron включён ({state['live_mode']}), а движок не готов",
             f"поток не поднимется — {diarize_nemotron.install_command(root)}")


def _engine_ready(state: dict) -> bool:
    """Окружение и веса на месте (пробы тут нет): по этому доктор отличает «не включён» от «не готов»."""
    return state["environment"] and state["weights"] and not state["interpreter_reason"]


def _check_nemotron_engine(cfg: dict, root: pathlib.Path, state: dict) -> None:
    """Выбран Nemotron: проба — настоящая, как и была; причина и совет — из состояния.

    `cfg` не читается намеренно: форму конфига уже нормализовал `engine_state`, и
    значение `sufler.nemotron_python` приходит в `state` — знание о форме живёт
    в одном месте. `cfg` остаётся в подписи ради вызывающего, но не трогается:
    строка вместо `sufler` здесь больше не роняет доктора (№622 B2, часть 1)."""
    setting = state["nemotron_python"]
    python, refusal = state["interpreter"], state["interpreter_reason"]
    out = diarize_nemotron.probe_in_env(setting, root=root)
    if out.ok:
        line(OK, f"Nemotron: mlx-audio {out.payload['mlx_audio']}, веса на месте, интерпретатор {python}")
        return
    # Команду установщика несёт один текст. При пустом ключе отказ «нечем работать»
    # (UNAVAILABLE) приходит из пробы уже с ней — второй раз не дописываем (№489); при
    # заданном ключе проба команду не даёт: установщик ключ не вылечит, лечит правка ключа.
    command = diarize_nemotron.install_command(root)
    if refusal:          # окружения нет — отказ пробы уже несёт команду установщика
        advice = ""
    elif setting.strip():  # ключ главнее установленного окружения: переустановка его не заменит
        advice = ("ключ sufler.nemotron_python главнее установленного окружения — исправьте или "
                  f"очистите его; окружение ставит {command}")
    elif out.kind == "unavailable":  # установленное окружение неполно — команда пришла с причиной
        advice = ""
    else:                # установленное окружение есть, но падает
        advice = f"переставить окружение: {command}"
    line(WARN, "Nemotron выбран, но не готов — голоса после встречи размечает sherpa",
         out.reason + (f"; {advice}" if advice else ""))


def _check_sherpa_engine(root: pathlib.Path, state: dict) -> None:
    """Выбран sherpa: движок Nemotron только на Apple Silicon — на других машинах молчим."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return
    command = diarize_nemotron.install_command(root)
    if _engine_ready(state):
        line(WARN, "Nemotron-движок стоит, но не включён — голоса после встречи размечает sherpa",
             f"включить: sufler.diarize_backend: nemotron; либо {command}")
    else:
        line(WARN, "Nemotron-движок не стоит — голоса после встречи размечает sherpa",
             f"поставить: {command}")


def check_deps() -> None:
    missing = []
    for mod in ("yaml", "requests", "numpy", "sounddevice", "onnx_asr"):
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    if missing:
        line(FAIL, f"не хватает пакетов: {', '.join(missing)}",
             ".venv/bin/pip install . (и запускайте через .venv/bin/python)")
    else:
        line(OK, "python-зависимости")


def check_llm_alive(cfg: dict) -> bool | str | None:
    """Отвечает ли модель на самом деле.

    Проверка выше спрашивает список моделей — у вставшей Ollama он приходит
    мгновенно. Отличить работающий инференс от замершего может только
    генерация: 03.08 разница между этими двумя вопросами стоила разбора
    встречи и часа поисков.

    Возвращает исход пробы (`llm_health.probe`) на КАЖДОМ выходе — его
    спрашивает `check_strict_json`; `None` — адрес отвергнут политикой или
    модуль пробы не читается (причина уже напечатана).
    """
    base = llm_url(cfg)
    if base is None:
        return None                 # адрес уже отвергнут выше, диагноз назван
    sys.path.insert(0, str(CODE / "src"))
    try:
        import llm_health
    except Exception as e:  # noqa: BLE001 — модуль вспомогательный
        line(WARN, f"проба генерации недоступна ({type(e).__name__})")
        return None
    started = time.monotonic()
    state = llm_health.probe(cfg, timeout=90)
    if state is True:
        line(OK, f"модель отвечает ({time.monotonic() - started:.1f} с)")
        return state
    if state == llm_health.BUSY:
        # 503/429: сервер жив, модель занята другим запросом (пересборка,
        # ночной цикл, соседняя встреча) — перезапуск тут навредил бы
        line(WARN, "модель занята другим запросом (сервер жив, ответил 503/429)",
             "перезапускать не нужно: дождитесь конца разбора встречи или "
             "ночного цикла — конвейер сам ждёт занятую модель")
        return state
    if state == llm_health.MISSING:
        line(FAIL, "сервер отвечает, а модели из конфига на нём нет (HTTP 404)",
             "установите модель (ollama pull …) или поправьте llm.model — "
             "перезапуск сервера тут не поможет")
        return state
    if state == llm_health.SLOW:
        line(WARN, "сервер на связи, но генерация не ответила за 90 с",
             "модель может быть занята длинной генерацией (разбор встречи, ночной "
             "цикл); конвейер ждёт до 5 минут (проба 120 с и ожидание 180 с) и "
             "не перезапускает сервер под своей живой генерацией; застряло "
             "наверняка — scripts/doctor.py --restart-llm")
        return state
    sys.path.insert(0, str(CODE / "src"))
    import privacy as _privacy
    if _privacy.cloud_engine_active(cfg):
        line(FAIL, "облачный шлюз не отвечает",
             "проверьте сеть, llm.cloud_base_url, имя модели и ключ в "
             "llm.cloud_key_file; локальную Ollama перезапускать не нужно — "
             "на ней живут эмбеддинги")
        return state
    port_owner = llm_health.listener_path(base)
    line(FAIL, "модель не отвечает на генерацию"
               + (f" (порт держит {port_owner})" if port_owner else ""),
         "инференс встал: перезапустите Ollama. Конвейер сделает это сам при "
         "следующей встрече — см. src/llm_health.py")
    return state


def check_strict_json(cfg: dict, alive: bool | str | None) -> None:
    """Умеет ли сервер строгий JSON — проба пробой, вердикт дверью `llm`.

    Порядок решает, что вообще спрашивать: адрес отвергнут или проба живости
    не дошла — молчим (причина уже названа); mlx-server и облако строгого
    JSON не обещают — одна строка, без запроса; модель занята/не найдена/
    молчит — проверять нечего, и запрос не шлём; и только живой Ollama
    получает дешёвую пробу `/api/chat` с `format`. Текст строки — из двери
    (`llm.strict_json_sentence`), один на stderr и доктора.
    """
    sys.path.insert(0, str(CODE / "src"))
    try:
        import llm
        import llm_health
        import privacy
    except Exception:  # noqa: BLE001 — нет requests и прочего: строки о строгом JSON нет
        return
    base = llm_url(cfg)
    if base is None:
        return                      # адрес уже отвергнут выше, диагноз назван
    if alive is None:
        return                      # проба живости не дошла — причина уже напечатана
    engine = privacy.llm_engine(cfg)
    if engine == "mlx-server" or privacy.cloud_engine_active(cfg):
        line(WARN, f"строгий JSON не проверяется на движке {engine}")
        return
    if alive is not True:
        words = {
            llm_health.BUSY: "модель занята",
            llm_health.SLOW: "модель отвечает медленно",
            llm_health.MISSING: "модель не найдена",
        }.get(alive, "модель не отвечает")
        line(WARN, f"строгий JSON не проверен: {words}")
        return
    model = str((cfg.get("llm") or {}).get("model") or "")
    verdict, reason = llm_health.strict_json(base, model)
    if verdict == llm.STRICT_YES:
        line(OK, "строгий JSON: есть")
    elif verdict == llm.STRICT_NO:
        line(WARN, llm.strict_json_sentence(model, base, reason),
             "движок и модели — docs/MODELS.md")
    else:
        line(WARN, f"строгий JSON не проверен — {reason}")


def check_pipeline() -> None:
    """Не застряли ли встречи и сколько обычно занимает обработка."""
    sys.path.insert(0, str(CODE / "src"))
    try:
        from meeting_processing import MeetingStatusStore
    except Exception as e:  # noqa: BLE001
        line(WARN, f"статусы встреч недоступны ({type(e).__name__})")
        return
    store = MeetingStatusStore(_root())
    if not store.directory.exists():
        line(WARN, "статусов встреч ещё нет — обработка ни разу не запускалась")
        return
    pending = store.unfinished()
    if pending:
        names = ", ".join(d["meeting_id"] for d in pending[:3])
        line(WARN, f"не доехали до графа: {len(pending)} ({names})",
             "следующая удачная встреча подберёт их сама; вручную — "
             f"CHAROITE_ROOT={shlex.quote(str(_root()))} "
             ".venv/bin/python src/rebuild_transcript.py transcripts/<файл>.md")
    else:
        line(OK, "незавершённых встреч нет")
    typical = store.typical_duration()
    if typical:
        line(OK, f"обработка обычно занимает ~{round(typical / 60)} мин")


def _import_dir(cfg: dict) -> str:
    """Где папка импорта. Её задают в приложении, а не в конфиге.

    macOS-приложение хранит путь в своих настройках (`charoite.importDir`),
    поэтому один только config.yaml тут ничего не знает — а проверять надо
    именно ту папку, за которой следит приложение.
    """
    raw = str((cfg.get("charoite") or cfg.get("sufler") or {}).get("importDir", "")).strip()
    if raw:
        return raw
    try:
        out = subprocess.run(["defaults", "read", "ai.charoite.app", "charoite.importDir"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def check_import_queue(cfg: dict) -> None:
    """Папка импорта: что легло и ждёт."""
    raw = _import_dir(cfg)
    if not raw:
        return
    folder = pathlib.Path(raw).expanduser()
    if not folder.exists():
        line(FAIL, f"папка импорта не существует: {folder}",
             "проверьте charoite.importDir в config.yaml")
        return
    waiting = [p for p in folder.iterdir()
               if p.is_file() and p.suffix.lower() in
               {".m4a", ".wav", ".mp3", ".caf", ".txt", ".md", ".vtt", ".srt"}]
    if waiting:
        line(WARN, f"в папке импорта ждёт файлов: {len(waiting)}",
             "их разберёт наблюдатель импорта; если он не запущен — "
             f"CHAROITE_ROOT={shlex.quote(str(_root()))} "
             ".venv/bin/python scripts/import_meeting.py --scan <папка>")
    else:
        line(OK, "папка импорта пуста")


def check_disk() -> None:
    """Место под модели и записи.

    Пороги 5 и 20 ГБ отвечают на вопрос «поместятся ли модели», не «хватит ли
    этой встречи»: четырёхчасовой порог живёт у записи. Мера одна —
    `free_bytes`, та же, что перед открытием `.pcm`.
    """
    free = free_bytes(_root()) / 1e9
    if free < 5:
        line(FAIL, f"на диске {free:.1f} ГБ",
             "записи и модели не поместятся — освободите место")
    elif free < 20:
        line(WARN, f"на диске {free:.1f} ГБ — хватит на несколько встреч")
    else:
        line(OK, f"на диске {free:.0f} ГБ")


def restart_llm() -> None:
    """`--restart-llm`: аварийный перезапуск сервера моделей поверх живых аренд.

    Конвейер сам никогда не убивает сервер под своей генерацией (аренда
    модели, src/model_lease.py); это единственный ручной выход, когда
    владелец аренды завис так, что его порог зависания не срабатывает."""
    cfg = check_config()
    base = llm_url(cfg)
    if base is None:
        sys.exit(1)
    sys.path.insert(0, str(CODE / "src"))
    import llm_health
    if not llm_health.force_restart(cfg, print):
        line(FAIL, "перезапуск не удался", "см. строки выше: адрес не локальный, команда перезапуска или владелец порта")
        sys.exit(1)
    # «перезапуск состоялся» = проба ответила, а не subprocess вернул 0: Ollama.app
    # поднимается 10–30 с, и рапорт сразу после команды врал бы (круг 2 GLM M2)
    deadline = time.monotonic() + llm_health.RESTART_WAIT
    state = None
    while time.monotonic() < deadline:
        time.sleep(llm_health.RESTART_POLL)
        state = llm_health.probe(cfg, timeout=60)
        if state is True or state == llm_health.BUSY:
            break
    if state is True or state == llm_health.BUSY:
        line(OK, f"сервер моделей перезапущен, проба ответила ({time.monotonic() - deadline + llm_health.RESTART_WAIT:.0f} с)")
        sys.exit(0)
    line(FAIL, f"команды перезапуска отданы, но проба не ответила за {llm_health.RESTART_WAIT} с",
         "смотрите лог сервера моделей; повторный --restart-llm не раньше, чем он поднимется")
    sys.exit(1)


def main() -> None:
    harden_umask()   # --restart-llm создаёт logs/mlx_server.log сервера моделей — только владельцу
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--restart-llm", action="store_true",
                    help="аварийный перезапуск сервера моделей поверх живых аренд "
                         "(единственный ручной выход при зависшем владельце порта)")
    args = ap.parse_args()
    if args.restart_llm:
        restart_llm()
        return
    print("Charoite doctor\n")
    print("Установка")
    check_python()
    check_deps()
    cfg = check_config()
    check_ollama(cfg)
    check_stt(cfg)
    check_models()
    check_engine(cfg)
    print("\nРабочее состояние")
    alive = check_llm_alive(cfg)
    check_strict_json(cfg, alive)
    check_pipeline()
    check_import_queue(cfg)
    check_disk()
    print()
    if issues:
        print(f"Проблем: {issues}. Пункты с «✗» чинить обязательно, с «–» — по желанию.")
        sys.exit(1)
    print("Всё на месте. Запускайте: ./app/make_app.sh или "
          f"CHAROITE_ROOT={shlex.quote(str(_root()))} .venv/bin/python src/main.py")


if __name__ == "__main__":
    main()

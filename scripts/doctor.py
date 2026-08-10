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

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or
                    pathlib.Path(__file__).resolve().parent.parent).expanduser()

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
    sys.path.insert(0, str(ROOT / "src"))
    try:
        import privacy
    except Exception as e:  # noqa: BLE001
        line(FAIL, f"src/privacy.py не читается ({type(e).__name__})",
             "без него нельзя решить, законен ли адрес LLM — проверьте src/privacy.py")
        return None
    try:
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
    cfg_path = ROOT / "config" / "config.yaml"
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
    gdir = pathlib.Path(str(suf.get("graph_dir", ""))).expanduser()
    if not suf.get("graph_dir"):
        line(WARN, "sufler.graph_dir пуст",
             "граф не будет писаться; для пробы: demo/graph, а проверить весь "
             "контур — scripts/memory_bench.py --demo")
    elif not gdir.exists():
        line(FAIL, f"graph_dir не существует: {gdir}", "создайте папку или поправьте путь")
    else:
        n = sum(1 for _ in gdir.rglob("*.md"))
        line(OK, f"graph_dir: {gdir} ({n} заметок)")
    return cfg


def check_ollama(cfg: dict) -> None:
    base = llm_url(cfg)
    if base is None:
        return
    try:
        # base выдан privacy.llm_base_url: либо loopback, либо явно
        # разрешённый владельцем адрес; не пользовательский ввод.
        # nosemgrep
        with urllib.request.urlopen(f"{base}/api/tags", timeout=4) as r:
            models = [m.get("name", "") for m in json.load(r).get("models", [])]
    except OSError:
        line(FAIL, f"Ollama не отвечает ({base})",
             "установите с ollama.com и запустите; либо поправьте llm.base_url")
        return
    line(OK, f"Ollama ({len(models)} моделей)")
    main = str(cfg.get("llm", {}).get("model", ""))
    if main and not any(m.startswith(main.split(":")[0]) for m in models):
        line(FAIL, f"модель llm.model «{main}» не найдена", f"ollama pull {main}")
    elif main:
        line(OK, f"основная модель: {main}")
    if any(m.startswith("bge-m3") for m in models):
        line(OK, "bge-m3 (семантический поиск)")
    else:
        line(WARN, "bge-m3 не установлена — поиск будет чисто лексическим",
             "ollama pull bge-m3   # ~1.2 ГБ")


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
        model = ROOT / model
    if model.exists() and model.with_name("tokens.txt").exists():
        line(OK, f"распознавание: {model.name} (SenseVoice)")
    else:
        line(FAIL, "stt.backend: sensevoice, но модели нет",
             ".venv/bin/python scripts/get_models.py --stt sensevoice — "
             "228 МБ, качается один раз")


def check_models() -> None:
    diar = ROOT / "models" / "diar" / "embedding.onnx"
    if diar.exists():
        line(OK, "диаризация: models/diar/embedding.onnx")
    else:
        line(WARN, "диаризации нет (метки «Собеседник N» будут по каналам)",
             ".venv/bin/python scripts/get_models.py --diar — поставит модель "
             "(варианты: --list, подробности: docs/DIARIZATION.md)")


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


def check_llm_alive(cfg: dict) -> None:
    """Отвечает ли модель на самом деле.

    Проверка выше спрашивает список моделей — у вставшей Ollama он приходит
    мгновенно. Отличить работающий инференс от замершего может только
    генерация: 03.08 разница между этими двумя вопросами стоила разбора
    встречи и часа поисков.
    """
    base = llm_url(cfg)
    if base is None:
        return                      # адрес уже отвергнут выше, диагноз назван
    sys.path.insert(0, str(ROOT / "src"))
    try:
        import llm_health
    except Exception as e:  # noqa: BLE001 — модуль вспомогательный
        line(WARN, f"проба генерации недоступна ({type(e).__name__})")
        return
    started = time.monotonic()
    if llm_health.probe(cfg, timeout=90):
        line(OK, f"модель отвечает ({time.monotonic() - started:.1f} с)")
        return
    port_owner = llm_health.listener_path(base)
    line(FAIL, "модель не отвечает на генерацию"
               + (f" (порт держит {port_owner})" if port_owner else ""),
         "инференс встал: перезапустите Ollama. Конвейер сделает это сам при "
         "следующей встрече — см. src/llm_health.py")


def check_pipeline() -> None:
    """Не застряли ли встречи и сколько обычно занимает обработка."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from meeting_processing import MeetingStatusStore
    except Exception as e:  # noqa: BLE001
        line(WARN, f"статусы встреч недоступны ({type(e).__name__})")
        return
    store = MeetingStatusStore(ROOT)
    if not store.directory.exists():
        line(WARN, "статусов встреч ещё нет — обработка ни разу не запускалась")
        return
    pending = store.unfinished()
    if pending:
        names = ", ".join(d["meeting_id"] for d in pending[:3])
        line(WARN, f"не доехали до графа: {len(pending)} ({names})",
             "следующая удачная встреча подберёт их сама; вручную — "
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
             ".venv/bin/python scripts/import_meeting.py --scan <папка>")
    else:
        line(OK, "папка импорта пуста")


def check_disk() -> None:
    """Место под записи: час встречи в двух каналах — это сотни мегабайт."""
    free = shutil.disk_usage(ROOT).free / 1e9
    if free < 5:
        line(FAIL, f"на диске {free:.1f} ГБ",
             "записи и модели не поместятся — освободите место")
    elif free < 20:
        line(WARN, f"на диске {free:.1f} ГБ — хватит на несколько встреч")
    else:
        line(OK, f"на диске {free:.0f} ГБ")


def main() -> None:
    print("Charoite doctor\n")
    print("Установка")
    check_python()
    check_deps()
    cfg = check_config()
    check_ollama(cfg)
    check_stt(cfg)
    check_models()
    print("\nРабочее состояние")
    check_llm_alive(cfg)
    check_pipeline()
    check_import_queue(cfg)
    check_disk()
    print()
    if issues:
        print(f"Проблем: {issues}. Пункты с «✗» чинить обязательно, с «–» — по желанию.")
        sys.exit(1)
    print("Всё на месте. Запускайте: ./app/make_app.sh или .venv/bin/python src/main.py")


if __name__ == "__main__":
    main()

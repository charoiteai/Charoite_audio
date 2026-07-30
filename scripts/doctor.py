#!/usr/bin/env python3
"""Диагностика установки Charoite: одна команда вместо гадания «почему молчит».

Проверяет всё, обо что реально спотыкаются при первом запуске: конфиг и его
ключи, папку графа, Ollama и нужные модели (включая bge-m3 для семантики),
STT-модели, диаризацию, зависимости. Ничего не чинит сам — печатает точный
следующий шаг для каждой проблемы.

    .venv/bin/python scripts/doctor.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

OK, WARN, FAIL = "✓", "–", "✗"
issues = 0


def line(mark: str, what: str, hint: str = "") -> None:
    global issues
    if mark == FAIL:
        issues += 1
    print(f" {mark} {what}" + (f"\n     → {hint}" if hint and mark != OK else ""))


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
    base = str(cfg.get("llm", {}).get("base_url", "http://127.0.0.1:11434")).rstrip("/")
    try:
        # nosemgrep — base из локального конфига (свой Ollama), не пользовательский ввод
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
             ".venv/bin/pip install -r requirements.txt (и запускайте через .venv/bin/python)")
    else:
        line(OK, "python-зависимости")


def main() -> None:
    print("Charoite doctor\n")
    check_python()
    check_deps()
    cfg = check_config()
    check_ollama(cfg)
    check_models()
    print()
    if issues:
        print(f"Проблем: {issues}. Пункты с «✗» чинить обязательно, с «–» — по желанию.")
        sys.exit(1)
    print("Всё на месте. Запускайте: ./app/make_app.sh или .venv/bin/python src/main.py")


if __name__ == "__main__":
    main()

"""Три находки аудита 0.46.0, у которых починка в одну строку, а цена — нет.

Собраны вместе потому, что у них общая форма: код делает шаг, который в
хорошую погоду незаметен, а в плохую стоит документа встречи, куска канала
или команды интерфейса. Ни одна из трёх не ловилась ничем.
"""
from __future__ import annotations

import ast
import io
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# --- MCP: пустой ответ модели не должен затирать готовые минутки -------------

class _Resp:
    def __init__(self, status: int, payload, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.mark.parametrize("resp", [
    _Resp(404, {"error": "model not found"}, "model not found"),
    _Resp(200, {"error": "model not found"}),
    _Resp(200, {"message": {"content": "   \n  "}}),
    _Resp(200, ValueError("не JSON")),
])
def test_неудача_модели_не_затирает_готовые_минутки(tmp_path, monkeypatch, resp):
    """Инструмент писал ответ модели поверх минуток БЕЗУСЛОВНО.

    Сценарий: встреча закончена, кнопка «Протокол» уже записала финальные
    минутки. Вечером человек просит в Claude Code «перегенерируй»; модель к
    этому времени переименована или удалена — Ollama отвечает ошибкой,
    `.get("message", {})` превращает её в пустую строку, и файл обнуляется.
    Инструмент при этом рапортует «Минутки сохранены», а пустышку дальше
    копирует архив. Документ встречи потерян до ручного повторного прогона.
    """
    import mcp_server

    live = tmp_path / "2026-08-07_181500.md"
    live.write_text("стенограмма", encoding="utf-8")
    minutes = tmp_path / "2026-08-07_181500_minutes.md"
    minutes.write_text("# Минутки\n- **Инженер** — выпустить релиз — пятница\n",
                       encoding="utf-8")
    before = minutes.read_text(encoding="utf-8")

    monkeypatch.setattr(mcp_server, "_latest", lambda: live)
    monkeypatch.setattr(mcp_server.requests, "post", lambda *a, **k: resp)

    answer = mcp_server.sufler_make_minutes()

    assert minutes.read_text(encoding="utf-8") == before, (
        "готовые минутки затёрты неудачным ответом модели")
    assert "НЕ тронуты" in answer, (
        f"инструмент отчитался успехом при неудаче модели: {answer!r}")


def test_удачный_ответ_минутки_пишет(tmp_path, monkeypatch):
    """Обратная сторона: страховка не должна превратиться в отказ работать."""
    import mcp_server

    live = tmp_path / "2026-08-07_181500.md"
    live.write_text("стенограмма", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_latest", lambda: live)
    monkeypatch.setattr(mcp_server.requests, "post",
                        lambda *a, **k: _Resp(200, {"message": {"content": "# Минутки\n- пункт"}}))

    answer = mcp_server.sufler_make_minutes()

    assert (tmp_path / "2026-08-07_181500_minutes.md").read_text(
        encoding="utf-8").startswith("# Минутки")
    assert "сохранены" in answer


# --- Демон: потомки не наследуют командный пайп приложения -------------------

def test_ни_один_потомок_демона_не_наследует_stdin():
    """Сторож класса, а не одного вызова.

    `claude` на унаследованном fifo ждёт EOF — это уже знали и закрыли в одном
    вызове из двух: ревизия нити висела до таймаута и молча гибла, а выпитые
    из пайпа байты — это команды интерфейса, которых демон уже не увидит.
    Проверяем сам вызов в разборе кода: аргумент, а не упоминание.
    """
    tree = ast.parse((ROOT / "src" / "daemon.py").read_text(encoding="utf-8"))
    naked = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) not in ("run", "Popen"):
            continue
        if getattr(getattr(node.func, "value", None), "id", None) != "subprocess":
            continue
        stdin = next((kw.value for kw in node.keywords if kw.arg == "stdin"), None)
        # Мало потребовать сам аргумент: `stdin=pipe` формально есть и ничего
        # не закрывает. Требуем именно DEVNULL — других правильных значений
        # здесь нет, ни один потомок демона со stdin не работает.
        if not (isinstance(stdin, ast.Attribute) and stdin.attr == "DEVNULL"):
            naked.append(node.lineno)
    assert not naked, (
        f"строки {naked}: потомок наследует командный пайп приложения — он "
        "может выпить команды интерфейса, а сам зависнуть до таймаута")


# --- Тап: рестарт не должен вставать на середину сэмпла ----------------------

class _OddStream(io.BytesIO):
    """Файл тапа, у которого дописан нечётный хвост — писатель в середине."""

    def read(self, n=-1):          # noqa: D102 — поведение BytesIO, но по кусочкам
        return super().read(n)


def test_позиция_рестарта_всегда_на_границе_сэмпла(monkeypatch):
    """`_pos` — то место, откуда сторож продолжит чтение после рестарта.

    Хвост в пол-сэмпла живёт в локальной переменной нити и после stop()
    гибнет. Если запомнить позицию ДО его выноса, seek встанет на середину
    сэмпла, и каждая следующая пара байт соберётся из половинок соседних:
    канал до конца встречи — шум, и ни строчки в логе об этом.
    """
    import audio

    cap = object.__new__(audio.TapStreamCapture)
    cap._m = {"samplerate": 16000}
    cap._stop_flag = __import__("threading").Event()
    cap.label = "blackhole"
    cap.q = __import__("queue").Queue()
    cap._pos = 0

    stream = _OddStream(b"\x01\x02" * 800 + b"\x7f")     # 1601 байт: хвост нечётный
    stop_after = {"n": 0}
    real_read = stream.read

    def read(n=-1):
        stop_after["n"] += 1
        if stop_after["n"] > 3:
            cap._stop_flag.set()
        return real_read(n)

    stream.read = read
    cap._pump_file(stream, None)

    assert cap._pos % 2 == 0, (
        f"запомнили нечётную позицию {cap._pos}: рестарт продолжит чтение с "
        "середины сэмпла и превратит канал в шум до конца встречи")
    assert cap._pos == 1600, f"потеряли или переприсвоили данные: {cap._pos}"

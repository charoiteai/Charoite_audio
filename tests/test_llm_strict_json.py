"""Строгий JSON — запасной путь, когда сервер модели его не умеет.

Сервер из формулы Homebrew собран без библиотеки грамматики: на
`POST /api/chat` с `"format":"json"` он отвечает 501
`{"error":"structured output is unavailable"}`, а та же служба на GGUF-модели
— 200. Прежде граф пропускал часть с ложной подсказкой «ollama pull», а имена
печатали «не удалось». Теперь клиент запоминает пару (адрес, ушедшая модель),
один раз говорит об этом в stderr и повторяет запрос без `format`, положившись
на промпт и разбор текста.

Тесты идут через маршрут сторожа сети `сценарий_чата`, без подмены
`requests`: настоящие `complete`, `resolve_model`, аренда и `_post_busy`.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
import requests

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import llm as llm_mod  # noqa: E402
import privacy  # noqa: E402
from llm import LLM, LLMHTTPError  # noqa: E402

CFG = {
    "llm": {"model": "тест-модель", "small_model": "тест-мелкая",
            "num_ctx": 8192, "temperature": 0.4},
    "sufler": {"role": "тестовая роль", "embed_model": "тест-эмбеддер"},
}

ПРИЧИНА = "structured output is unavailable"


def _ответ(статус: int, тело: dict) -> requests.Response:
    r = requests.Response()
    r.status_code = статус
    r._content = json.dumps(тело, ensure_ascii=False).encode("utf-8")
    return r


def _сценарий_отказа(статус: int, причина: str, *, ответ: str = "{}"):
    """`/api/chat`: с `format` — отказ `статус` с причиной, без — обычный ответ.

    Возвращает (журнал тел, обработчик маршрута): журнал показывает, что
    реально ушло на провод, и сколько было запросов.
    """
    журнал: list = []

    def обработчик(url, **k):
        # Снимок: повтор убирает format из того же словаря, а запоминать надо,
        # что ушло на каждый запрос (настоящий requests сериализует сразу).
        тело = dict(k.get("json") or {})
        журнал.append(тело)
        if "format" in тело:
            return _ответ(статус, {"error": причина})
        return _ответ(200, {"message": {"content": ответ}})

    return журнал, обработчик


def _всегда(статус: int, причина: str):
    журнал: list = []

    def обработчик(url, **k):
        журнал.append(dict(k.get("json") or {}))
        return _ответ(статус, {"error": причина})

    return журнал, обработчик


@pytest.mark.parametrize("статус, ожидание", [
    (501, True), (400, True),
    (429, False), (502, False), (503, False),
], ids=["501-грамматики-нет", "400-другой-сборки", "429-занято", "502-занято", "503-занято"])
def test_распознаватель_причины(статус, ожидание):
    """Причина — по телу без учёта регистра; занятость причиной не считается,
    даже если сервер по недоразумению вложил текст в 503."""
    err = LLMHTTPError(статус, '{"error":"Structured Output Is Unavailable"}')
    assert LLM._structured_output_unavailable(err) is ожидание

    без = LLMHTTPError(статус, "model not found")
    assert LLM._structured_output_unavailable(без) is False


def test_строка_в_stderr_сбрасывается_сразу(monkeypatch):
    """Строка обязана доехать до журнала, не осесть в буфере: она — вся
    диагностика владельцу, как у остальных строк llm.py (`flush=True`)."""
    class _Поток:
        def __init__(self):
            self.text = ""
            self.flushed = 0

        def write(self, s):
            self.text += s

        def flush(self):
            self.flushed += 1

    поток = _Поток()
    monkeypatch.setattr(llm_mod.sys, "stderr", поток)
    llm_mod._strict_json_announce(("http://x", "м"), "м", "http://x", "причина")

    assert "строгий JSON недоступен" in поток.text
    assert поток.flushed == 1, "строка осталась в буфере"


@pytest.mark.parametrize("статус", [501, 400], ids=["501", "400"])
def test_отказ_признаётся_по_телу_и_повтор_идёт_без_format(_ollama_маршруты, статус):
    """501 — частый код, но сборка вправе ответить 400 с тем же текстом.

    Первый запрос несёт `format`, повтор — уже нет; наружу уходит ответ повтора.
    """
    журнал, чат = _сценарий_отказа(статус, ПРИЧИНА, ответ='{"кто": "Сергей"}')
    _ollama_маршруты.сценарий_чата(чат)

    out = LLM(CFG).complete("в", model="тест-модель", json_format=True)

    assert out == '{"кто": "Сергей"}'
    assert [("format" in т) for т in журнал] == [True, False], журнал
    assert журнал[0]["format"] == "json"
    assert журнал[1]["model"] == журнал[0]["model"] == "тест-модель"


def test_второй_вызов_в_процессе_уже_не_кладёт_format(_ollama_маршруты):
    """Реестр живёт на модуле: новый `LLM` (клиент строят на вызов) берёт пару
    из него и не платит заведомо провальным запросом снова."""
    журнал, чат = _сценарий_отказа(501, ПРИЧИНА)
    _ollama_маршруты.сценарий_чата(чат)

    assert LLM(CFG).complete("в", model="тест-модель", json_format=True) == "{}"
    assert len(журнал) == 2, "первый вызов: format → отказ → повтор без format"

    журнал.clear()
    assert LLM(CFG).complete("в", model="тест-модель", json_format=True) == "{}"
    assert [("format" in т) for т in журнал] == [False], "format ушёл второй раз"


def test_строка_в_stderr_одна_на_два_вызова(_ollama_маршруты, capsys):
    """Отказ повторялся бы на каждом вызове: в журнале встречи это шум, из-за
    которого настоящей причины не видно. Одна строка на процесс на пару."""
    журнал, чат = _сценарий_отказа(501, ПРИЧИНА)
    _ollama_маршруты.сценарий_чата(чат)

    LLM(CFG).complete("в", model="тест-модель", json_format=True)
    LLM(CFG).complete("в", model="тест-модель", json_format=True)

    err = capsys.readouterr().err
    assert err.count("строгий JSON недоступен") == 1, err
    assert "тест-модель" in err and "сборка без грамматики" in err
    assert "ollama pull" not in err, "рецепта в строке быть не должно"


@pytest.mark.parametrize("статус", [503, 500], ids=["503-занято", "500-без-причины"])
def test_чужая_ошибка_не_попадает_в_реестр_и_идёт_наружу(_ollama_маршруты, capsys, статус):
    """Занятость и 500 без этой причины — не «нет грамматики»: повтор без
    format их не чинит, и реестр ими пачкать нельзя."""
    журнал, чат = _всегда(статус, "модель занята" if статус == 503 else "внутренняя ошибка")
    _ollama_маршруты.сценарий_чата(чат)

    with pytest.raises(LLMHTTPError) as e:
        LLM(CFG).complete("в", model="тест-модель", json_format=True, busy_wait=0)

    assert e.value.status == статус
    assert not llm_mod._strict_json, "чужой отказ записан в реестр строгого JSON"
    assert "строгий JSON недоступен" not in capsys.readouterr().err


@pytest.mark.ollama_отвечает("тест-мелкая")
def test_ключ_реестра_это_ушедшая_модель_а_не_запрошенная(_ollama_маршруты):
    """Основная модель не скачана — `resolve_model` отдаёт запасную; 501
    приходит на неё, и в реестр ложится именно запасная."""
    журнал, чат = _сценарий_отказа(501, ПРИЧИНА)
    _ollama_маршруты.сценарий_чата(чат)

    llm = LLM(CFG)
    assert llm.complete("в", json_format=True) == "{}"      # model не задан — решает resolve_model

    assert журнал[0]["model"] == "тест-мелкая", "на провод ушла не запасная"
    assert list(llm_mod._strict_json) == [(llm.base, "тест-мелкая")]
    assert ПРИЧИНА in llm_mod._strict_json[(llm.base, "тест-мелкая")]


def test_обычный_complete_не_шлёт_format(_ollama_маршруты):
    """`json_format=False` — поля format в теле нет: серверный отказ в ответ на
    format не должен запускать повтор там, где format и не просили."""
    журнал, чат = _сценарий_отказа(501, ПРИЧИНА)
    _ollama_маршруты.сценарий_чата(чат)

    assert LLM(CFG).complete("в", model="тест-модель") == "{}"
    assert len(журнал) == 1 and "format" not in журнал[0]


def test_причина_без_json_format_идёт_наружу_без_повтора(_ollama_маршруты):
    """Сервер ответил причиной, но format мы не просили: повторять нечего —
    отказ идёт наружу, как раньше, и реестр остаётся чистым."""
    журнал, чат = _всегда(501, ПРИЧИНА)
    _ollama_маршруты.сценарий_чата(чат)

    with pytest.raises(LLMHTTPError) as e:
        LLM(CFG).complete("в", model="тест-модель")

    assert e.value.status == 501
    assert len(журнал) == 1, "повтор без format, которого не было, — лишний запрос"
    assert not llm_mod._strict_json


def test_probe_живости_ходит_в_generate_без_format(_сеть_закрыта):
    """Строгий JSON сломан (501 на `/api/chat`), но модель жива: проба ходит в
    `/api/generate` без `format` и получает True."""
    base = privacy.llm_base_url(CFG)
    генерация: list = []

    def generate(url, **k):
        генерация.append(k.get("json") or {})
        return _ответ(200, {"response": "ok"})

    _сеть_закрыта[("POST", f"{base}/api/generate")] = generate
    _, чат = _сценарий_отказа(501, ПРИЧИНА)
    _сеть_закрыта[("POST", f"{base}/api/chat")] = чат

    import llm_health
    assert llm_health.probe(CFG) is True
    assert len(генерация) == 1 and "format" not in генерация[0]
    assert генерация[0]["prompt"] == "ok"

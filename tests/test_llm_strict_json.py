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


@pytest.mark.parametrize("статус, тело, ожидание", [
    (200, '{"кто": "Сергей"}', llm_mod.STRICT_YES),
    (200, '{"error": "structured output is unavailable"}', llm_mod.STRICT_NO),
    (200, '{"error": "Structured Output Is Unavailable"}', llm_mod.STRICT_NO),
    (200, '{"error": "model not found"}', llm_mod.STRICT_UNKNOWN),
    (200, "", llm_mod.STRICT_UNKNOWN),
    (200, "не json", llm_mod.STRICT_UNKNOWN),
    (200, "<html>страница квоты</html>", llm_mod.STRICT_UNKNOWN),
    (200, '[{"кто": "Сергей"}]', llm_mod.STRICT_UNKNOWN),
    (501, '{"error": "structured output is unavailable"}', llm_mod.STRICT_NO),
    (400, "structured output is unavailable", llm_mod.STRICT_NO),
    (500, "внутренняя ошибка", llm_mod.STRICT_UNKNOWN),
    (404, "model not found", llm_mod.STRICT_UNKNOWN),
    (429, '{"error": "structured output is unavailable"}', llm_mod.STRICT_UNKNOWN),
    (502, "structured output is unavailable", llm_mod.STRICT_UNKNOWN),
    (503, "busy", llm_mod.STRICT_UNKNOWN),
], ids=["200-объект", "200-фраза", "200-фраза-регистр", "200-иная-ошибка",
        "200-пусто", "200-не-json", "200-html", "200-не-объект",
        "501", "400-сырой-текст", "500", "404", "429", "502", "503"])
def test_вердикт_по_статусу_и_телу(статус, тело, ожидание):
    """Одна таблица двери: 200 с объектом без error — yes; error с фразой —
    no, без фразы — unknown; пусто/HTML/не объект — unknown. Вне 200 фразу
    решает причина, а занятость (429/502/503) — всегда unknown: это очередь,
    не отсутствие грамматики."""
    исход, _ = llm_mod.strict_json_verdict(статус, тело)
    assert исход == ожидание


def test_причина_вердикта_это_поле_error():
    """Причина — поле error объекта JSON, иначе сырой текст."""
    _, причина = llm_mod.strict_json_verdict(501, '{"error": "нет грамматики"}')
    assert причина == "нет грамматики"
    _, сырой = llm_mod.strict_json_verdict(500, "сервер упал")
    assert сырой == "сервер упал"


def test_фраза_за_окном_печати_всё_равно_признаётся():
    """Вердикт читает ПОЛНЫЙ текст: причина, стоящая за REASON_WINDOW-м знаком,
    всё равно даёт no, а напечатанная причина обрезана окном."""
    длинный = "х" * (llm_mod.REASON_WINDOW + 10) + ПРИЧИНА
    исход, причина = llm_mod.strict_json_verdict(501, длинный)
    assert исход == llm_mod.STRICT_NO
    assert len(причина) == llm_mod.REASON_WINDOW
    assert причина == длинный[:llm_mod.REASON_WINDOW]


def test_строка_одна_на_пару_даже_при_новом_отказе(capsys):
    """Дедуп — свойство самой строки, а не следствие реестра: после истечения
    записи тот же отказ снова ложится в реестр, но в журнал строка не
    повторяется. Другая пара — своя строка (выходной круг 1 по №419, DS M4)."""
    ключ = ("http://x", "м")
    llm_mod._strict_json_announce(ключ, "м", "http://x", "причина")
    llm_mod._strict_json_announce(ключ, "м", "http://x", "причина")
    llm_mod._strict_json_announce(("http://x", "другая"), "другая", "http://x", "причина")

    err = capsys.readouterr().err
    assert err.count("строгий JSON недоступен у м на") == 1, err
    assert err.count("строгий JSON недоступен у другая на") == 1, err


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
    assert "тест-модель" in err
    assert "сборка без грамматики" not in err, "догадка о сборке не замерена (№438)"
    assert ПРИЧИНА in err, "причина сервера в строке дословно"
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


def test_unknown_идёт_наружу_и_не_трогает_реестр(_ollama_маршруты, capsys):
    """200 с полем error без фразы двери — вердикт unknown: это не «нет
    грамматики», повтор без format его не чинит. Наружу, реестр и строка
    «уже сказали» не тронуты."""
    журнал, чат = _всегда(200, "model not found")
    _ollama_маршруты.сценарий_чата(чат)

    with pytest.raises(LLMHTTPError) as e:
        LLM(CFG).complete("в", model="тест-модель", json_format=True, busy_wait=0)

    assert e.value.status == 200
    assert len(журнал) == 1, "unknown — повтор не шлём"
    assert not llm_mod._strict_json
    assert "строгий JSON недоступен" not in capsys.readouterr().err


def test_фраза_одна_у_stderr_и_доктора(_ollama_маршруты, capsys):
    """Строку о двери печатает одна функция (`strict_json_sentence`): stderr и
    доктор берут её оттуда дословно, а не повторяют текст каждый по-своему."""
    llm = LLM(CFG)
    _, чат = _сценарий_отказа(501, ПРИЧИНА)
    _ollama_маршруты.сценарий_чата(чат)
    LLM(CFG).complete("в", model="тест-модель", json_format=True)

    err = capsys.readouterr().err
    ожидание = llm_mod.strict_json_sentence("тест-модель", llm.base, ПРИЧИНА)
    assert ожидание in err, err
    assert llm_mod.STRUCTURED_OUTPUT_PHRASE in ожидание


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


def test_проба_строгого_json_идёт_на_чат_с_format(_сеть_закрыта):
    """Транспорт пробы: тот же `/api/chat`, дешёвое тело двери — format, один
    токен, think выключен, без стрима. Вердикт — по ответу сервера."""
    import llm_health
    base = privacy.llm_base_url(CFG)
    тела: list = []
    сроки: list = []

    def чат(url, **k):
        тела.append(k.get("json") or {})
        сроки.append(k.get("timeout"))
        return _ответ(200, {"message": {"content": "{}"}})

    _сеть_закрыта[("POST", f"{base}/api/chat")] = чат

    assert llm_health.strict_json(base, "тест-модель") == (llm_mod.STRICT_YES, '{"message": {"content": "{}"}}')
    assert len(тела) == 1
    assert сроки == [90], "таймаут пробы по умолчанию — 90 с"
    тело = тела[0]
    assert тело["format"] == "json"
    assert тело["options"] == {"num_predict": 1}
    assert тело["think"] is False and тело["stream"] is False
    assert тело["messages"] == [{"role": "user", "content": "ok"}]


@pytest.mark.parametrize("статус, тело, ожидание", [
    (501, {"error": ПРИЧИНА}, (llm_mod.STRICT_NO, ПРИЧИНА)),
    (200, {"error": "иное"}, (llm_mod.STRICT_UNKNOWN, "иное")),
    (503, {"error": ПРИЧИНА}, (llm_mod.STRICT_UNKNOWN, ПРИЧИНА)),
], ids=["no", "unknown-на-200", "занято"])
def test_проба_строгого_json_вердикт(_сеть_закрыта, статус, тело, ожидание):
    import llm_health
    base = privacy.llm_base_url(CFG)
    _сеть_закрыта[("POST", f"{base}/api/chat")] = lambda url, **k: _ответ(статус, тело)

    assert llm_health.strict_json(base, "тест-модель") == ожидание


def test_проба_строгого_json_на_сети_и_таймауте_unknown(_сеть_закрыта):
    """Сети и таймаута ответа сервера нет — вердикта о грамматике из них не
    вывести: наружу unknown с причиной, а не «нет строгого JSON»."""
    import llm_health
    base = privacy.llm_base_url(CFG)
    _сеть_закрыта[("POST", f"{base}/api/chat")] = lambda url, **k: (
        _ for _ in ()).throw(requests.ConnectionError("нет маршрута"))

    исход, причина = llm_health.strict_json(base, "тест-модель")
    assert исход == llm_mod.STRICT_UNKNOWN
    assert причина.startswith("сеть:")

    _сеть_закрыта[("POST", f"{base}/api/chat")] = lambda url, **k: (
        _ for _ in ()).throw(requests.ReadTimeout("тишина"))
    исход, причина = llm_health.strict_json(base, "тест-модель")
    assert (исход, причина) == (llm_mod.STRICT_UNKNOWN, "таймаут")


def test_здесь_же_равенство_занятости_у_доктора():
    """Своя BUSY_STATUSES у llm_health — тот же набор, что у двери: иначе
    проба живости и дверь строгого JSON считали бы «занято» по-разному."""
    import llm_health
    assert frozenset(llm_health.BUSY_STATUSES) == llm_mod.BUSY_STATUSES


def _часы(monkeypatch, старт: float = 1000.0):
    """Часы модуля (`_fit_clock` — один шов на кэш и реестр) под рукой теста."""
    now = [старт]
    monkeypatch.setattr(llm_mod, "_fit_clock", lambda: now[0])
    return now


def test_запись_реестра_стареет_и_format_пробуется_снова(_ollama_маршруты, capsys, monkeypatch):
    """Отсутствие грамматики — факт о сборке сервера, а не о процессе: сервер
    могут починить, пока живёт демон. Через срок format уходит снова; пока
    запись свежая — нет (выходной круг 1 по №419, DS I2)."""
    now = _часы(monkeypatch)
    журнал, чат = _сценарий_отказа(501, ПРИЧИНА)
    _ollama_маршруты.сценарий_чата(чат)
    LLM(CFG).complete("в", model="тест-модель", json_format=True)

    журнал.clear()
    LLM(CFG).complete("в", model="тест-модель", json_format=True)
    assert [("format" in т) for т in журнал] == [False], "тот же миг — запись свежая"

    now[0] += llm_mod.STRICT_JSON_RECHECK_S - 1
    журнал.clear()
    LLM(CFG).complete("в", model="тест-модель", json_format=True)
    assert [("format" in т) for т in журнал] == [False], "свежая запись — format не нужен"

    now[0] += 2
    журнал.clear()
    LLM(CFG).complete("в", model="тест-модель", json_format=True)
    assert [("format" in т) for т in журнал] == [True, False], "старая запись — format пробуется снова"
    assert capsys.readouterr().err.count("строгий JSON недоступен") == 1, "пока сломан — строка одна"


def test_часы_назад_запись_не_продлевают(_ollama_маршруты, monkeypatch):
    now = _часы(monkeypatch)
    журнал, чат = _сценарий_отказа(501, ПРИЧИНА)
    _ollama_маршруты.сценарий_чата(чат)
    LLM(CFG).complete("в", model="тест-модель", json_format=True)

    now[0] -= 5                       # стенные часы ушли назад
    журнал.clear()
    LLM(CFG).complete("в", model="тест-модель", json_format=True)
    assert [("format" in т) for т in журнал] == [True, False]


def test_починили_и_сломали_снова_строка_снова_видна(_ollama_маршруты, capsys, monkeypatch):
    """Сервер починили (format принят) — пара забыта целиком, и новый эпизод
    без грамматики снова виден владельцу в журнале (выходной круг 2, DS M3).
    Маршрут один на тест (сторож ставит первый), поэтому состояние — флагом."""
    now = _часы(monkeypatch)
    сервер = {"без_грамматики": True}
    журнал: list = []

    def чат(url, **k):
        тело = dict(k.get("json") or {})
        журнал.append(тело)
        if "format" in тело and сервер["без_грамматики"]:
            return _ответ(501, {"error": ПРИЧИНА})
        return _ответ(200, {"message": {"content": "{}"}})

    _ollama_маршруты.сценарий_чата(чат)
    llm = LLM(CFG)
    llm.complete("в", model="тест-модель", json_format=True)
    ключ = (llm.base, "тест-модель")

    сервер["без_грамматики"] = False              # положили библиотеку, перезапустили
    now[0] += llm_mod.STRICT_JSON_RECHECK_S + 1
    журнал.clear()
    assert LLM(CFG).complete("в", model="тест-модель", json_format=True) == "{}"
    assert [т.get("format") for т in журнал] == ["json"], "после срока format уходит и принимается"
    assert ключ not in llm_mod._strict_json

    сервер["без_грамматики"] = True               # обновили формулу — библиотека пропала снова
    LLM(CFG).complete("в", model="тест-модель", json_format=True)
    assert capsys.readouterr().err.count("строгий JSON недоступен") == 2, "второй эпизод немой"


def test_граф_после_отказа_грамматики_разбирает_чистый_ответ(_ollama_маршруты):
    """Сервер без грамматики: 501 → повтор без format → чистый JSON. Граф его
    берёт — без этой правки часть уходила с ложной подсказкой «ollama pull»."""
    import graph_updater as gu
    _, чат = _сценарий_отказа(501, ПРИЧИНА, ответ='{"название": "Тест", "люди": []}')
    _ollama_маршруты.сценарий_чата(чат)

    assert gu._extract(CFG, "стенограмма") == {"название": "Тест", "люди": []}


@pytest.mark.parametrize("ответ", [
    'Вот граф:\n{"название": "Тест", "люди": []}\nГотово.',
    '{"название": "Тест", "люди": [{"имя": "А"',
    '{"название": "2-3 слова", "люди": [{"имя": "..."}]}\n{"название": "Тест", "люди": [{"имя": "А"',
], ids=["проза-вокруг", "обрыв", "эхо-образца-и-обрыв"])
def test_граф_без_грамматики_не_угадывает(_ollama_маршруты, capsys, ответ):
    """Разбор графа строгий и в деградации: проза, обрыв, эхо образца — это
    названный отказ части, а не догадка. Терпимый срез два круга подряд отдавал
    в граф образец или осколок (выходные круги 1–2 по №419, DS C1)."""
    import graph_updater as gu
    _, чат = _сценарий_отказа(501, ПРИЧИНА, ответ=ответ)
    _ollama_маршруты.сценарий_чата(чат)

    assert gu._extract(CFG, "стенограмма") is None
    assert "граф: ответ модели" in capsys.readouterr().out


def test_граф_ответ_не_объектом_это_названный_отказ(_ollama_маршруты, capsys):
    """Без грамматики ответ может прийти списком: наверху ждут словарь, и
    `.items()` уронил бы весь пост-процессинг (выходной круг 3, DS I3)."""
    import graph_updater as gu
    _, чат = _сценарий_отказа(501, ПРИЧИНА, ответ='[{"название": "Тест"}]')
    _ollama_маршруты.сценарий_чата(чат)

    assert gu._extract(CFG, "стенограмма") is None
    assert "не объект, а list" in capsys.readouterr().out

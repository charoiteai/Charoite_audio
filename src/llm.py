"""Клиент модели: ЕДИНСТВЕННАЯ точка разговора с LLM-сервером.

Весь транспорт живёт здесь: стриминг подсказок, не-стриминговые документы
(complete) и эмбеддинги (embed). Модули конвейера не собирают HTTP-запросы
сами — иначе смена сервера превращается в правку тринадцати файлов, а модель
из конфига подменяется захардкоженной (аудит 14.08: Саммари и заметки месяц
звали старую модель).

Движка два, выбирает llm.engine в конфиге (см. privacy.llm_engine):

  ollama      — умолчание, Ollama API (/api/chat, NDJSON-стрим);
  mlx-server  — OpenAI-совместимый mlx_lm.server (/v1/chat/completions, SSE).
                Зачем: у него кэш префикса (LRUPromptCache/fetch_nearest_cache),
                и растущая нить встречи получает префилл 0.3с вместо 30с
                (замер 14.08, 88×). Ограничения честно: строгого JSON-режима
                (format:"json") у него нет — complete(json_format=True) там
                полагается на промпт и разбор текста; сервер обслуживает ОДНУ
                модель, с которой запущен, поэтому small/fallback-лестница
                схлопывается в неё же (семантика CHAROITE_ONE_MODEL).

Эмбеддинги движка не выбирают: bge-m3 живёт на Ollama при любом engine —
mlx_lm.server эмбеддингов не отдаёт.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator

import requests

import privacy


class LLMHTTPError(RuntimeError):
    """Сервер ответил, но не результатом: HTTP-статус ≠ 200 или поле error.

    Отдельный класс, а не голый None: вызывающему коду нужны и статус
    (404 → «модель установлена?»), и текст ошибки — сообщения пользователю
    в mcp_server и graph_updater различают эти случаи.
    """

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"HTTP {status}: {detail[:200]}")
        self.status = status
        self.detail = detail


def parse_json_block(text: str) -> dict | None:
    """Первый JSON-объект из ответа модели.

    Модели заворачивают JSON в прозу и ```-заборы даже при прямом запрете;
    берём первый блок в фигурных скобках. None — разобрать нечего.
    """
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# Дефолтные веса для mlx-server: тот же MoE, что боевой ollama-тег, только
# именем HF-репозитория — mlx_lm.server грузит модели из huggingface-кэша.
DEFAULT_MLX_MODEL = "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit"

# Явный потолок ответа для mlx-server, когда вызывающий не задал num_predict.
# У Ollama отсутствие num_predict означает «без потолка», а mlx_lm.server
# молча применяет СВОЙ дефолт из аргументов запуска (обычно 512) — живой тред
# и разбор графа резались бы на полуслове, внешне неотличимо от короткого
# ответа модели.
MLX_MAX_TOKENS_DEFAULT = 4096


def embed(cfg: dict, texts: list[str], model: str | None = None,
          keep_alive: str | None = None, timeout: float = 20) -> list[list[float]]:
    """Эмбеддинги через /api/embed. Пустой список — сервер не ответил векторами.

    Всегда Ollama (privacy.llm_base_url), независимо от llm.engine:
    mlx_lm.server эмбеддингов не отдаёт, bge-m3 остаётся здесь.

    Таймаут по умолчанию короткий (20с): эмбеддинг занимает ~0.2с, и если
    сервер занят тяжёлой генерацией, вызывающему контуру дешевле пропустить
    проход, чем стоять заблокированным (замер дежавю).
    """
    payload: dict = {
        "model": model or (cfg.get("sufler") or {}).get("embed_model", "bge-m3:latest"),
        "input": texts,
    }
    if keep_alive:
        payload["keep_alive"] = keep_alive
    r = requests.post(privacy.llm_base_url(cfg) + "/api/embed",
                      json=payload, timeout=timeout)
    return r.json().get("embeddings", []) or []


class LLM:
    def __init__(self, cfg: dict):
        l = cfg["llm"]
        self._cfg = cfg          # для оживления вставшей модели в complete()
        self.engine = privacy.llm_engine(cfg)
        # У каждого движка свой адрес: у Ollama — llm.base_url (:11434),
        # у mlx_lm.server — llm.mlx_base_url (:8080). Оба под одной
        # privacy-дисциплиной loopback/allow_remote.
        self.base = (privacy.mlx_base_url(cfg) if self.engine == "mlx-server"
                     else privacy.llm_base_url(cfg))
        self.mlx_model = str(l.get("mlx_model") or DEFAULT_MLX_MODEL)
        self.model = l["model"]
        self.small = l.get("small_model", self.model)
        self.fallback = l.get("fallback_model", self.small)
        # Ночью — одна модель на всё.
        #
        # Днём мелкие задачи уходят на маленькую модель, и это правильно: они
        # быстрее и не занимают большую. Ночью же шаги идут подряд, большая и
        # маленькая чередуются, и на занятой памяти сервер начинает выгружать
        # одну ради другой: 12.08 за один прогон модель грузилась 41 раз,
        # запросы висели по 2-6 минут, а потом сервер лёг совсем. Держать в
        # памяти одну — дешевле, чем экономить на её размере: у 35B-A3B
        # активны те же 3B, что и у маленькой.
        if os.environ.get("CHAROITE_ONE_MODEL"):
            self.small = self.model
            self.fallback = self.model
        self.temperature = float(l.get("temperature", 0.4))
        # num_ctx ЯВНО: без него Ollama грузит модель с контекстом из Modelfile
        # (qwen3.6 — 262144), KV-кэш раздувается и генерации медленнее в разы
        # (20.07: подсказка не укладывалась в 90с на «тёплой» модели)
        self.num_ctx = int(l.get("num_ctx", 8192))
        self.system = cfg["sufler"]["role"]
        # свой шаблон минуток: разделы/формат под команду, не наш дефолт
        self.minutes_template = str(cfg["sufler"].get("minutes_template", "")).strip()
        # язык генерируемых документов (минутки/саммари/мгновенный ответ):
        # ru (дефолт) | en. Роль подсказок задаёт сам пользователь в sufler.role.
        self.lang = str(cfg["sufler"].get("language", "ru")).strip().lower()

    def _models_available(self) -> set[str]:
        try:
            r = requests.get(f"{self.base}/api/tags", timeout=3)
            return {m["name"] for m in r.json().get("models", [])}
        except Exception:
            return set()

    def resolve_model(self) -> str:
        """Основная, если скачана; иначе fallback (чтобы прототип работал сразу).

        На mlx-server лестницы нет: сервер обслуживает одну модель, с которой
        запущен, — возвращаем её имя (HF-репо), не спрашивая /api/tags.
        """
        if self.engine == "mlx-server":
            return self.mlx_model
        have = self._models_available()
        for m in (self.model, self.fallback, self.small):
            if m in have:
                return m
        return self.model  # пусть ollama сам скажет об ошибке

    def stream(self, prompt: str, model: str | None = None, system: str | None = None,
               think: bool = False, num_predict: int | None = None,
               temperature: float | None = None) -> Iterator[str]:
        # think=False КРИТИЧЕН для live-контуров: дефолтный thinking у gemma4
        # молча съедает ~10с до первого слова (замер 17.07: TTFT 10.4с → 0.5с).
        # think=True — только для глубоких фоновых проходов (deep_loop).
        #
        # ЛОВУШКА (замер 22.07): в Ollama num_predict ОДИН на рассуждение и ответ
        # (у Gemini это раздельные thinkingBudget/maxOutputTokens). qwen3.6 на
        # задаче «разложи по шаблону» думает на 12 тыс. знаков и съедает бюджет
        # целиком: минутки при think=True вышли ПУСТЫМИ (0 знаков) на бюджетах
        # 500 и 1600, а при 4000 — 83с против 10с и документ вдвое беднее.
        # Для документов рассуждение не включать; при think=True num_predict
        # либо не задавать вовсе (как в deep_loop), либо давать с запасом ×8.
        messages = [
            {"role": "system", "content": system or self.system},
            {"role": "user", "content": prompt},
        ]
        if self.engine == "mlx-server":
            yield from self._stream_mlx(messages, think=think,
                                        num_predict=num_predict,
                                        temperature=temperature)
            return
        options: dict = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_ctx": self.num_ctx,
        }
        if num_predict:
            options["num_predict"] = num_predict
        payload = {
            "model": model or self.resolve_model(),
            "messages": messages,
            "stream": True,
            "think": think,
            "keep_alive": "90m",  # держать модель в памяти всю встречу
            "options": options,
        }
        with requests.post(f"{self.base}/api/chat", json=payload, stream=True, timeout=300) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    break

    def _mlx_payload(self, messages: list[dict], *, think: bool | None,
                     num_predict: int | None, temperature: float | None,
                     stream: bool) -> dict:
        """Тело запроса к mlx_lm.server (/v1/chat/completions).

        Модель всегда self.mlx_model: сервер обслуживает ту одну, с которой
        запущен, и переданные ollama-теги (small/fallback из вызовов) сюда
        не транслируются — лестница схлопывается осознанно (докстринг модуля).
        num_ctx не передаётся: контекст — параметр старта сервера, проблемы
        «Modelfile раздувает KV-кэш» у этого движка нет.
        """
        payload: dict = {
            "model": self.mlx_model,
            "messages": messages,
            "stream": stream,
            "temperature": self.temperature if temperature is None else temperature,
            # потолок ВСЕГДА явный: без него сервер молча применяет свой
            # дефолт из аргументов запуска, и длинный ответ режется на
            # полуслове — внешне неотличимо от короткого ответа модели
            "max_tokens": num_predict or MLX_MAX_TOKENS_DEFAULT,
        }
        if think is not None:
            # enable_thinking — kwarg chat-шаблона qwen; сервер пробрасывает
            # его через chat_template_kwargs (mlx_lm 0.31: server.py:545)
            payload["chat_template_kwargs"] = {"enable_thinking": bool(think)}
        return payload

    def _stream_mlx(self, messages: list[dict], *, think: bool | None,
                    num_predict: int | None,
                    temperature: float | None) -> Iterator[str]:
        payload = self._mlx_payload(messages, think=think, num_predict=num_predict,
                                    temperature=temperature, stream=True)
        with requests.post(f"{self.base}/v1/chat/completions", json=payload,
                           stream=True, timeout=300) as r:
            r.raise_for_status()
            # SSE: полезная нагрузка ТОЛЬКО в строках «data: …», терминатор
            # «data: [DONE]». Всё остальное пропускаем по спецификации: во
            # время префилла сервер шлёт keepalive-комментарии «: keepalive
            # 1/1» — живой smoke 15.08 упал ровно на такой строке.
            for line in r.iter_lines():
                if not line or not line.startswith(b"data: "):
                    continue
                line = line[6:]
                if line.strip() == b"[DONE]":
                    break
                data = json.loads(line)
                chunk = (((data.get("choices") or [{}])[0].get("delta") or {})
                         .get("content") or "")
                if chunk:
                    yield chunk

    def warmup(self):
        """Гоним модель в память заранее — иначе первая подсказка ждёт ~20с загрузки."""
        try:
            for _ in self.stream("Ответь одним словом: готов", system="Ты просто отвечаешь: готов."):
                break
        except Exception:
            pass  # ollama может быть не поднят — не валим старт

    def complete(self, prompt: str, *, system: str | None = None,
                 model: str | None = None, think: bool | None = False,
                 json_format: bool = False, num_predict: int | None = None,
                 num_ctx: int | None = None, temperature: float | None = None,
                 timeout: float = 300, revive: bool = False) -> str:
        """Не-стриминговый чат: документы, разборы, классификация.

        Возвращает текст ответа модели («» — модель промолчала).
        LLMHTTPError — сервер ответил статусом ≠ 200 или полем error;
        requests.RequestException — сеть; ValueError — тело не JSON.
        Обработка у вызывающего: у минуток, графа и заметок разная цена
        ошибки, и превращать её здесь в пустую строку — та самая тихая
        деградация (пустышка ложилась поверх готовых минуток, аудит 0.46.0).

        revive=True — одна попытка поднять вставшую посреди работы модель
        через llm_health: для длинной стенограммы между частями проходят
        минуты, и «стояла с самого начала» ловит проба ДО разбора, а этот
        флаг — падение посреди.

        num_ctx без явного значения берётся из конфига (см. __init__:
        без явного num_ctx Ollama грузит модель с контекстом из Modelfile
        и KV-кэш раздувается в разы).

        think=None — поле не передаётся вовсе, действует умолчание модели
        (у qwen3.6 это ВКЛючённое рассуждение): так исторически работают
        разбор встречи и минутки через MCP, и выключать им рассуждение —
        отдельное решение с замером, а не побочный эффект рефакторинга.
        """
        messages = ([{"role": "system", "content": system}] if system else []) \
                   + [{"role": "user", "content": prompt}]
        if self.engine == "mlx-server":
            # Строгого JSON-режима у mlx-server нет: json_format здесь
            # полагается на промпт и разбор текста вызывающим (докстринг
            # модуля). Перед сменой боевого движка это меряет bench_extract.
            payload = self._mlx_payload(messages, think=think,
                                        num_predict=num_predict,
                                        temperature=temperature, stream=False)
            r = self._post_with_revive(f"{self.base}/v1/chat/completions",
                                       payload, timeout, revive)
            body = self._checked_body(r)
            msg = ((body.get("choices") or [{}])[0].get("message") or {})
            return (msg.get("content") or "").strip()
        options: dict = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_ctx": num_ctx or self.num_ctx,
        }
        if num_predict:
            options["num_predict"] = num_predict
        payload = {
            "model": model or self.resolve_model(),
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if think is not None:
            payload["think"] = think
        if json_format:
            payload["format"] = "json"
        r = self._post_with_revive(f"{self.base}/api/chat", payload, timeout, revive)
        body = self._checked_body(r)
        return ((body.get("message") or {}).get("content") or "").strip()

    def _post_with_revive(self, url: str, payload: dict, timeout: float,
                          revive: bool):
        """POST с одной попыткой поднять модель, вставшую посреди работы."""
        try:
            return requests.post(url, json=payload, timeout=timeout)
        except requests.RequestException:
            if not revive:
                raise
            import llm_health
            print("llm: запрос к модели не прошёл — пробую оживить")
            if not llm_health.ensure_alive(self._cfg, lambda m: print(f"llm: {m}")):
                raise
            return requests.post(url, json=payload, timeout=timeout)

    @staticmethod
    def _checked_body(r) -> dict:
        """Тело ответа или LLMHTTPError — общая часть обоих движков."""
        if r.status_code != 200:
            raise LLMHTTPError(r.status_code, r.text[:500])
        body = r.json()
        if isinstance(body, dict) and body.get("error"):
            raise LLMHTTPError(r.status_code, str(body["error"]))
        return body

    # Формат подсказки живёт в коде, а не в роли из конфига. Роль отвечает на
    # вопрос «кто ты и в каком контексте», формат — общий для всех и проверяем
    # тестом. Раньше он был размазан по пользовательскому config.yaml, и любая
    # правка роли молча меняла то, что человек читает во время встречи.
    HINT_FORMAT = {
        "ru": (
            "Веди конспект встречи для того, кто её слушает. Не ответ на вопрос, "
            "а нить разговора: о чём сейчас, почему это обсуждают, что было по "
            "этой теме раньше.\n\n"
            "Формат (пропускай раздел, если сказать нечего — пустых заголовков не пиши):\n"
            "● <тема сейчас, 3-5 слов>\n"
            "- <что сказали — до 3 строк, по делу>\n"
            "Почему: <зачем это обсуждают, что стоит за спором — одна строка>\n"
            "Было: <что по этой теме в памяти прошлых встреч, с датой — одна строка>\n"
            "Открыто: <вопрос, который висит без ответа — одна строка>\n\n"
            "Имя говорящего пиши ТОЛЬКО при передаче слова: несколько реплик одного "
            "человека подряд — имя один раз, дальше без него. Не пересказывай "
            "отчётными глаголами («утверждает», «уточняет», «упоминает») — пиши саму "
            "суть: не «Мария: Уточняет, что нужен стенд», а «нужен стенд, неделя на "
            "отработку кода».\n"
            "Пиши телеграфно, по-русски, фактами из разговора. Имена людей, систем и "
            "версий — ровно как звучали: «1.8», а не «RHEL 8». Узнаваемое название "
            "продукта, которого в разговоре не было, — выдумка, даже если оно кажется "
            "очевидным. Раздел «Было» бери ТОЛЬКО из памяти прошлых встреч выше; нет "
            "совпадений — пропусти его."
        ),
        "en": (
            "Keep a running digest for someone following the meeting. Not an answer "
            "to a question — the thread of the conversation: what is being discussed "
            "now, why, and what happened on this topic before.\n\n"
            "Format (skip a section when there is nothing to say):\n"
            "● <current topic, 3-5 words>\n"
            "- <what was said — up to 3 lines>\n"
            "Why: <what is behind this discussion — one line>\n"
            "Before: <what past-meeting memory says on this topic, with a date — one line>\n"
            "Open: <the question left hanging — one line>\n\n"
            "Name a speaker ONLY when the voice changes: several lines by the same "
            "person carry the name once. No reporting verbs (“states”, “clarifies”, "
            "“mentions”) — write the substance itself.\n"
            "Be terse, factual, in English. «Before» comes ONLY from the past-meeting "
            "memory above; no match — skip it."
        ),
        "zh": (
            "为正在旁听会议的人做实时纪要。不是回答问题，而是对话的脉络：现在在谈什么、"
            "为什么谈、这个话题此前有过什么。\n\n"
            "格式（没有内容的部分直接跳过）：\n"
            "● <当前话题，3-5 个词>\n"
            "- <说了什么——最多 3 行>\n"
            "为什么：<讨论背后的原因——一行>\n"
            "此前：<过往会议记忆中关于该话题的内容，带日期——一行>\n"
            "待解：<悬而未决的问题——一行>\n\n"
            "发言人名字只在换人时写一次，同一人连续发言不重复名字。不用「表示」「指出」"
            "「提到」这类转述动词——直接写内容本身。\n"
            "简洁、用中文、只用对话中的事实。「此前」只能来自上方的过往会议记忆。"
        ),
    }

    # Дописывание нити. Отличие от HINT_FORMAT принципиальное: там модель
    # каждый раз пишет конспект заново, здесь — только то, чего в нити ещё нет.
    # Приём из практики прогрессивных заметок (arXiv:2510.06677): показать уже
    # собранное и попросить «только новое»; нет нового — вернуть NONE.
    THREAD_FORMAT = {
        "ru": (
            "Ниже уже собранная нить встречи и свежий кусок разговора.\n"
            "Добавь ТОЛЬКО то, чего в нити ещё нет. Ничего нового не прозвучало — "
            "ответь ровно: NONE\n\n"
            "Каждая строка начинается со знака:\n"
            "● — новая тема разговора (3-5 слов). Ставь, только если тема сменилась.\n"
            "- — что сказали: кто и что предложил, возразил, сообщил\n"
            "⚑ — решение, срок, поручение: то, за что потом спросят\n"
            "? — вопрос, оставшийся без ответа\n"
            "⏮ — что по этой теме было раньше, с датой; ТОЛЬКО из памяти прошлых "
            "встреч выше, иначе не пиши\n\n"
            "ОДНА СТРОКА — ОДНА МЫСЛЬ, до 12 слов. Это читают краем глаза во время "
            "разговора: строка в три предложения там не читается вовсе. Два факта — "
            "две строки.\n"
            "Телеграфно, по-русски, фактами из разговора. Имена людей, систем и "
            "версий — ровно как звучали: «1.8», а не «RHEL 8»: узнаваемое название "
            "продукта, которого в разговоре не было, — выдумка. Пересказывать уже "
            "записанное другими словами не нужно: это и есть повтор."
        ),
        "en": (
            "Below is the thread of the meeting so far and a fresh stretch of talk.\n"
            "Add ONLY what is not in the thread yet. Nothing new — answer exactly: NONE\n\n"
            "Every line starts with a mark:\n"
            "● — a new topic (3-5 words). Only when the topic actually changed.\n"
            "- — what was said: who proposed, objected, reported\n"
            "⚑ — a decision, a deadline, an assignment: what you will be asked about\n"
            "? — a question left unanswered\n"
            "⏮ — what happened on this topic before, with a date; ONLY from the "
            "past-meeting memory above\n\n"
            "Terse, factual, in English. Names and versions exactly as they sounded."
        ),
        "zh": (
            "以下是已整理的会议脉络和最新一段对话。\n"
            "只补充脉络中还没有的内容。没有新内容就回答：NONE\n\n"
            "每行以符号开头：\n"
            "● — 新话题（3-5 个词），仅在话题真正改变时使用\n"
            "- — 谁说了什么：提议、反对、通报\n"
            "⚑ — 决定、期限、任务\n"
            "? — 尚未回答的问题\n"
            "⏮ — 该话题此前的情况，带日期；只能来自上方的过往会议记忆\n\n"
            "简洁、用中文、只用对话中的事实。名称和版本号照原样。"
        ),
    }

    def thread(self, transcript_tail: str, so_far: str,
               model: str | None = None) -> Iterator[str]:
        """Дописать нить встречи по свежему куску разговора."""
        collected = (f"<нить>\n{so_far}\n</нить>\n\n" if so_far.strip() else "")
        return self.stream(
            collected
            + f"<свежий разговор>\n{transcript_tail}\n</свежий разговор>\n\n"
            + self.THREAD_FORMAT.get(self.lang, self.THREAD_FORMAT["en"]),
            model=model,
        )

    def hint(self, transcript_tail: str, model: str | None = None) -> Iterator[str]:
        return self.stream(
            "Свежая стенограмма встречи (последние минуты):\n\n"
            f"{transcript_tail}\n\n"
            + self.HINT_FORMAT.get(self.lang, self.HINT_FORMAT["en"]),
            model=model,
        )

    def instant(self, tail: str, model: str | None = None) -> Iterator[str]:
        """Мгновенный готовый ответ на вопрос собеседника (режим собеседования).

        По умолчанию — лёгкая модель: TTFT доли секунды и кулер не раскручивает.
        """
        mem = ""
        if "Память прошлых встреч" in self.system:
            mem = "Память прошлых встреч" + self.system.split("Память прошлых встреч", 1)[1]
        return self.stream(
            f"Разговор (последние реплики):\n{tail}\n\n"
            "Последняя реплика собеседника — вопрос. Дай ГОТОВЫЙ ответ от первого лица, "
            "2-4 предложения, по делу, без вступлений и без маркеров.",
            model=model or self.small,
            # «Уверенно» из промпта убрано сознательно: в паре с памятью прошлых
            # встреч оно на тонкой стенограмме (первая минута) рождало ответы про
            # задачи и системы, которых на ЭТОЙ встрече никто не называл, —
            # модель уверенно выдавала контекст памяти за текущую повестку.
            system=((
                "你代表主人在工作会议或面试中发言，用他的口吻回答。简短、具体，用中文。"
                "诚实优先于自信：本次会议的事实（议程、任务、名称、数字）只能来自对话内容。"
                "下方的过往会议记忆只是风格和术语的背景，不是本次会议的议程。"
                "对话中没有的信息——直接说明或给出不含具体细节的笼统回答。\n\n" + mem
            ) if self.lang == "zh" else (
                "You answer AS the owner in a work meeting or interview, in their voice. "
                "Short, concrete, in English. HONESTY OVER CONFIDENCE: facts of THIS "
                "meeting (agenda, tasks, names, numbers) come ONLY from the conversation. "
                "Past-meeting memory below is style/terminology background, NOT today's "
                "agenda. No data in the conversation — say so or answer vaguely.\n\n" + mem
            ) if self.lang == "en" else (
                "Ты отвечаешь ЗА владельца на рабочей встрече или собеседовании, его голосом. "
                "Коротко, конкретно, по-русски. ЧЕСТНОСТЬ ВАЖНЕЕ УВЕРЕННОСТИ: факты этой "
                "встречи (повестка, задачи, названия, цифры) бери ТОЛЬКО из реплик разговора. "
                "Память прошлых встреч ниже — фон для стиля и терминов, НЕ повестка текущей "
                "встречи. Нет данных в разговоре — скажи прямо или дай обтекаемую "
                "формулировку без конкретики.\n\n" + mem
            )),
            # полный ответ за ~3с вместо 5-7с: глубокую версию параллельно даёт облако
            num_predict=180,
        )

    # Единый стиль всех документов встреч: plain-md читается без рендера.
    # Правила из практик (Google md-style, meeting-minutes best practices):
    # списки вместо таблиц, жирный ключ в начале пункта, короткие блоки,
    # одинаковая структура каждый раз — читатель знает, где что искать.
    STYLE = (
        "ФОРМАТ: никаких markdown-таблиц (|…|) — они нечитаемы в plain-тексте, "
        "только списки «- …» с жирным ключом в начале пункта "
        "(например «- **Иван** — подготовить расчёт — к пятнице»). "
        "Пустая строка после каждого заголовка. Коротко, без воды."
    )
    STYLE_EN = (
        "FORMAT: no markdown tables (|…|) — unreadable as plain text; "
        "use lists «- …» with a bold key first "
        "(e.g. «- **Ivan** — prepare the estimate — by Friday»). "
        "Blank line after every heading. Terse, no filler."
    )
    STYLE_ZH = (
        "格式：禁止使用 markdown 表格（|…|）——纯文本下不可读；"
        "只用列表「- …」，每项开头加粗关键词"
        "（例如「- **伊万** — 准备预算 — 周五前」）。"
        "每个标题后空一行。简洁，不说废话。"
    )

    def summary(self, transcript: str) -> Iterator[str]:
        if self.lang == "zh":
            return self.stream(
                f"会议记录：\n\n{transcript}\n\n"
                "压缩成会议纪要：决定事项、任务用「- **谁** — 做什么 — 期限」格式、"
                "待解决问题。用列表，中文。"
                "硬性限制：不超过700字符，每项一行。",
                model=self.small,
                system="你把工作会议记录压缩成清晰的纪要。不说废话。" + self.STYLE_ZH,
                num_predict=320,
                temperature=0.0,
            )
        if self.lang == "en":
            return self.stream(
                f"Meeting transcript:\n\n{transcript}\n\n"
                "Compress into a protocol: decisions, tasks as «- **Who** — what — due», "
                "open questions. Bullets, in English. "
                "HARD LIMIT: under 700 characters, one line per item.",
                model=self.small,
                system="You compress work-meeting transcripts into a crisp protocol. No filler. " + self.STYLE_EN,
                num_predict=320,
                temperature=0.0,
            )
        return self.stream(
            f"Стенограмма встречи:\n\n{transcript}\n\n"
            "Сожми в протокол: решения, задачи списком «- **Кто** — что — срок», "
            "открытые вопросы. Маркерами, по-русски. "
            "ЖЁСТКИЙ ЛИМИТ: не длиннее 700 знаков, каждый пункт — одна строка.",
            model=self.small,
            system="Ты сжимаешь стенограммы рабочих встреч в чёткий протокол. Без воды. " + self.STYLE,
            num_predict=320,
            temperature=0.0,  # см. minutes(): документ — не творческая задача
        )

    def _fit(self, transcript: str) -> str:
        """Длинную встречу сворачиваем в сводки частей, а не отдаём на обрезку.

        num_ctx 8192 — это примерно 25 000 знаков русского, то есть полчаса
        разговора. Часовая встреча не влезала вдвое, трёхчасовая вшестеро, и
        Ollama молча обрезала промпт: минутки выходили без единого решения из
        первого часа, но выглядели нормальным документом. Плюс num_predict
        делит тот же бюджет — при переполнении ответ обрывался на полуслове.
        """
        limit = max(4_000, self.num_ctx * 3 - 4_000)   # ~3 знака на токен, запас на ответ
        if len(transcript) <= limit:
            return transcript
        step = limit // 2
        parts = [transcript[i:i + step] for i in range(0, len(transcript), step)]
        digests = []
        for n, part in enumerate(parts, 1):
            text = "".join(self.summary(part)).strip()
            if text:
                digests.append(f"[Часть {n} из {len(parts)}]\n{text}")
        return "\n\n".join(digests) if digests else transcript[:limit]

    def minutes(self, transcript: str) -> Iterator[str]:
        """Полноценные минутки встречи (markdown, сохраняются файлом)."""
        transcript = self._fit(transcript)
        if self.lang == "zh":
            return self.stream(
                f"<transcript>\n{transcript}\n</transcript>\n\n"
                "按以下模板用 markdown 写会议纪要：\n"
                + (self.minutes_template + "\n\n" if self.minutes_template else
                   "# 会议纪要\n"
                   "**日期/时间：** … **参会人：** …\n"
                   "## 议题\n## 决定\n## 行动项\n## 待解决问题\n## 风险\n\n")
                + "规则：\n"
                "- 只用会议记录中说过的内容\n"
                "- 每项一行，每节最多3项\n"
                "- 行动项用复选框：「- [ ] **姓名** — 做什么 — 期限」\n"
                "  例：「- [ ] **德米特里** — 与财务对齐预算 — 7月25日前」\n"
                "- 决定的格式：「- **决定了什么** — 谁负责执行」\n"
                "- 参会人：对话中出现的名字；一个都没听到——写「主人及对方」\n"
                "- 空的部分只写一个词：「无」\n"
                "- 全文控制在900字符以内：纪要要一分钟能读完",
                system="你是会议记录员。准确、简练的中文会议纪要。" + self.STYLE_ZH,
                num_predict=420,
                temperature=0.0,
            )
        if self.lang == "en":
            return self.stream(
                f"<transcript>\n{transcript}\n</transcript>\n\n"
                "Write meeting minutes in markdown using this template:\n"
                + (self.minutes_template + "\n\n" if self.minutes_template else
                   "# Meeting minutes\n"
                   "**Date/time:** … **Participants:** …\n"
                   "## Topics\n## Decisions\n## Action items\n## Open questions\n## Risks\n\n")
                + "Rules:\n"
                "- use only what was said in the transcript\n"
                "- one line per item, at most 3 items per section\n"
                "- action items as checkboxes: «- [ ] **Name** — what — due»\n"
                "  example: «- [ ] **Dmitry** — align the budget with finance — by Jul 25»\n"
                "- decisions as: «- **what was decided** — who implements»\n"
                "- participants: names from the conversation; none heard — «owner and counterparts»\n"
                "- empty section: single word «none»\n"
                "- keep the whole document under 900 characters: minutes are a one-minute read",
                system="You are the meeting secretary. Precise, dry minutes in English. " + self.STYLE_EN,
                num_predict=420,
                temperature=0.0,
            )
        return self.stream(
            # Данные отделены тегами от инструкций, правила — позитивные
            # («пиши так»), а не отрицания: qwen следует им заметно лучше
            f"<стенограмма>\n{transcript}\n</стенограмма>\n\n"
            "Составь минутки встречи в markdown по шаблону:\n"
            + (self.minutes_template + "\n\n" if self.minutes_template else
               "# Минутки встречи\n"
               "**Дата/время:** … **Участники:** …\n"
               "## Темы\n## Решения\n## Поручения\n## Открытые вопросы\n## Риски\n\n")
            + "Правила:\n"
            "- бери только то, что прозвучало в стенограмме\n"
            "- каждый пункт — одна строка, максимум 3 пункта в разделе\n"
            "- поручение пиши чекбоксом: «- [ ] **Имя** — что сделать — срок»\n"
            "  пример: «- [ ] **Дмитрий** — согласовать бюджет с финансами — до 25.07»\n"
            "- решение пиши так: «- **что решили** — кто внедряет»\n"
            "- участники: имена из разговора; если имена не звучали — «владелец и собеседники»\n"
            "- в пустом разделе ставь одно слово «нет»\n"
            "- держи весь документ в пределах 900 знаков: минутки читают за минуту, "
            "это выжимка решений и поручений",
            system="Ты секретарь встречи. Пишешь точные, сухие минутки по-русски. " + self.STYLE,
            num_predict=420,  # потолок ≈1400 знаков: страховка от простыни
            # Замер на реальной встрече: при t=0.3 четыре прогона одной
            # встречи дали 39 разных утверждений, 32 — в единственном
            # экземпляре; один прогон выдумал номер задачи, которого в
            # стенограмме нет. При t=0 три прогона совпали побуквенно,
            # выдумка ушла: жадная выборка режет хвост распределения,
            # где галлюцинации и живут.
            # Оговорка: одинаковость ≠ правота. Стабильная ошибка
            # останется стабильной — на это работает сверка в
            # fact_check, а не температура.
            temperature=0.0,
        )

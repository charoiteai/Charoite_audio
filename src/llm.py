"""Клиент Ollama: стриминг подсказок суфлёра."""
from __future__ import annotations

import json
from collections.abc import Iterator

import requests


class LLM:
    def __init__(self, cfg: dict):
        l = cfg["llm"]
        self.base = l["base_url"].rstrip("/")
        self.model = l["model"]
        self.small = l.get("small_model", self.model)
        self.fallback = l.get("fallback_model", self.small)
        self.temperature = float(l.get("temperature", 0.4))
        # num_ctx ЯВНО: без него Ollama грузит модель с контекстом из Modelfile
        # (qwen3.6 — 262144), KV-кэш раздувается и генерации медленнее в разы
        # (20.07: подсказка не укладывалась в 90с на «тёплой» модели)
        self.num_ctx = int(l.get("num_ctx", 8192))
        self.system = cfg["sufler"]["role"]

    def _models_available(self) -> set[str]:
        try:
            r = requests.get(f"{self.base}/api/tags", timeout=3)
            return {m["name"] for m in r.json().get("models", [])}
        except Exception:
            return set()

    def resolve_model(self) -> str:
        """Основная, если скачана; иначе fallback (чтобы прототип работал сразу)."""
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
        options: dict = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_ctx": self.num_ctx,
        }
        if num_predict:
            options["num_predict"] = num_predict
        payload = {
            "model": model or self.resolve_model(),
            "messages": [
                {"role": "system", "content": system or self.system},
                {"role": "user", "content": prompt},
            ],
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

    def warmup(self):
        """Гоним модель в память заранее — иначе первая подсказка ждёт ~20с загрузки."""
        try:
            for _ in self.stream("Ответь одним словом: готов", system="Ты просто отвечаешь: готов."):
                break
        except Exception:
            pass  # ollama может быть не поднят — не валим старт

    def hint(self, transcript_tail: str, model: str | None = None) -> Iterator[str]:
        return self.stream(
            "Свежая стенограмма встречи (последние минуты):\n\n"
            f"{transcript_tail}\n\n"
            "Дай подсказку по формату из твоей роли.",
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
            "2-4 предложения, уверенно и по делу, без вступлений и без маркеров.",
            model=model or self.small,
            system=(
                "Ты отвечаешь ЗА владельца на рабочей встрече или собеседовании, его голосом. "
                "Коротко, уверенно, конкретно, по-русски. Факты не выдумывай: если данных "
                "в разговоре нет — предложи обтекаемую формулировку.\n\n" + mem
            ),
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

    def summary(self, transcript: str) -> Iterator[str]:
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

    def minutes(self, transcript: str) -> Iterator[str]:
        """Полноценные минутки встречи (markdown, сохраняются файлом)."""
        return self.stream(
            # Данные отделены тегами от инструкций, правила — позитивные
            # («пиши так»), а не отрицания: qwen следует им заметно лучше
            f"<стенограмма>\n{transcript}\n</стенограмма>\n\n"
            "Составь минутки встречи в markdown по шаблону:\n"
            "# Минутки встречи\n"
            "**Дата/время:** … **Участники:** …\n"
            "## Темы\n## Решения\n## Поручения\n## Открытые вопросы\n## Риски\n\n"
            "Правила:\n"
            "- бери только то, что прозвучало в стенограмме\n"
            "- каждый пункт — одна строка, максимум 3 пункта в разделе\n"
            "- поручение пиши так: «- **Имя** — что сделать — срок»\n"
            "  пример: «- **Дмитрий** — согласовать бюджет с финансами — до 25.07»\n"
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

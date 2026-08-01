"""Клиент Ollama: стриминг подсказок суфлёра."""
from __future__ import annotations

import json
from collections.abc import Iterator

import requests

import privacy


class LLM:
    def __init__(self, cfg: dict):
        l = cfg["llm"]
        self.base = privacy.llm_base_url(cfg)
        self.model = l["model"]
        self.small = l.get("small_model", self.model)
        self.fallback = l.get("fallback_model", self.small)
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

# Charoite (Чароит)

[![CI](https://github.com/charoiteai/Charoite_audio/actions/workflows/ci.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/ci.yml) [![swift-tests](https://github.com/charoiteai/Charoite_audio/actions/workflows/swift-tests.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/swift-tests.yml) [![CodeQL](https://github.com/charoiteai/Charoite_audio/actions/workflows/codeql.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/codeql.yml) ![License](https://img.shields.io/badge/license-Apache--2.0-blue) ![Platform](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-lightgrey) ![Local](https://img.shields.io/badge/cloud-none%20by%20default-success) [![Release](https://img.shields.io/github/v/release/charoiteai/Charoite_audio)](https://github.com/charoiteai/Charoite_audio/releases)

**Полностью локальный AI-ассистент встреч: диаризация спикеров и самообновляемый граф знаний. Ничего не покидает ваш Mac.**

![Приложение Charoite — стенограмма, тезисы, ответы по архиву](docs/img/app-main.png)

![Задачи со встреч — все чекбоксы графа одним окном](docs/img/app-tasks.png)

Чароит слушает встречи (микрофон + системный звук, без ботов в звонке), транскрибирует локально, различает говорящих, отвечает на вопросы прямо по ходу встречи, а после — обновляет Obsidian-граф знаний: люди, системы, решения и сквозные темы помнятся между встречами.

## Что умеет

- **Всё локально по умолчанию.** Аудио, распознавание (GigaAM — лучший русский STT), диаризация, саммари на Ollama — на вашей машине. Без облака, телеметрии и аккаунтов. Облачный Claude-слой — опция, по умолчанию выключен.
- **Диаризация, которая работает.** Живые метки «Собеседник 1/2/…» во время встречи + оффлайн-пересборка полной записи после — чистые абзацы по говорящим. Имя подставляется автоматически, когда человек представился, — и никогда не угадывается.
- **Граф знаний, а не свалка заметок.** Встречи — эпизоды; люди, системы, решения — узлы; сквозные темы — «Ядра» со статусом и хроникой. По ходу встречи суфлёр подсказывает: «⏮ уже обсуждалось 15.07, статус был такой».
- **Слои документов на каждую встречу**: Саммари на минуту чтения (со связью с прошлыми встречами) → Минутки → Разбор → полная Стенограмма.
- **Помощь в реальном времени**: мгновенный локальный ответ на вопрос собеседника (⚡), автотезисы, живой черновик минуток, голосовые заметки и диктовка.

## Требования

- Mac на Apple Silicon (M1+), рекомендуется 32 ГБ RAM для моделей по умолчанию
- [Ollama](https://ollama.com) — какие модели поместятся, см. таблицу по RAM ниже
- Python 3.11+
- Опционально: [BlackHole](https://existential.audio/blackhole/) для захвата системного звука, [Obsidian](https://obsidian.md) для графа

## Какие модели под вашу RAM

Всё локально. STT (~1 ГБ) и диаризация (~0.5 ГБ) постоянны; с памятью
масштабируются LLM. Везде `num_ctx: 8192`.
Семантический поиск добавляет `bge-m3` (~1,2 ГБ) — рекомендуется от 16 ГБ.

| RAM | Основная LLM | Лёгкая LLM | Граф | Что получаешь |
|----|----|----|----|----|
| **8 ГБ** | `qwen3.5:4b` | та же | нет | Стенограмма, тезисы, минутки, базовые подсказки |
| **16 ГБ** | `gemma4:latest` | `qwen3.5:2b` | медленно | Полный живой контур — рекомендуемая точка входа |
| **32 ГБ** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | да | Конфиг по умолчанию, на нём мерили |
| **64 ГБ+** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | да | Запас на облачный слой Claude + длинные встречи |

Ниже 16 ГБ граф знаний выключен (модели меньше 30B ломают JSON-схему); на
4 ГБ — только STT, а `llm.base_url` направить на другую машину.

**iOS/iPadOS**: телефон делает STT и лёгкую генерацию, тяжёлое уходит на Mac
через REST API. На iOS 26+ встроенные ~3B Foundation Models берут тезисы
бесплатно. Полные таблицы macOS/iOS и обоснование: [docs/MODELS.ru.md](docs/MODELS.ru.md).

## Быстрый старт

```bash
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml   # впишите user_name и graph_dir
```

Что-то не работает? Одна команда покажет, чего не хватает и как починить:

```bash
python3 scripts/doctor.py
```

**Вариант A — macOS-приложение (рекомендуется):**

Скачайте `Charoite.app.zip` из [последнего релиза](https://github.com/charoiteai/Charoite_audio/releases/latest)
(первый запуск: правый клик → Открыть — подпись ad-hoc), либо соберите сами:

```bash
./app/make_app.sh && open app/build/Charoite.app
```

Живая стенограмма, тезисы и подсказки, вопросы и брифы по архиву,
локальный чат с памятью графа, диктовка (⌥⌘D) и голосовые заметки (⌥⌘N).

**Вариант B — CLI:**

```bash
.venv/bin/python src/main.py     # живая стенограмма + подсказки в терминале
```

**Ещё нет встреч?** Наведите `graph_dir` на встроенный [демо-граф](demo/)
и спросите «что решили по платёжному провайдеру?» — продукт виден до
первой записи. Одна команда проверяет весь контур: `python3 scripts/memory_bench.py --demo`.
Есть старые записи? Одна команда импортирует файл встречи (аудио/текст/субтитры Zoom) в архив и граф: `python3 scripts/import_meeting.py файл --date 2026-07-15`.

STT-модели скачаются сами при первом запуске. Для живой диаризации положите ERes2Net-модель эмбеддингов в `models/diar/embedding.onnx` (см. docs/DIARIZATION.md).

## Документация

- [Планы](ROADMAP.md) · [Как контрибьютить](CONTRIBUTING.md)

- [Установка](docs/SETUP.ru.md) — зависимости, BlackHole для звонков, права, первый запуск
- [Возможности](docs/FEATURES.ru.md) — всё, что делает Чароит во время встречи и после
- [Архитектура](docs/ARCHITECTURE.ru.md) — демон, двухпроходная диаризация, пайплайн графа
- [Модели](docs/MODELS.ru.md) — почему такие дефолты, с бенчмарками; **пресеты по RAM для macOS (4/8/16/32 ГБ) и iOS**
- [Диаризация](docs/DIARIZATION.md) — установка и настройка модели эмбеддингов

## Приватность

[PRIVACY.md](PRIVACY.md): телеметрии нет, сетевых вызовов кроме вашего localhost нет — проверьте сами, код открыт. Записи удаляются через `record_keep_days`. Голосовые эмбеддинги живут только в RAM встречи — слепки голосов не хранятся.

## Статус

Публичная бета. Issues и фидбек приветствуются. Нативное macOS-приложение — в [app/](app/), сборка `app/make_app.sh`. Планы: английские промпты, узнавание голосов между встречами (голос → узел Люди), просмотрщик графа.

## Лицензия

Apache-2.0.

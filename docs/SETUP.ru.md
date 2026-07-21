# Установка и первый запуск

## 1. Зависимости

```bash
# Ollama + модели (основная и лёгкая)
brew install ollama
ollama pull qwen3.6:35b-a3b && ollama pull qwen3.5:4b && ollama pull gemma4:latest

# Charoite
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
```

На 16 GB RAM возьмите модели полегче — пресеты в комментариях конфига
и [MODELS.ru.md](MODELS.ru.md).

## 2. Конфиг: два обязательных поля

В `config/config.yaml`:

- `sufler.user_name` — ваше имя. Им подписывается ваш микрофон в стенограмме,
  и его никогда не присвоит другой голос.
- `sufler.graph_dir` — папка графа знаний (пусто = граф выключен, работает
  только транскрибация). Укажите путь внутри вашего Obsidian-vault, например
  `~/Documents/Obsidian/Work` — Чароит создаст структуру сам.

Полезно заполнить `sufler.user_context` (кто вы по работе, 1-2 предложения) —
это контекст для мгновенных ответов.

## 3. Системный звук (звонки) — BlackHole

Без него Чароит слышит только микрофон (очные встречи работают сразу).
Для звонков (Zoom/Meet/любые):

1. Установите [BlackHole 2ch](https://existential.audio/blackhole/).
2. Audio MIDI Setup → «+» → Multi-Output Device → отметьте динамики И BlackHole.
3. Выход системы → этот Multi-Output (звук слышен и попадает в Чароит).

Раздельные каналы дают бесплатную диаризацию «вы/собеседники» и фильтр эха.

## 4. Права macOS

- **Микрофон** — запросится при первом запуске.
- **Universal Access** (опционально) — только для системной вставки диктовки
  (⌘V за вас); без него текст просто остаётся в буфере обмена.

## 5. Диаризация голосов (опционально)

Положите ERes2Net-эмбеддер в `models/diar/embedding.onnx` —
инструкция в [DIARIZATION.md](DIARIZATION.md). Без него метки идут
по каналам (вы/собеседник), с ним — по голосам («Собеседник 1/2/…»).

## 6. Запуск

```bash
.venv/bin/python src/main.py        # CLI: живая стенограмма + подсказки
# или демон для интеграции с UI (NDJSON stdout/stdin):
.venv/bin/python src/daemon.py
```

Первый запуск скачает STT-модель (~1 мин). Скажите что-нибудь — строки
стенограммы появятся в консоли.

## 7. Что где лежит

- `transcripts/` — стенограммы и рабочие файлы встречи
- `recordings/` — полные записи (автоудаление через `record_keep_days`)
- `<graph_dir>/Встречи-архив/` — папка «дата — название» на каждую встречу:
  Саммари, Минутки, Стенограмма, Вопросы-ответы, Разбор

## Проблемы

- **Пустая стенограмма** — проверьте вход: `python -c "import sounddevice as sd; print(sd.query_devices())"`.
- **Медленные ответы** — `ollama ps`: модель должна держаться в RAM;
  проверьте, что в конфиге не убран `num_ctx: 8192`.
- **Нет системного звука** — выход macOS должен быть Multi-Output,
  а не напрямую динамики.

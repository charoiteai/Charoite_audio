# Experimental chronological meeting feed

The opt-in grounded feed (`sufler.live_feed: true`) runs locally through
`grounded_feed.semantic.SemanticActor` and `grounded_feed.local_runner.LocalFeedRunner`
under `LocalLoopbackAuthority` (loopback-only local Ollama `:local` or `mlx-server`).
Accepted speech, open questions, pending spans and canvas entries commit in one
immutable state transaction with a verified local Stop export (`<transcript>.grounded.json`).
Typed Swift rows show topic titles on transitions and return while retaining
original scroll IDs.

Живая нить (`sufler.live_feed: true`) работает строго локально через
`SemanticActor` и `LocalFeedRunner` под защитой `LocalLoopbackAuthority`
(loopback Ollama `:local` или `mlx-server`). Принятая речь, открытые вопросы и
полотно фиксируются одной транзакцией и сохраняются в проверенный Stop-экспорт
(`<transcript>.grounded.json`). Swift показывает заголовки тем при переходе и
возврате, сохраняя ID прокрутки.

Full local Stop exports have a shared writer/readback bound of 64,000,000 bytes
for the complete source and semantic history. Data is never truncated to fit;
an over-limit or failed export leaves an explicit error status and is not accepted
as complete. This is a finite storage bound, not a guarantee for unlimited sessions.

Полный локальный Stop-экспорт ограничен 64 000 000 байтами одинаково на записи
и чтении. Данные не обрезаются: превышение или ошибка дают явный статус отказа.
Это конечный предел хранения, а не обещание неограниченной встречи.

The feed appends short paragraphs in conversation order, keeps earlier text,
and places answers under their questions. Recording channel events remain
visible even when automatic generation is paused. Manual questions use their
existing card and take priority over a feed step.

Opt in explicitly in the local configuration:

```yaml
sufler:
  live_feed: true
  # feed_model: your-local-model  # otherwise llm.model
```

Only a loopback Ollama or mlx-server endpoint is supported. mlx-server always
uses `llm.mlx_model`. The feed has no cloud fallback. Existing cloud permissions
are unchanged. Missing new keys retain classic behaviour; `live_feed: false`
restores it. Classic `instant`, `auto_hint` and `auto_expand` can be disabled
individually. In feed mode their values cannot enable competing writers.

For the next app-managed mlx-server startup, optional
`llm.mlx_prefill_step_size` supplies a positive integer to the supported
`--prefill-step-size` flag. If omitted, classic startup keeps its original
arguments and feed startup selects 128. Explicit null, boolean, float, string,
zero and negative values are configuration errors before health/restart IO.
An inactive MLX key does not affect another engine. A healthy existing server
is left running: changing configuration expresses startup intent and does not
establish its actual profile or restart it.

The isolated cancellation evidence covers mlx-lm 0.31.3 and a cached public
27B 4-bit model at profile 128. One observed mid-prefill original call retired
within 4.502 s after cancellation, with roughly 0.5 s margin. Hardware
utilization was not measured. This is a model/profile-bound trial, not a
universal deadline or evidence about an existing working installation.
Default enablement remains false while the other acceptance gates are open.

A step runs about every three minutes and drains an existing backlog in short
successive steps. Even a short final utterance is eligible at the interval.
The chip pauses automatic generation; manual questions and channel events still
work. Stop cancels the active step and preserves the feed. Unprocessed speech
remains in the transcript; Stop does not launch another model request.

Scrolling follows updates only while the reader is near the bottom. Scrolling
up suspends following; returning to the bottom resumes it. An open manual answer
prevents a feed update from scrolling the reader away from that card.
An explicit question reveals its card once; later streamed tokens preserve the
reader's position. A newer scroll movement cancels an already queued card jump.

The model context has a 3,500-character serialized JSON budget, including
escaping. It admits whole open questions newest first, then up to two recent
whole paragraphs, then the latest whole paragraph for other known topics.
Every element retains its exact topic identity. Identical facts in different
topics remain distinct; a selected paragraph replaces only its own identical
topic anchor. Questions retain separate IDs. Feed equality preserves case,
punctuation, numbers and negation; classic fuzzy matching is unchanged.

An oversized element is skipped, and smaller subsequent elements are still
considered. Omission counts describe unrepresented open questions, the two
eligible recent paragraphs, and topics whose latest fact was not represented
in either paragraphs or anchors. Omitted old questions may remain unanswered:
the context does not search unavailable IDs after their answer speech is
acknowledged. This bound does not guarantee removal of semantic repetitions.
Custom budgets below two characters raise an error; budgets too small for the
empty counted schema return `{}`. The production budget always includes counts.

## Release gates

Default enablement remains blocked until cancellation of an actual local model
server request, including prefill, is demonstrated within five seconds. Reaping
the HTTP client is measured separately and does not establish server completion.
If cleanup cannot confirm its owned child, the model slot is quarantined rather
than allowing an overlapping request.

| Scenario | Evidence required |
| --- | --- |
| >20,000 characters; uninterrupted monologue; oversized word | Accepted bodies covered sequentially, no missing suffix; every input ≤9,000 characters |
| Speaker rename, markup, new speech during generation | Cursor unchanged by display edits; later speech remains pending |
| Invalid JSON, interrupted stream, busy, pause, loop restart | No acknowledgement until completed valid commit; retry same speech |
| Valid none or duplicate | Speech acknowledged without duplicating paragraphs |
| Channel loss/return during generation | System entries retained in all snapshots, transcript notes and sidecar |
| Two manual requests and timeout of one | Separate pending registrations; manual answer works while chip is off |
| Connect, headers, first token, read, busy backoff | Cancel client, reap child, close lease; verify actual server work separately |
| Classic/opt-in configuration and local model role | Retain legacy defaults; suppress competing feed writers; no cloud calls |
| Launched isolated UI | Scroll up/down, open manual answer, update feed, Stop and new meeting |
| Three invented quality scenes, 18 criteria | Actual model inputs/outputs and human assessment; criteria never sent to model |

Synthetic quality results do not establish equivalence on private historical
meetings. This implementation neither reads nor ships such data.

## Русский

Лента дописывает пересказ по ходу разговора, сохраняя прежние абзацы. Вопросы
и ответы стоят рядом; события записи не исчезают при паузе генерации. Для
включения задайте `sufler.live_feed: true`; модель — `sufler.feed_model` или
`llm.model`. На mlx-server фактически работает `llm.mlx_model`. Облако для
ленты не используется. Без нового ключа сохраняется классический режим.

Плашка приостанавливает пересказ; ручной вопрос остаётся доступен. Даже
короткая последняя реплика обрабатывается по таймеру. «Стоп» отменяет текущий
шаг и сохраняет экран; необработанная речь остаётся в стенограмме. Нового
запроса модели при остановке нет. При чтении выше лента сама не прокручивается.
Включение по умолчанию ждёт проверки остановки реального серверного запроса.
Необязательный `llm.mlx_prefill_step_size` — положительное целое для следующего
запуска MLX силами приложения. При отсутствии ключа classic сохраняет прежние
аргументы, а лента выбирает 128. Явные null, bool, дробь, строка, ноль и
отрицательное значение вызывают ошибку до проверок здоровья и перезапуска.
Здоровый уже работающий сервер не перезапускается: конфиг задаёт намерение для
следующего запуска, а не подтверждает действующие параметры. Отдельный опыт
с mlx-lm 0.31.3 и публичной 27B 4-bit моделью при 128 дал 4,502 с до окончания
работы после отмены внутри prefill; запас около 0,5 с, загрузка GPU не измерялась.
Это ограниченный опыт, а не гарантия для любых условий или рабочей установки.
Ручной вопрос открывает карточку один раз; следующие токены сохраняют позицию
чтения. Более поздняя прокрутка отменяет ещё ожидающий переход к карточке.

Контекст модели ограничен 3500 символами JSON с учётом экранирования. Сначала
помещаются целые открытые вопросы от новых к старым, затем два последних
абзаца и последние целые факты остальных тем. Принадлежность теме и ID вопроса
сохраняются; отрицания, даты, числа и знаки не обрезаются. Пропущенные элементы
показаны счётчиками. Старый вопрос вне контекста может остаться без ответа:
поиска недоступных ID нет. Это ограничение не гарантирует устранения смысловых
повторов. Бюджет менее двух символов вызывает ошибку; если даже пустая схема
со счётчиками не помещается, результат — `{}`.

## 中文

纪要按交谈顺序追加简短段落，保留已有内容，并把答案放在对应问题下。
设置 `sufler.live_feed: true` 后启用；模型使用 `sufler.feed_model`，未设置时
使用 `llm.model`。mlx-server 始终使用 `llm.mlx_model`。该功能仅支持本机
Ollama 或 mlx-server，不会回退到云端。未设置新开关时保留原有模式。

上方开关可暂停自动生成，手动提问和录音状态事件仍然有效。短的最后一句
也会按定时处理。停止会取消当前步骤并保留纪要；未处理的话语留在转录中，
不会在停止时再发起模型请求。向上阅读时不会自动滚动。默认启用仍等待
实际模型服务器请求取消的验证。

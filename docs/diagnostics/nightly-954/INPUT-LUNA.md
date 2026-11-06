# INPUT review — task 954

Base: `2f706a328bc7b880cd6c694f7786ca9b12b7de41` (clean public snapshot; privacy gate passed per task brief).

## Critical

C0 — нет.

## Important

I1 — `PLAN.md:7-11,18-20`: план требует «closed phase/reason enums» и описывает счётчики, но не задаёт допустимые значения enum и точную семантику их инкремента. Например, при timeout во время stream неясно, считать ли outcome как `timeout`, `error` или оба; при empty stream — outcome и stream entry также не определены. Это мешает гарантировать закрытый агрегат и однозначные синтетические ожидания. Минимальная правка: в контракте перечислить фиксированные phase/reason значения и правила «ровно один outcome на начатый local completion», а также какие события увеличивают `attempts`, `stream_entries`, `final_refusals` и `committed_topics`. Править `PLAN.md` до реализации.

I2 — `PLAN.md:17-20`: wording для shell-кодов 2/4 заявлен, но acceptance не требует проверить сами сообщения и статусные метки через `nightly.sh`; текущие `tests/test_nightly_exit.py:375-382` жёстко ожидают прежнее «модель-молчала». В таком виде тесты не ловят возврат ложной формулировки, а обновление может сохранить старую метку как статус. Минимальная правка: добавить синтетические проверки rc=2 и rc=4, что ночь остаётся failed и вывод/метки не утверждают молчание модели при неизвестной причине, включая combined scan failure. Место: acceptance в `PLAN.md` и тесты `tests/test_nightly_exit.py`.

## Minor

Нет.

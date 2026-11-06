# №954 / №958 — сверка находок

INPUT gpt-6.1-terra high и gpt-6-luna high: один исходный clean packet/base 2f706a3.
Все входные Important/Minor приняты до реализации: точные enum/единицы учёта, инкрементный legacy-счётчик,
вложенный finally, нейтральный Python/shell статус, отдельные request и final topic итоги и синтетические проверки.

OUTPUT / final risk: обе модели независимо получили head 49d9cd03a9744f96325dfe51881e5412342874b3,
base 2f706a328bc7b880cd6c694f7786ca9b12b7de41, clean packet b7b3e5af27ae49fd0cb5bebdf91c2582e873f2f459bcbb46cfd5aec36dba6bfb.
Terra: C0/I1/M1; Luna: C0/I0/M0. Чтение не выдаётся за эксперимент.

- Terra I1 принят: реальный декодер LLM использует LLMHTTPError и для ошибки тела/обрыва при HTTP200.
  Новый закрытый stream_error выбирается только по типизированному status==200, HTTP-отказ остаётся http_error;
  detail/body в проекцию не попадают. Тесты проводят реальный local_complete/LLM decoder с синтетическим backend,
  проверяя request outcome, итоговый отказ темы и закрытие ответа для HTTP200 и HTTP400.
- Terra M1 принят: BrokenPipeError в выводе сводки заменял исходный KeyboardInterrupt.
  При исходном исключении IO/closed-stream ошибка вывода не подменяет его; без исходной ошибки она видна вызывающему.
  При отказе stdout доставка агрегата не подтверждена; это ограничение, а не успешный вывод.
- Причинный контроль на коде 49d9 с теми же четырьмя добавленными тестами: 2 FAIL / 2 PASS.
  После двух адресных исправлений: 209 PASS / 5.85с (25 новых telemetry-сценариев и зависимые transport/decoder/encoding).

Critical отсутствуют. Important/Minor проверены адресными опытами; новый бесконечный круг не нужен по правилам проекта.
№958: classifier меняет ровно два сохранённых historical controls; карта регенерирована. До: 3 layout-расхождения; после: PASS.
Полная приёмка ждёт обязательные CI той же SHA. Реальная ночь/установка/причина инцидента остаются вне данного опыта.

## Обязательная матрица и survivor follow-up

PR-CI42172791582/head2202643f: Linux: 11081 PASS / 2 FAIL, mutation: 49/49 judged, 15 critical + 1 noncritical survived.
Два полных regression failures — обязательные реестры: новый LLMHTTPError reader и две точные KINDS history-записи.
Ожидания обновлены вместе с независимыми rule-dependent проверками, без расширения области исключения.
Мутационная suite-discovery не видела косвенный nightly import в новом telemetry suite; реальный CLI теперь
импортирован явно и используется сценарием pre-request подготовки. Официальный tests_for находит suite (18 наборов).

Все 16 findings закрываются в №954. 11 сохранившихся операторов воспроизведены штатным AST-mutator API в отдельном
чистом архиве с теми же 35 сценариями: 11 killed / 0 survived. Пять прежних bool/default-format переключателей
(два preserve_request arguments, его default, отдельный начальный request_failed bool и sort_keys) удалены
из источника; waiver/исключения/понижение зон не добавлялись. JSON-ключи уже обходятся детерминированно,
а лексикографический порядок контракт не обещает.

Новые случаи проверяют целую сборку при admission/preflight/contract refusal, append admission, завершение
темы другим писателем после скана, отказ подготовки до запроса, HTTPError, прерывание и standalone completion.
Также обнаружен настоящий lifecycle-дефект: empty первой попытки маскировал ошибку подготовки повтора.
Этот сценарий упал до исправления; теперь новая попытка очищает только текущую атрибуцию, сохраняя историю.
Последняя адресная проверка: 173 PASS / 69.36 с (telemetry35 + failure-kind/import-boundary contracts).
Факты/хеши и causal failures — в survivor-replay.json. Полная новая CI и обе final-risk проверки ещё обязательны.

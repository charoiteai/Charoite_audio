#!/usr/bin/env python3
"""Контроль поручений графа встреч: старые и ничьи снимаются, каждый прогон — сводка.

Решения владельца 23.09. Правила:
  «Мои» (исполнитель — владелец из конфига): без срока — встреча старше 14 дней;
  чужие: без срока — старше 7 дней;
  со сроком — просрочено на 7 дней и больше. Срок для записи разбирается шире, чем чип приложения:
  «до 5.10», «до 30 сентября», «до конца месяца», «к понедельнику», «в октябре»; из нескольких
  сроков берётся поздний. Срок назван, но не разобран («до конца спринта», «до 15») — не снимаем;
  без живого исполнителя (нет **Имени** в начале, метка «Собеседник N») — встреча не моложе 2 дней:
  облачная ревизия успевает подставить имя. Вложенный пункт без имени — исполнитель родителя.
Снятое остаётся в файле: `- [-] … _(снято по сроку ДД.ММ)_` или `_(снято: нет исполнителя ДД.ММ)_`.
Это не «выполнено»: вкладка «Задачи» читает только `- [ ]` и `- [x]`.

Вернуть: `restore.py` из каталога прогона (весь прогон, возвращённое попадает в реестр и больше не
снимается) или руками `[-]` → `[ ]`, оставив пометку `_(снято …)_` — строка с пометкой не снимается.

Ключ встречи — 12 цифр из пути, как у приложения. Пишем только в файлы встреч (`Встречи-архив/`,
`Встречи/`, `Документация/Стенограммы встреч/`) и не в код-блоках; минутки, отчёт ревизии и копии
снимаются одинаково. Остальные файлы только считаются в сводке.

Сводка: что сейчас во вкладке Mac — порт TasksService.scanSync + canonicalItems (склейка копий
поручения, Чароит 0.84.1, №367) + TasksScreenPolicy.split (Мои / Старые).

Гейты: самопроверка перед записью; живая встреча (лок демона или .pcm моложе 15 минут) — пропуск;
запись под общим замком графа через `safe_write.rewrite_file` (снимок до чтения, гейт expect, две
попытки); манифест JSON Lines дописывается до записи файла; в каталоге прогона — копии файлов,
`run.json`, `restore.py` и снимок хелперов `lib/` (откат работает и без исходного checkout).
Любая ошибка — строка в лог и уведомление, код 1.

Известный предел: источник минуток — `<стем>_minutes.md` в каталоге стенограмм вне графа, его мы
не трогаем. Повторная раскладка встречи конвейером возвращает строки в копиях графа, следующий
прогон снимает их снова (замер 23.09: 5 старых минуток за два месяца, все — ручные миграции).
Постоянное место — шаг ночного конвейера (№366), статус в источнике.

Запуск: без флагов — пробный прогон; --apply — запись; --selftest — правила, сводка и откат
на временном графе.
"""
from __future__ import annotations

import argparse
import calendar
import collections
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/` всегда
# лежит рядом с этим файлом. См. src/charoite_paths.py. Вставка — только чтобы
# импортировать сам канон.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import charoite_paths
import file_locks
import live_gate
from charoite_graph import graph_names, safe_write

CODE = charoite_paths.code_root(__file__)
#: Корень данных называет точка входа (`main` / `named_data_root`), а не импорт: до
#: названного корня решать, где лежат данные, нечем — и молчаливая догадка на импорте
#: читалась бы обработчиками как решение владельца.
DATA: pathlib.Path | None = None

MINE_DAYS, OTHERS_DAYS, OVERDUE_DAYS, NOOWNER_DAYS, GRACE_DAYS = 14, 7, 7, 2, 7
TAB_STALE_DAYS = 14                  # TasksScreenPolicy.staleAfterDays — только для сводки
LIVE_PCM_SECONDS = 15 * 60
LOCK_WAIT = 300
HANDLED = 75                          # код «ошибка уже в логе и в уведомлении»: не 1 и не 2 python/argparse
MINUTES = "Минутки.md"
WRITABLE = (("Встречи-архив",), ("Встречи",), ("Документация", "Стенограммы встреч"))
# Склейка вкладки — порт TasksService (Чароит 0.84.1, №367): каталоги встреч, строки минуток, которые
# знают поручение без чекбокса, ключ «тот же пункт».
MEETING_ROOTS = ("Встречи-архив/", "Встречи/", "Документация/Стенограммы встреч/")
KNOWN = re.compile(r"^\s*[-*+] +((?:\[-\]\s+|~~.+?~~\s*_\((?:снято ревизией|withdrawn by the review|已由审阅撤回)"
                   r"|⚠ (?:не участник|not a participant|非与会者)).*)$")
_OUTSIDER = re.compile(r"⚠[^:]*:")
_WITHDRAWN = re.compile(r"_\((?:снято|withdrawn by the review|已由审阅撤回)[^)]*\)_")
_WORD = re.compile(r"[^\W_]+")                       # \p{L}\p{N}
BRIDGE_MARKS = ("(из ревизии)", "(from the review)", "（来自审阅）")
MARKERS = ("до ", "к ", "дедлайн ", "дедлайну ")          # TaskDue.parse
BOX = re.compile(r"^(\s*[-*] )\[ \] (.+)$")
TODO = re.compile(r"^\s*[-*] \[( |x|X)\] +(.+)$")        # TasksService.todoRx
ANY_BOX = re.compile(r"^(\s*)[-*] \[[ xX-]\] +(.+)$")    # дерево пунктов: родитель вложенного
FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})")
HEADING = re.compile(r"^\s*#{1,6}\s")
LEAD_BOLD = re.compile(r"^\s*\*\*([^*]+)\*\*")
REASONS = {"срок": "снято по сроку", "ничьё": "снято: нет исполнителя", "давно": "снято: давно, срок не разобран",
           "не поручение": "снято: не поручение"}
CLOSED_MARK = re.compile(r"\s*_\((?:снято по сроку|снято: нет исполнителя|снято: давно, срок не разобран|снято: не поручение) \d\d\.\d\d\)_\s*$")
CAP_DAYS = 90                         # неразобранный знак времени — строка держится до потолка давности
LONG_CAP_DAYS = 400                   # то же со знаком долгого срока (квартал, полгода, год, месяцы, сезон)

# Сроки для записи (чип вкладки их не видит). Правило полное, а не остаточное (круги 2–3):
#   в строке нет ни одного ЗНАКА ВРЕМЕНИ (дата, время, год, месяц или день недели словом, «сегодня»,
#   «неделя», «квартал», «через N дней», «до 15»…) — правило возраста;
#   все знаки покрыты разобранными датами — правило срока по самой поздней, но не раньше правила
#   возраста: разобранный срок снятие только откладывает (круг 7 — фантомная «в среду тестирования»,
#   ссылка «от 04.09», версия «до 3.11.10» перестают ускорять снятие, какой бы ни была ошибка разбора);
#   хоть один знак не покрыт — строка держится до позднего из: потолка (CAP_DAYS, со знаком долгого
#   срока LONG_CAP_DAYS) и любой разобранной границы + неделя; потом «снято: давно».
# Диапазон за единицей срока («через месяц — максимум два», «неделю-другую», «3 месяца или 4») не
# покрывает знак, но оставляет нижнюю и верхнюю границу: строка держится до верхней (круг 7).
# Остаточный риск — срок словами вне словаря («к празднику»): строка живёт по правилу возраста, как
# поручение без срока. Разбором он не устраняется; постоянное место — срок полем (№366).
# Ошибка разбора так может только задержать снятие, но не ускорить его. Словарь знаков — корни, а не
# формы: неизвестная форма со знакомым корнем уходит в потолок, а не в возраст. Корень не сужают:
# фраза, где он не время («в тестовой среде», «до 5 человек»), — строка таблицы гашения `_NOT_TIME`,
# и только там (круг 5: сужение корня «среда» сняло «к следующей среде» раньше срока). Числительное
# словом («два», «полтора», «несколько») — знак: верхняя граница диапазона («через неделю или две»)
# иначе молча сжималась до нижней (круг 6). Голая цифра — не знак: замер 23.09 на 145 открытых
# поручениях — 38 % строк ушли бы в потолок против 22 %, почти все из-за счёта («9 ТБ», «10 000
# клиентов»); число за единицей срока через «или», тире, «максимум» — граница диапазона (`_RANGE_AFTER`).
# Покрывает знак только дата в окне срока — не раньше встречи на неделю и не дальше двух лет
# (`HORIZON_DAYS`), одно правило в `due_dates` для всех разборщиков: прошлый год за маркером
# («обновить до 3.11.10» — 2010-й) и номер версии («3.4.35» — 2035-й) уходят в потолок, а не в
# срок. Цена: настоящий срок дальше двух лет держится только до потолка давности (LONG_CAP_DAYS) —
# у поручений встречи таких не бывает. Сторож монотонности в самопроверке держит эти правила механически.
# Маркеры («до», «срок», «дедлайн») знаками не считаются: «срок не назван» — строка без срока.
# Постоянное место — срок полем при записи поручения (№366), тогда разбирать речь не придётся.
_MONTH_STEMS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август",
                "сентябр", "октябр", "ноябр", "декабр")
_MONTH = (r"(январ[ьяеюё]|феврал[ьяеюё]|март[аеу]?|апрел[ьяеюё]|ма[йяею]|июн[ьяеюё]|июл[ьяеюё]|"
          r"август[аеу]?|сентябр[ьяеюё]|октябр[ьяеюё]|ноябр[ьяеюё]|декабр[ьяеюё])(?!\w)")
# Маркеры срока — один список на привязку даты и «число за маркером» (круг 7: «не позже 20-го»).
_MARKERS = r"(?:до|к|ко|по|дедлайн[ау]?|срок[ау]?|не\s+(?:позднее|позже)|самое\s+позднее)"
_TIED = re.compile(r"(?<!\w)" + _MARKERS + r"(?:\s+кон(?:ца|цу))?\s*[:—–-]?\s*$")
_NUM_DATE = re.compile(r"(?<![\w.,/])(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?![\d%])(?![.,]\d)(?!\s*раз)")
_ISO_DATE = re.compile(r"(?<!\d)(20\d\d)-(\d\d)-(\d\d)(?!\d)")
_DAY_MONTH = re.compile(r"(?<![\w.,])(\d{1,2})(?:-?го)?\s+" + _MONTH)
_MONTH_WORD = re.compile(r"(?<!\w)" + _MONTH)
_PERIOD_END = re.compile(r"(?<!\w)кон(?:ца|цу|ец|це)\s+(месяца|квартала|года|недели|дня|встречи)(?!\w)")
_QUARTER = re.compile(r"(?<!\w)(?:(iv|i{1,3}|[1-4])(?:-?й)?\s+квартал\w*|q([1-4]))(?!\w)")
# Числительные — одна таблица на разбор «через N …», знаки и верхнюю границу диапазона (критика Opus,
# круг 7: три расходящихся списка). «Один» знаком не считается: диапазон растёт вверх.
_NUMBER_WORDS = {
    "один": 1, "одну": 1, "одна": 1, "одной": 1, "одним": 1,
    "два": 2, "две": 2, "двух": 2, "двум": 2, "двумя": 2, "пару": 2, "пары": 2, "парой": 2,
    "три": 3, "трёх": 3, "трех": 3, "трём": 3, "трем": 3, "тремя": 3,
    "четыре": 4, "четырёх": 4, "четырех": 4, "четырём": 4, "четырем": 4, "четырьмя": 4,
    "пять": 5, "пяти": 5, "пятью": 5, "шесть": 6, "шести": 6, "шестью": 6, "семь": 7, "семи": 7, "семью": 7,
    "восемь": 8, "восьми": 8, "восемью": 8, "девять": 9, "девяти": 9, "девятью": 9,
    "десять": 10, "десяти": 10, "десятью": 10, "полтора": 1.5, "полторы": 1.5, "полутора": 1.5,
    **{form: n for n, stem in enumerate(("одиннадцат", "двенадцат", "тринадцат", "четырнадцат", "пятнадцат",
                                         "шестнадцат", "семнадцат", "восемнадцат", "девятнадцат", "двадцат"), 11)
       for form in (stem + "ь", stem + "и", stem + "ью")},
    "тридцать": 30, "тридцати": 30, "тридцатью": 30, "сорок": 40, "сорока": 40, "пятьдесят": 50, "пятидесяти": 50,
    "шестьдесят": 60, "шестидесяти": 60, "семьдесят": 70, "семидесяти": 70, "восемьдесят": 80, "восьмидесяти": 80,
    "девяносто": 90, "девяноста": 90, "сто": 100, "ста": 100,
}
# Составное числительное «двадцать пять», «сорока трёх»: десятки и единицы складываются (Opus I2, круг 12).
_TENS = ("двадцат", "тридцат", "сорок", "пятьдесят", "пятидесят", "шестьдесят", "шестидесят", "семьдесят",
         "семидесят", "восемьдесят", "восьмидесят", "девяност")
_by_length = lambda words: "|".join(sorted(words, key=len, reverse=True))
_UNITS_1_9 = _by_length(w for w, n in _NUMBER_WORDS.items() if isinstance(n, int) and 1 <= n <= 9)
_COMPOUND = (r"(?:" + _by_length(w for w in _NUMBER_WORDS if w.startswith(_TENS)) + r")\s+(?:" + _UNITS_1_9 + r")")
_NUMERAL = r"(?:\d+|" + _COMPOUND + r"|" + _by_length(_NUMBER_WORDS) + r")(?!\w)"
_NUMERAL_SIGN = (r"(?<!\w)(?:" + _by_length(w for w, n in _NUMBER_WORDS.items() if n != 1)
                 + r"|нескольк(?:о|их|им|ими))(?!\w)")
_UNIT_DAYS = (("дн", 1), ("ден", 1), ("недел", 7), ("нед", 7), ("месяц", 30), ("мес", 30), ("квартал", 91),
              ("полгод", 182), ("полугоди", 182), ("год", 365), ("лет", 365), ("час", 0))
_SPAN = re.compile(r"(?<!\w)(?:через|в\s+течение)\s+(?:(" + _NUMERAL + r")\s+)?"
                   r"(дн(?:я|ей|и)?|день|недел[юиь]|недель|месяц(?:а|ев)?|квартал(?:а|ов)?|полгода|полугоди[ея]"
                   r"|год(?:а)?|лет|час(?:а|ов)?)(?!\w)")
# Диапазон за единицей срока: дефис вплотную («месяц-два», «неделю-другую») или разделитель — тире,
# запятая, связка («или», «максимум», «возможно», «в худшем случае»…) — перед числом или «другой».
# Без разделителя число — не граница («через неделю 2 раза»); тире перед словом без числа —
# разделитель («через неделю — прислать отчёт»). Граница несёт свою единицу («или 10 дней»), без неё —
# единицу левого куска; за «до» — только с единицей: «через неделю до 20 сентября» — дата, не граница
# (круг 8). Кусок диапазона включает хвост: число границы объяснено им, а не долгим знаком.
# Формы единицы целиком, не по началу слова («годных», «деньги» — не единицы; Opus M1, круг 9).
_UNIT_FORMS = (r"(?:дн(?:я|ей|и|ям|ями|ях)?|день|недел(?:я|и|ю|ь|е|ей|ями|ях|ям)|недель"
               r"|месяц(?:а|ев|ы|ам|ами|ах|е|ем|у)?|квартал(?:а|ов|ы|е|ом|ам|ами|ах|у)?|полгода|полугоди[еяю]"
               r"|год(?:а|у|ы|ов|ом|ам|ами|ах)?|лет|мес\.?|нед\.?)(?!\w)")
_CONNECTOR = (r"(?:или|либо|максимум|минимум|не\s+больше|не\s+более|не\s+позже|до|а\s+то\s+и|возможно"
              r"|может\s+(?:и|быть)|в\s+(?:худшем|крайнем)\s+случае|самое\s+большее|край)")
_RANGE_TAIL = (r"(?:(?P<adj>[-–])(?=\w)|\s*[-–—,(]\s*(?:(?P<c1>" + _CONNECTOR + r")\s*,?\s+)?"
               r"|\s+(?P<c2>" + _CONNECTOR + r")\s*,?\s+)"
               r"(?P<n>" + _NUMERAL + r"|друг\w*)(?:\s+(?P<u>" + _UNIT_FORMS + r"))?")
_RANGE_AFTER = re.compile(_RANGE_TAIL)
_NEXT = re.compile(r"(?<!\w)(?:на|в)\s+следующ\w+\s+(недел|месяц|квартал|полугоди|год)\w*")
_THIS_WEEK = re.compile(r"(?<!\w)(?:на|в)\s+(?:этой|эту|текущей|текущую)\s+недел\w*")
_NEAR = re.compile(r"(?<!\w)(до|к|ко|в|во)\s+(\w+)")
_WEEKDAY_CASE = {
    "до": ("понедельника", "вторника", "среды", "четверга", "пятницы", "субботы", "воскресенья"),
    "к": ("понедельнику", "вторнику", "среде", "четвергу", "пятнице", "субботе", "воскресенью"),
    "в": ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"),
}
_DAYWORD = re.compile(r"(?<!\w)(сегодня|послезавтра|завтра)(?!\w)")
_TIME_AFTER = re.compile(r"(?<!\w)(?:до|к|в)\s+(\d{1,2}:\d{2})")
_NO_DUE = re.compile(r"срок\w*\s+не\s+(?:назван|указан|определ)\w*|без\s+срока")
# Таблица гашения: фразы, где корень знака — не время. Гасятся пробелами до разбора и поиска знаков.
# «в/во/на (+ до двух слов) среде» — место: день недели в предложном с этими предлогами не бывает
# («в среду», «к среде»); слово между ними — не предлог, иначе «на встрече к среде» гасилось бы
# вместе со сроком; «среда/средой разработки» — только падежи, которых у дня-срока нет («к среде»,
# «до среды», «в среду» остаются знаками, круг 6); число за маркером с единицей счёта — не дата.
_NOT_TIME = re.compile(
    r"(?<!\w)(?:в|во|на)\s+(?:(?!(?:к|ко|до|по|в|во|на|с|со)\s)\w+\s+){0,2}среде(?!\w)"
    r"|(?<!\w)сред(?:а|ой)\s+(?:разработки|тестирования|исполнения|окружения)(?!\w)"
    r"|(?<!\w)(?:до|к|ко|по)\s+\d+\s*(?:человек\w*|штук\w*|шт(?:\.|(?!\w))|пункт\w*|строк\w*|руб\w*|%"
    r"|задач\w*|раз(?:а)?(?!\w)|тыс\w*|млн|млрд)")
_SIGN = re.compile(
    r"(?<![\w.,/])\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?![\d%])(?![.,]\d)(?!\s*раз)"
    r"|(?<!\d)20\d\d(?:-\d\d-\d\d)?(?!\d)"
    r"|(?<!\w)(?:январ|феврал|март|апрел|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*"
    r"|(?<!\w)(?:ма[йяею](?!\w)|майск\w*)"
    r"|(?<!\w)(?:янв|фев|мар|апр|авг|сен|сент|окт|ноя|нояб|дек)(?:\.|(?!\w))"
    r"|(?<!\w)(?:осен\w*|зим\w*|весн\w*|лет(?:о|а|у|ом)|лет)(?!\w)"
    r"|(?<!\w)(?:понедельник|вторник|четверг|пятниц|суббот|воскресен)\w*"
    r"|(?<!\w)сред(?:[аыуе]\w*|ой|ою)(?!\w)"
    r"|" + _NUMERAL_SIGN +
    r"|(?<![\w.])\d{1,2}-?(?:го|е|му)(?!\w)"
    r"|(?<!\w)(?:сегодня|завтра|послезавтра|недел\w*|месяц\w*|квартал\w*|полгод\w*|полугоди\w*"
    r"|кон(?:ец|ца|цу|це)|середин\w*|начал[аеу]|течени\w*|год[ау]?|q[1-4]|дн(?:ей|я)|сут(?:ки|ок)|час(?:а|ов)?)(?!\w)"
    r"|(?<!\d)\d+\s*(?:-?го\s+)?(?:дн\w*|числ\w*|недел\w*|месяц\w*|год\w*|лет)")
# Знак долгого срока: неразобранная строка с ним держится LONG_CAP_DAYS («через квартал», «к осени»).
# Число рядом с единицей (до двух слов в любую сторону: «13 месяцев», «месяц — максимум три») —
# долгий, если число × единица дальше потолка; счёт без единицы («два источника») — нет.
_LONG_SIGN = re.compile(r"(?<!\w)(?:квартал\w*|полгод\w*|полугоди\w*|год(?:а|у|ом|ы|ов)?|лет|осен\w*|зим\w*"
                        r"|весн\w*|лет(?:о|а|у|ом)|месяц(?:а|ев|ам|ами|ах))(?!\w)")
# Пара «число — единица»: число вплотную к единице («13 месяцев») или единица, связка диапазона и
# число («месяц — максимум три»). Через произвольные слова не считается: «5 серверов за месяц» — счёт.
_COUNT_BEFORE = re.compile(r"(?<!\w)(?P<n>" + _NUMERAL + r")\s+(?P<unit>" + _UNIT_FORMS + r")")
_COUNT_AFTER = re.compile(r"(?<!\w)(?P<unit>" + _UNIT_FORMS + r")" + _RANGE_TAIL)
# Число в строке после единицы срока, не объяснённое разобранным куском, — кандидат верхней границы в
# ближайшей предыдущей единице (Opus, «Как чинить» круга 9): неопознанная пунктуация или связка («месяц,
# возможно, 3», «(максимум 3)», второй хвост) задерживает снятие, а не теряет границу. Граница фразы
# («;», точка) кандидата не обрывает. Число НЕ гасится ни суффиксом, ни маркером (круг 11: гашение «не
# позже 3» как дня месяца потеряло границу «3 месяца»): неоднозначное число даёт кандидата, и строку
# держит поздний из прочтений. Объясняет число только разобранная дата («20 сентября», «10.09»).
# Кандидатом не считаются часть даты или времени по соседним цифрам и проценты. Цена — задержки вроде
# «через неделю, не позднее 20» (20 недель); замер на боевом графе — ниже, в решении 23.09.
_UNIT_OR_NUMBER = re.compile(
    r"(?P<unit>(?<!\w)" + _UNIT_FORMS + r")"
    r"|(?P<num>(?<!\w)(?<!\d[.,/:])(?:\d+[.,]\d+(?!\d)|\d+(?!\d)|" + _COMPOUND + r"(?!\w)|"
    + _by_length(_NUMBER_WORDS) + r"(?!\w))(?![.,/:]\d)(?!\s*%))")
# Единица сразу за числом, слитно или через пробел («3мес», «3 месяца»), — своя единица кандидата. Цифра
# со слитным суффиксом («3й», «2х», «20го») — кандидат, как дефисная; число у знака препинания за буквой
# («макс.3») — тоже; часть даты, времени и десятичной дроби отсекает цифра перед разделителем (Opus, круг 12).
_OWN_UNIT = re.compile(r"\s*(" + _UNIT_FORMS + r")")
# Число за маркером срока — знак, даже если датой не разобралось («до 15», «срок — 20», «не позже 20»).
_MARKER_NUMBER = re.compile(r"(?<!\w)" + _MARKERS + r"\s*[:—–-]?\s*(\d+)(?!\d)")
HORIZON_DAYS = 731                    # окно срока: дальше двух лет от встречи — версия или опечатка, не срок


def config(data: pathlib.Path) -> tuple[pathlib.Path, str]:
    import yaml
    cfg = yaml.safe_load((data / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    s = cfg.get("sufler") or {}
    # Переменные окружения важнее конфига: ими приложение и тесты направляют прогон в песочницу,
    # а не в боевой граф. Относительный путь считается от корня данных, не от текущего каталога.
    raw = os.environ.get("CHAROITE_GRAPH_DIR") or os.environ.get("SUFLER_GRAPH_DIR") or s.get("graph_dir")
    if not raw:
        raise SystemExit("граф не задан: нет sufler.graph_dir и переменных CHAROITE_GRAPH_DIR/SUFLER_GRAPH_DIR")
    path = pathlib.Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = data / path
    return path, str(s.get("user_name") or "")


def md_files(graph: pathlib.Path):
    """Все .md графа без скрытых каталогов и файлов — как enumerator(.skipsHiddenFiles) приложения."""
    for root, dirs, files in os.walk(graph):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".md") and not name.startswith("."):
                yield pathlib.Path(root) / name


def meeting_key(rel: str) -> str | None:
    digits = "".join(ch for ch in rel if ch.isdecimal() and ch.isascii())[:12]
    return digits if len(digits) == 12 else None


def meeting_date(key: str | None) -> dt.date | None:
    if not key:
        return None
    try:
        return dt.date(int(key[:4]), int(key[4:6]), int(key[6:8]))
    except ValueError:
        return None


def writable(rel: pathlib.PurePath) -> bool:
    return any(rel.parts[:len(prefix)] == prefix for prefix in WRITABLE)


def _lenient(year: int, month: int, day: int) -> dt.date:
    """Как Calendar.date(from:): лишний день уходит в следующий месяц (31.09 → 01.10)."""
    return dt.date(year, month, 1) + dt.timedelta(days=day - 1)


def _in_frame(day: int, month: int, anchor: dt.date) -> dt.date:
    """Год срока — от встречи; срок раньше встречи на неделю и больше — следующий год (TaskDue.status)."""
    target = _lenient(anchor.year, month, day)
    if target < anchor - dt.timedelta(days=GRACE_DAYS):
        target = _lenient(anchor.year + 1, month, day)
    return target


def due_of(text: str) -> tuple[int, int] | None:
    """Порт TaskDue.parse: маркер отдельным словом, за ним ДД.ММ. Только для сводки вкладки."""
    for marker in MARKERS:
        start = 0
        while (i := text.find(marker, start)) != -1:
            start = i + len(marker)
            if i > 0 and (text[i - 1].isalpha() or text[i - 1].isdigit()):
                continue
            parts = text[start:start + 5].split(".")
            if len(parts) == 2 and all(p.isascii() and p.isdecimal() for p in parts):
                day, month = int(parts[0]), int(parts[1])
                if 1 <= day <= 31 and 1 <= month <= 12:
                    return day, month
    return None


def overdue_days(due: tuple[int, int], anchor: dt.date | None, today: dt.date) -> int:
    """Порт TaskDue.status: дни просрочки, 0 — срок не прошёл. Без якоря (заметка без даты в пути) —
    год от сегодня, следующий год, если срок прошёл больше чем на полгода."""
    day, month = due
    if anchor:
        target = _in_frame(day, month, anchor)
    else:
        target = _lenient(today.year, month, day)
        if (target - today).days < -183:
            target = _lenient(today.year + 1, month, day)
    days = (target - today).days
    return -days if days < 0 else 0


def _month_no(word: str) -> int:
    return next(n for n, stem in enumerate(_MONTH_STEMS, 1) if word.startswith(stem))


def _last_day(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _frame_or_ref(day: int, month: int, year: str | None, anchor: dt.date, tied: bool) -> dt.date | None:
    """Дата из числа. Год назван — как назван; за маркером без года — как TaskDue.status (раньше встречи
    на неделю — следующий год); без маркера и без года — год встречи. Годится ли дата в сроки, решает
    окно в `due_dates`: прошлое без маркера — ссылка, прошлый год и даль за два года — не срок."""
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    if year:
        return _lenient(int(year) + (2000 if len(year) == 2 else 0), month, day)
    return _in_frame(day, month, anchor) if tied else _lenient(anchor.year, month, day)


def _month_end(month: int, anchor: dt.date, tied: bool) -> dt.date | None:
    target = _last_day(anchor.year, month)
    if target >= anchor - dt.timedelta(days=GRACE_DAYS):
        return target
    return _last_day(anchor.year + 1, month) if tied else None


def _number(word: str | None) -> float:
    """Число из цифр, десятичное («2,5» — так пишет распознавание речи) или слово таблицы
    `_NUMBER_WORDS`; без числа — один."""
    if not word:
        return 1
    if word[0].isdigit():
        return float(word.replace(",", "."))
    return sum(_NUMBER_WORDS.get(part, 0) for part in word.split()) or 1


def _unit_days(unit: str) -> int:
    return next(days for stem, days in _UNIT_DAYS if unit.startswith(stem))


def _range_ok(m: re.Match) -> bool:
    """Хвост — опознанный диапазон: дефис вплотную («месяц-два»), «другой» (диапазон по смыслу слова)
    или число со своей единицей за любым разделителем («или 10 дней», «до 3 месяцев»). Число без
    единицы — не граница диапазона (Sonnet, круг 10: «максимум 3 годных варианта»): его берёт кандидат
    `_UNIT_OR_NUMBER` с тем же днём, и граница не теряется."""
    return bool(m["adj"] or m["n"].startswith("друг") or m["u"])


def _range_after(low: str, pos: int) -> re.Match | None:
    m = _RANGE_AFTER.match(low, pos)
    return m if m and _range_ok(m) else None


def _parsed_spans(low: str, anchor: dt.date) -> list[tuple[int, int, dt.date | None, bool]]:
    """Разобранные сроки строки: (начало, конец, дата, покрывает ли знак). Диапазон за единицей срока
    знак не покрывает, но отдаёт верхнюю границу: строка держится до неё (круг 7)."""
    tied = lambda start: bool(_TIED.search(low[:start]))
    out: list[tuple[int, int, dt.date | None, bool]] = []
    for m in _NUM_DATE.finditer(low):
        out.append((m.start(), m.end(), _frame_or_ref(int(m[1]), int(m[2]), m[3], anchor, tied(m.start())), True))
    for m in _ISO_DATE.finditer(low):                 # год назван — маркер не важен, решает окно срока
        out.append((m.start(), m.end(), _frame_or_ref(int(m[3]), int(m[2]), m[1], anchor, False), True))
    day_months = []
    for m in _DAY_MONTH.finditer(low):
        day_months.append((m.start(), m.end()))
        out.append((m.start(), m.end(), _frame_or_ref(int(m[1]), _month_no(m[2]), None, anchor, tied(m.start())), True))
    for m in _MONTH_WORD.finditer(low):
        if any(a <= m.start() and m.end() <= b for a, b in day_months):
            continue                                  # «3 октября» уже разобрано днём, не концом месяца
        # «до конца июля», «к началу октября»: слово перед месяцем — часть того же срока
        pre = re.search(r"(?<!\w)(?:кон(?:ец|ца|цу|це)|начал[аеу]|середин[аеуы])\s+$", low[:m.start()])
        start = pre.start() if pre else m.start()
        out.append((start, m.end(), _month_end(_month_no(m[1]), anchor, tied(start)), True))
    for m in _PERIOD_END.finditer(low):
        out.append((m.start(), m.end(), {
            "месяца": _last_day(anchor.year, anchor.month),
            "квартала": _last_day(anchor.year, (anchor.month - 1) // 3 * 3 + 3),
            "года": dt.date(anchor.year, 12, 31),
            "недели": anchor + dt.timedelta(days=6 - anchor.weekday())}.get(m[1], anchor), True))
    for m in _QUARTER.finditer(low):
        quarter = {"i": 1, "ii": 2, "iii": 3, "iv": 4}.get(m[1] or "") or int(m[1] or m[2])
        out.append((m.start(), m.end(), _month_end(quarter * 3, anchor, True), True))
    for m in _SPAN.finditer(low):
        unit = _unit_days(m[2])
        days, end, rng = _number(m[1]) * unit, m.end(), _range_after(low, m.end())
        if rng:                                       # «через месяц-два», «неделю — другую»: до верхней
            upper = 2 * days if rng["n"].startswith("друг") else _number(rng["n"]) * (
                _unit_days(rng["u"]) if rng["u"] else unit)
            days, end = max(days, upper), rng.end()
        out.append((m.start(), end, anchor + dt.timedelta(days=min(int(days), HORIZON_DAYS + 1)), rng is None))
    for m in _NEXT.finditer(low):
        if m[1] == "недел":
            found = anchor + dt.timedelta(days=13 - anchor.weekday())
        elif m[1] == "год":
            found = dt.date(anchor.year + 1, 12, 31)
        elif m[1] == "полугоди":
            found = dt.date(anchor.year + 1, 6, 30) if anchor.month > 6 else dt.date(anchor.year, 12, 31)
        else:
            months = 1 if m[1] == "месяц" else (3 - (anchor.month - 1) % 3) + 2
            y, mo = divmod(anchor.month - 1 + months, 12)
            found = _last_day(anchor.year + y, mo + 1)
        out.append((m.start(), m.end(), found, True))
    for m in _THIS_WEEK.finditer(low):
        out.append((m.start(), m.end(), anchor + dt.timedelta(days=6 - anchor.weekday()), True))
    for m in _NEAR.finditer(low):
        forms = _WEEKDAY_CASE.get({"ко": "к", "во": "в"}.get(m[1], m[1]), ())
        if m[2] in forms:
            out.append((m.start(2), m.end(2),
                        anchor + dt.timedelta(days=(forms.index(m[2]) - anchor.weekday()) % 7 or 7), True))
    for m in _DAYWORD.finditer(low):
        out.append((m.start(), m.end(),
                    anchor + dt.timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[m[1]]), True))
    for m in _TIME_AFTER.finditer(low):
        out.append((m.start(1), m.end(1), anchor, True))
    return out


def due_dates(text: str, anchor: dt.date) -> tuple[list[dt.date], bool, bool]:
    """(Даты строки в окне срока, есть ли знак времени без покрытия, есть ли среди таких знак долгого
    срока.)

    В сроки идёт только дата в окне: не раньше встречи на неделю и не дальше `HORIZON_DAYS`. Всё
    прочее — ссылка на прошлое, прошлый год, номер версии — знак не покрывает и ведёт в потолок, а не
    в правило возраста и не в срок. Одно окно на все разборщики (круг 6). Граница диапазона — в датах,
    но знак не покрывает (круг 7)."""
    low = text.lower()
    for table in (_NO_DUE, _NOT_TIME):
        low = table.sub(lambda m: " " * len(m.group(0)), low)
    lo, hi = anchor - dt.timedelta(days=GRACE_DAYS), anchor + dt.timedelta(days=HORIZON_DAYS)
    good = [span for span in _parsed_spans(low, anchor) if span[2] is not None and lo <= span[2] <= hi]
    inside = lambda s0, s1, spans: any(a <= s0 and s1 <= b for a, b, *_ in spans)
    signs = [s.span() for s in _SIGN.finditer(low)] + [m.span(1) for m in _MARKER_NUMBER.finditer(low)]
    open_signs = [(s0, s1) for s0, s1 in signs if not inside(s0, s1, [g for g in good if g[3]])]
    # Долгий знак — только тот, чью величину не объясняет разобранный кусок: граница диапазона уже в
    # датах («через месяц — максимум три» держится до своей верхней, а не до LONG_CAP_DAYS).
    # объяснено — значит само число лежит в разобранном куске (с хвостом диапазона), круг 8
    counts = ([(m["n"], m["unit"], m.span("n")) for m in _COUNT_BEFORE.finditer(low)]
              + [(m["n"], m["u"] or m["unit"], m.span("n")) for m in _COUNT_AFTER.finditer(low)
                 if _range_ok(m)])
    size = lambda n, unit: (2 if n.startswith("друг") else _number(n)) * _unit_days(unit)
    long_sign = bool(open_signs) and (
        any(_LONG_SIGN.search(low[s0:s1]) for s0, s1 in open_signs if not inside(s0, s1, good))
        or any(size(n, u) + OVERDUE_DAYS > CAP_DAYS for n, u, (n0, n1) in counts if not inside(n0, n1, good)))
    # число после единицы срока в той же фразе, не объяснённое куском, — кандидат верхней границы (круг 9)
    extra, unit_days = [], None
    for m in _UNIT_OR_NUMBER.finditer(low):
        if m["unit"]:
            unit_days = _unit_days(m["unit"])
            continue
        # своя единица («3мес», «3 месяца») — ещё одно прочтение рядом с единицей срока, не замена:
        # «через неделю 2 дня» держит и 2 недели, как в круге 11 (круг 12)
        own = _OWN_UNIT.match(low, m.end("num"))
        for days in (unit_days, _unit_days(own[1]) if own else None):
            if days and not inside(*m.span("num"), good):
                extra.append(anchor + dt.timedelta(days=min(int(_number(m["num"]) * days), HORIZON_DAYS + 1)))
    # кандидат за окном — открытый долгий знак, как разобранный кусок за окном (Opus C1, круг 10)
    far = any(d > hi for d in extra)
    return ([found for _, _, found, _ in good] + [d for d in extra if d <= hi],
            bool(open_signs) or far, long_sign or far)


def kind_of(text: str, owner: str) -> str:
    """«мои», «чужие» или «ничьё». Порт TasksScreenPolicy.isMine: исполнитель — ведущий жирный блок,
    слово имени владельца (3+ букв) целым словом; метка диаризации — граф Чароита, не своя копия."""
    m = LEAD_BOLD.match(text)
    if not m:
        return "ничьё"
    name = m.group(1).strip()
    if graph_names.is_speaker_placeholder(graph_names.strip_speaker_label(name)):
        return "ничьё"
    for word in (w for w in owner.split() if len(w) >= 3):
        hit = re.search(re.escape(word), name, re.IGNORECASE)
        if hit:
            before = name[hit.start() - 1] if hit.start() else ""
            after = name[hit.end()] if hit.end() < len(name) else ""
            if not before.isalpha() and not after.isalpha():
                return "мои"
    return "чужие"


def decide(text: str, anchor: dt.date, today: dt.date, owner: str, kind: str | None = None) -> str | None:
    """Причина снять («срок» / «ничьё» / «давно») или None — поручение остаётся открытым."""
    if CLOSED_MARK.search(text):
        return None                                   # человек вернул снятое, пометку оставил
    kind = kind or kind_of(text, owner)
    age = (today - anchor).days
    if kind == "ничьё":
        return "ничьё" if age >= NOOWNER_DAYS else None
    dates, unparsed, long_sign = due_dates(text, anchor)
    if unparsed:
        cap = LONG_CAP_DAYS if long_sign else CAP_DAYS
        if age <= cap or (dates and (today - max(dates)).days < OVERDUE_DAYS):
            return None                               # держит поздний из двух: потолок и любая граница
        return "давно"
    if age <= (MINE_DAYS if kind == "мои" else OTHERS_DAYS):
        return None                                   # срок только откладывает снятие, не ускоряет (круг 7)
    if dates:
        return "срок" if (today - max(dates)).days >= OVERDUE_DAYS else None
    return "срок"


def _norm(text: str) -> str:
    """TasksService.normalizedTaskText: без **, нижний регистр, пробелы схлопнуты."""
    return " ".join(text.replace("**", "").lower().split())


def task_key(meeting: str | None, text: str) -> str:
    return f"{meeting}\0{_norm(CLOSED_MARK.sub('', text))}"


def load_returned(base: pathlib.Path) -> set[str]:
    """Реестр возвращённого (`returned.jsonl` пишет restore.py): эти поручения больше не снимаются.
    Байты режутся по строкам до декодирования, как манифест в restore.py: хвост, оборванный посреди
    буквы, пропускается, а не роняет каждый следующий прогон."""
    keys = set()
    try:
        lines = (base / "returned.jsonl").read_bytes().split(b"\n")
    except FileNotFoundError:
        return keys
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except ValueError:                            # и UnicodeDecodeError: оборванная запись отката
            continue
        if isinstance(row, dict) and isinstance(row.get("old"), str) and isinstance(row.get("file"), str):
            b = BOX.match(row["old"])
            if b:
                keys.add(task_key(meeting_key(row["file"]), b.group(2)))
    return keys


def meeting_of(rel: str) -> str | None:
    """Порт TasksService.meetingOf: встреча у файла — только в каталогах встреч."""
    return meeting_key(rel) if rel.startswith(MEETING_ROOTS) else None


def same_task_key(text: str) -> str:
    """Порт TasksService.sameTaskKey: без «⚠ не участник (Имя):», отметок снятия и пометки моста,
    слова в нижнем регистре через пробел."""
    s = _WITHDRAWN.sub(" ", _OUTSIDER.sub(" ", text))
    for mark in BRIDGE_MARKS:
        s = s.replace(mark, " ")
    return " ".join(_WORD.findall(s.lower()))


def canonical_items(items, known: list[tuple[str, str]]):
    """Порт TasksService.canonicalItems (Чароит 0.84.1, №367): пункты минуток — всегда; пункт другого
    файла встречи прячется, если минутки его знают — задачей, снятым или строкой «не участник»;
    одинаковые копии вне минуток — одна, папка архива важнее; пустой ключ ни с чем не склеивается."""
    minute_keys = set()
    for rel, text in [(it[0], it[2]) for it in items if it[1]] + known:
        key, meeting = same_task_key(text), meeting_of(rel)
        if key and meeting:
            minute_keys.add((meeting, key))
    rank = lambda rel: 0 if rel.startswith("Встречи-архив/") else 1 if rel.startswith("Встречи/") else 2
    seen, kept = set(), set()
    for i in sorted(range(len(items)), key=lambda i: (rank(items[i][0]), i)):
        rel, is_min, text, done, _ = items[i]
        words, meeting = same_task_key(text), meeting_of(rel)
        if meeting is None or is_min or not words:
            kept.add(i)
        elif (meeting, words) not in minute_keys and (meeting, words, done) not in seen:
            seen.add((meeting, words, done))
            kept.add(i)
    return [items[i] for i in sorted(kept)]


def tab_summary(items, known: list[tuple[str, str]], owner: str, today: dt.date) -> dict[str, int]:
    """Вкладка Mac: склейка `canonical_items` и split (Мои / Старые). Пункт — (путь, из минуток,
    текст, отмечен, дата файла); дата поручения — штамп в пути, как `happenedAt` приложения."""
    opened = [it for it in canonical_items(items, known) if not it[3]]
    mine = stale = 0
    for rel, _, text, _, mtime in opened:
        if kind_of(text, owner) == "мои":
            mine += 1
            continue
        anchor = meeting_date(meeting_key(rel))
        due = due_of(text)
        if due:
            stale += overdue_days(due, anchor, today) >= OVERDUE_DAYS
        else:
            stale += (anchor or mtime) < today - dt.timedelta(days=TAB_STALE_DAYS)
    return {"во вкладке": len(opened), "Мои": mine, "Старые": stale}


def _fence_map(lines: list[str]) -> list[bool]:
    """Строка внутри код-блока (или сама его граница). Блок закрывает только тот же символ, не
    короче открывающего, без хвоста после маркера; для открытия с отступом 0..3 закрывает отступ
    0..3, для вложенного открытия (4+) — отступ от открытия до +3 (не строка верхнего уровня):
    ``` и ````python внутри ````-блока и строка с 4+ пробелами в блоке нулевого уровня — текст."""
    inside, opener, opener_indent, out = False, "", 0, []
    for line in lines:
        m = FENCE.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            marker = m.group(2)
            tail = line[m.end():]
            if not inside:
                if marker[0] != "`" or "`" not in tail:
                    inside, opener, opener_indent = True, marker, indent
                    out.append(True)
                    continue
            else:
                indent_ok = indent <= 3 if opener_indent <= 3 else opener_indent <= indent <= opener_indent + 3
                if marker[0] == opener[0] and len(marker) >= len(opener) and not tail.strip() and indent_ok:
                    inside, opener, opener_indent = False, "", 0
                    out.append(True)
                    continue
        out.append(inside)
    return out


def _kinds_with_parents(lines: list[str], owner: str, fenced: list[bool] | None = None) -> dict[int, str]:
    """Исполнитель каждого пункта: свой ведущий жирный блок или, у вложенного без имени, родителя."""
    if fenced is None:
        fenced = _fence_map(lines)
    kinds: dict[int, str] = {}
    stack: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        if HEADING.match(line):
            stack.clear()
            continue
        m = ANY_BOX.match(line)
        if not m:
            continue
        indent = len(m.group(1).expandtabs(4))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        kind = kind_of(m.group(2), owner)
        if kind == "ничьё" and stack and not LEAD_BOLD.match(m.group(2)):
            kind = stack[-1][1]
        kinds[i] = kind
        stack.append((indent, kind))
    return kinds


def scan(graph: pathlib.Path, owner: str, today: dt.date, returned: set[str] = frozenset()):
    """Один проход по графу: план правок, счёт снимаемого (по канону встречи) и сводка вкладки."""
    files, minutes_meetings = [], set()
    for path in md_files(graph):
        rel = path.relative_to(graph)
        key = meeting_key(str(rel))
        try:
            text = path.read_text(encoding="utf-8")
            mtime = dt.date.fromtimestamp(path.stat().st_mtime)
        except (OSError, UnicodeDecodeError):
            continue                                  # приложение такой файл тоже не читает
        if key and path.name == MINUTES:
            minutes_meetings.add(key)
        if "- [" in text or path.name == MINUTES:      # минутки знают пункт и без чекбоксов (KNOWN)
            files.append((path, rel, key, text.split("\n"), mtime))
    plan: dict[pathlib.Path, list[tuple[int, str, str]]] = {}
    stats: collections.Counter = collections.Counter()
    items, known = [], []
    for path, rel, key, lines, mtime in files:
        anchor = meeting_date(key)
        is_min = path.name == MINUTES
        canonical = is_min or key not in minutes_meetings
        can_write = anchor is not None and writable(rel)
        fenced = _fence_map(lines)
        kinds = _kinds_with_parents(lines, owner, fenced) if can_write else {}
        for i, line in enumerate(lines):
            m = TODO.match(line)
            if m:
                items.append((str(rel), is_min, m.group(2), m.group(1) != " ", mtime))
            elif is_min and (k := KNOWN.match(line)):
                known.append((str(rel), k.group(1)))
            b = BOX.match(line)
            if not b or not can_write or fenced[i]:
                continue
            text = b.group(2)
            if task_key(key, text) in returned or CLOSED_MARK.search(text):
                stats["возвращено"] += canonical
                continue
            why = decide(text, anchor, today, owner, kinds.get(i))
            if why is None:
                if canonical and kinds.get(i) != "ничьё":
                    stats["срок не разобран"] += due_dates(text, anchor)[1]
                continue
            mine = kinds.get(i) == "мои"
            plan.setdefault(path, []).append(
                (i, line, f"{b.group(1)}[-] {text.rstrip()} _({REASONS[why]} {today:%d.%m})_", (why, canonical, mine)))
            if canonical:
                stats[why] += 1
                stats["ваших"] += mine
    return plan, stats, tab_summary(items, known, owner, today)


def live_meeting(data: pathlib.Path) -> bool:
    if live_gate.daemon_alive(data):
        return True
    now = time.time()
    for pcm in (data / "recordings").glob("*.pcm"):
        try:
            if now - pcm.stat().st_mtime < LIVE_PCM_SECONDS:
                return True
        except OSError:
            continue                                  # запись закрылась между glob и stat
    return False


def notify(text: str) -> None:
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "Чароит — задачи"'],
                   capture_output=True, check=False)


def _unique_index(lines: list[str], wanted: str, fenced: list[bool]) -> int | None:
    hits = [j for j, line in enumerate(lines) if line == wanted and not fenced[j]]
    return hits[0] if len(hits) == 1 else None


def apply(graph: pathlib.Path, plan, data: pathlib.Path, note=lambda line: None, guard=None, on_error=None):
    """Записать план под замком графа. Возвращает (записанные правки, пропущено строк, каталог прогона).

    Под замком реестр возврата перечитывается: откат, прошедший между сканом и записью, выигрывает.
    Файл переписывает `safe_write.rewrite_file` (снимок до чтения, expect перед replace, две попытки);
    строка ищется по номеру, при сдвиге — единственная такая же вне код-блоков. Манифест пишется
    как журнал «намерение — сделано»: намерение пишется до файла, откат берёт только подтверждённое;
    копия файла — в files/.

    `note(line)` — журнал хода (любой пропуск, старт записи). `on_error(detail)` — машинный сигнал
    настоящего сбоя ввода-вывода/кодировки файла (`copy2`, `OSError`, `UnicodeDecodeError`,
    `LostRace.UNREACHABLE`); запись остальных файлов продолжается. Штатные гонки (`LostRace.CHANGED`,
    `LostRace.GONE`) и симлинк вне графа пишутся в `note` без `on_error`."""
    # Симлинк графа могут перецепить, пока ждём замок: пишем и откатываем ровно те пути,
    # которые видели при планировании, а не то, куда теперь ведёт изменяемый псевдоним.
    graph = pathlib.Path(graph).resolve()
    resolved: dict[pathlib.Path, list] = {}
    for planned, edits in plan.items():
        real = pathlib.Path(planned).resolve()
        try:
            real.relative_to(graph)
        except ValueError:
            note(f"пропуск {planned}: путь вне графа после раскрытия ссылок")
            continue
        if real in resolved:
            seen_pairs = {(i, old) for i, old, *_ in resolved[real]}
            for item in edits:
                if (item[0], item[1]) not in seen_pairs:
                    resolved[real].append(item)
                    seen_pairs.add((item[0], item[1]))
            note(f"объединение плана {planned} → {real.relative_to(graph)}: две ссылки на один файл")
        else:
            resolved[real] = list(edits)
    plan = resolved
    base = charoite_paths.graph_backups(graph, "tasks_control", root=data)
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=data))
    # Микросекунды и pid в имени: два прогона в одну секунду иначе делили бы каталог,
    # манифест и копии файлов, и откат одного возвращал бы правки другого.
    backup = charoite_paths.secure_dir(
        base / f"{dt.datetime.now():%Y%m%d-%H%M%S-%f}-{os.getpid()}")
    (backup / "run.json").write_text(json.dumps(
        {"graph": str(graph), "lock_dir": str(lock_dir), "code": str(CODE),
         "returned": str(base / "returned.jsonl")}, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(CODE / "scripts" / "restore.py", backup / "restore.py")
    try:                                          # хелперы рядом: откат переживёт удаление checkout
        lib = backup / "lib"
        (lib / "charoite_graph").mkdir(parents=True, exist_ok=True)
        shutil.copy2(CODE / "src" / "file_locks.py", lib / "file_locks.py")
        # safe_write кладём ВНУТРЬ пакета: откат импортирует члена пакета только через
        # charoite_graph (границы импортов), и без исходного checkout пакет берётся из lib/.
        shutil.copy2(CODE / "src" / "charoite_graph" / "safe_write.py",
                     lib / "charoite_graph" / "safe_write.py")
        (lib / "charoite_graph" / "__init__.py").write_text("", encoding="utf-8")
    except OSError as e:
        # Снимок — часть гарантии отката: без него граф не трогаем (нельзя обещать откат,
        # которого не будет, если checkout исчезнет).
        raise RuntimeError(f"снимок хелперов отката не скопирован: {type(e).__name__}: {e} — не пишу") from e
    manifest = backup / "manifest.jsonl"
    manifest.touch()
    applied, skipped = [], 0
    with file_locks.graph_lock(lock_dir, LOCK_WAIT) as taken:
        if not taken:
            raise RuntimeError(f"замок графа не взят за {LOCK_WAIT} с — не пишу")
        def _note(line: str) -> None:
            try:
                note(line)
            except OSError:                               # сбой лога не должен обрывать цикл по файлам
                pass

        note(f"запись начата; откат: {backup / 'restore.py'}")
        returned = load_returned(base)
        pending = list(plan.items())
        for index, (path, edits) in enumerate(pending):
            if guard is not None and not guard():
                # ожидание замка могло длиться минуты, а встреча начаться в любой момент записи:
                # проверка перед каждым файлом, быстрая часть гейта — последней
                _note("пропуск: гейт не пропустил запись (встреча, занятость или нечитаемый сигнал) — "
                      "не пишу дальше")
                skipped += sum(len(items) for _, items in pending[index:])
                break
            rel = str(path.relative_to(graph))
            key = meeting_key(rel)
            edits = [e for e in edits if task_key(key, BOX.match(e[1]).group(2)) not in returned]
            if not edits:
                continue
            dst = backup / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(path, dst)
            except OSError as e:
                if isinstance(e, FileNotFoundError) and not path.exists():
                    _note(f"пропуск {rel}: файл исчез до записи ({e})")
                    continue
                detail = f"{rel}: {type(e).__name__}: {e}"
                _note(f"пропуск {detail}")
                if on_error is not None:
                    on_error(detail)
                skipped += len(edits)
                continue
            done: list = []

            def transform(before: str, edits=edits, done=done) -> tuple[str, int]:
                done.clear()
                lines = before.split("\n")
                fenced = _fence_map(lines)
                for i, old, new, meta in edits:
                    ok = i < len(lines) and lines[i] == old and not fenced[i]
                    j = i if ok else _unique_index(lines, old, fenced)
                    if j is not None:
                        lines[j] = new
                        done.append((j, old, new, meta))
                return "\n".join(lines), len(done)

            def _mark_not_applied(missed) -> None:
                if not missed:
                    return
                try:
                    with manifest.open("a", encoding="utf-8") as f:
                        for idx, old, new, *_ in missed:
                            f.write(json.dumps({"file": rel, "line": idx + 1, "old": old, "new": new,
                                                "state": "not_applied"}, ensure_ascii=False) + "\n")
                except OSError:
                    pass

            # Журнал до записи, подтверждение — после. «Намерение» (state=intent) пишется ДО
            # перезаписи файла: обрыв между записью и журналом иначе оставлял правку без следа
            # в откате. Откатывается только подтверждённое (state=done): откат «намерения»
            # мог бы вернуть чужую уже закрытую строку с тем же текстом, которая к прогону
            # отношения не имеет. Штатно неприменённые строки закрываются state=not_applied,
            # а у прерванного прогона голые intent-записи остаются в отчёте («не подтверждено»).
            with manifest.open("a", encoding="utf-8") as f:
                for i, old, new, _ in edits:
                    f.write(json.dumps({"file": rel, "line": i + 1, "old": old, "new": new,
                                        "state": "intent"}, ensure_ascii=False) + "\n")
            try:
                n = safe_write.rewrite_file(path, transform, "снятие поручений")
            except safe_write.LostRace as e:
                detail = f"{rel}: {type(e).__name__}: {e}"
                _note(f"пропуск {detail}")
                if e.unreachable:
                    if on_error is not None:
                        on_error(detail)
                    skipped += len(edits)
                else:
                    _mark_not_applied(edits)
                continue
            except (OSError, UnicodeDecodeError) as e:
                detail = f"{rel}: {type(e).__name__}: {e}"
                _note(f"пропуск {detail}")
                if on_error is not None:
                    on_error(detail)
                skipped += len(edits)
                continue
            if n:
                with manifest.open("a", encoding="utf-8") as f:
                    for j, old, new, _ in done:
                        f.write(json.dumps({"file": rel, "line": j + 1, "old": old, "new": new,
                                            "state": "done"}, ensure_ascii=False) + "\n")
                applied.extend(meta for *_, meta in done)
            if n < len(edits):                            # строка успела измениться — не сбой записи, без тревоги
                done_Left = collections.Counter((old, new) for _, old, new, _ in done)
                missed = []
                for item in edits:
                    pair = (item[1], item[2])
                    if done_Left[pair] > 0:
                        done_Left[pair] -= 1
                    else:
                        missed.append(item)
                _mark_not_applied(missed)
                _note(f"пропуск {rel}: строки уже изменены или неоднозначны ({len(edits) - n})")
    return applied, skipped, backup


def summary_line(stats, tab, applied: bool) -> str:
    verb = "снято" if applied else "будет снято"
    return (f"{verb}: по сроку {stats['срок']}, ничьих {stats['ничьё']}, давно {stats['давно']} "
            f"(ваших {stats['ваших']}); "
            f"во вкладке {tab['во вкладке']} (Мои {tab['Мои']}, Старые {tab['Старые']})")


def _selftest_rules() -> list[str]:
    today = dt.date(2026, 9, 23)                      # среда
    d = lambda n: today - dt.timedelta(days=n)
    cases = [
        ("**Иван** — сделать", 15, "срок"), ("**Иван** — сделать", 14, None),
        ("**Коля** — сделать", 8, "срок"), ("**Коля** — сделать", 7, None),
        ("**Собеседник 2** — сделать", 2, "ничьё"), ("**Собеседник 2** — сделать", 1, None),
        ("сделать без исполнителя", 2, "ничьё"), ("**Саша (Спикер 1)** — сделать", 3, None),
        # срок снятие только откладывает (круг 7): «Мои» с просрочкой 13 дней живут до правила возраста
        ("**Иван** — сделать — до 10.09", 11, None), ("**Иван** — сделать — до 10.09", 15, "срок"),
        # срок раньше встречи больше чем на неделю — следующий год, как в TaskDue.status
        ("**Иван** — сделать — до 10.09", 3, None), ("**Коля** — сделать — до 20.09", 20, None),
        ("**Коля** — сделать — до 17.09", 3, None), ("**Коля** — сделать — до 30.09", 30, None),
        ("**Ирина** — сделать", 8, "срок"), ("**Иван/Сергей** — сделать", 8, None),
        ("**Коля** — установку в понедельник 10.09", 3, None),
        # сроки, которых не видит чип приложения: писатель не снимает поручение до срока
        ("**Коля** — отчёт до 30 сентября", 10, None), ("**Коля** — до 5.10 прислать", 8, None),
        ("**Коля** — до конца месяца", 21, None), ("**Коля** — сделать в октябре", 8, None),
        ("**Коля** — к понедельнику — 18.09, крайний срок 21.09", 11, None),
        # короткие сроки проходят раньше возрастного порога — снимаем, как по возрасту
        ("**Коля** — до конца недели", 13, "срок"), ("**Коля** — к понедельнику", 13, "срок"),
        # срок назван, но не разобран — не снимаем
        ("**Коля** — до конца спринта", 13, None), ("**Коля** — до 15", 13, None),
        # «в 2 этапа», «к Пете» — не сроки
        ("**Коля** — сделать в 2 этапа", 8, "срок"), ("**Коля** — зайти к Пете", 8, "срок"),
        # невозможная дата уходит вперёд, как у Calendar: 31.06 → 01.07
        ("**Коля** — до 31.06", 95, "срок"),
        # «завтра до 13:00» — срок на день позже встречи, а не в её день
        ("**Коля** — связаться — завтра до 13:00", 7, None), ("**Коля** — связаться — завтра до 13:00", 8, "срок"),
        # человек вернул снятое и оставил пометку
        ("**Коля** — сделать _(снято по сроку 01.09)_", 20, None),
        ("**Коля** — сделать _(снято: давно, срок не разобран 01.09)_", 70, None),
        # круг 2: срок не сразу за маркером, падежи, «в среде» — не среда, ссылки на прошлое
        ("**Коля** — отчёт, срок: 30.10", 8, None), ("**Коля** — отчёт (срок — 30.10)", 8, None),
        ("**Коля** — подготовить к марту", 8, None), ("**Иван** — развернуть в тестовой среде", 9, None),
        ("**Коля** — сделать отчёт, срок сдачи 30 сентября", 10, None),
        ("**Коля** — с 1 октября перейти", 8, None), ("**Коля** — через две недели", 8, None),
        ("**Коля** — в течение месяца", 20, None), ("**Коля** — на следующей неделе", 20, "срок"),
        # ссылка на прошлое и «версия похожа на дату» держатся до потолка: ошибка фильтра ссылок
        # задерживает снятие, но не ускоряет (Opus, круг 3)
        ("**Коля** — сравнить с 24/27 июля", 8, None), ("**Коля** — обновить Astra 1.7 → 1.8", 8, None),
        ("**Коля** — сравнить с 24/27 июля", 91, "давно"),
        ("**Коля** — увеличить в 1.5 раза", 8, "срок"),
        ("**Коля** — перевести ресурсы (срок: II квартал)", 8, None),
        ("**Коля** — сделать до 10.09.2026", 20, "срок"),
        # за маркером срок раньше встречи — следующий год; без маркера прошлая дата — ссылка, не срок
        ("**Коля** — сделать до 20.08", 20, None), ("**Иван** — сравнить с отчётом 20.08", 10, None),
        ("**Коля** — сделать до 01.07", 65, None), ("**Коля** — ресурсы (срок: II квартал)", 65, None),
        ("**Коля** — ускорить в 1.12 раза", 8, "срок"),
        # «до конца недели» у «Моих» не снимает раньше 14 дней: срок только откладывает (круг 7)
        ("**Иван** — до конца недели", 10, None), ("**Иван** — до конца недели", 15, "срок"),
        # круг 3: ближний срок не отменяет неразобранный дальний; месяц за «срок:»; дальние формы
        ("**Коля** — сегодня отправить заявку, до конца спринта согласовать", 8, None),
        ("**Коля** — сегодня отправить заявку, до конца спринта согласовать", 91, "давно"),
        ("**Коля** — завтра созвон, итог до 15 числа", 8, None),
        ("**Коля** — к понедельнику черновик, финал в следующем полугодии", 8, None),
        ("**Коля** — отчёт, срок: январь", 8, None), ("**Коля** — дедлайн — март", 8, None),
        ("**Коля** — сдать не позднее января", 8, None), ("**Коля** — перенести на февраль", 8, None),
        ("**Коля** — через полгода проверить", 8, None), ("**Коля** — через год", 8, None),
        ("**Коля** — в следующем году", 8, None), ("**Коля** — в 2027 году", 8, None),
        ("**Коля** — в Q4", 8, None), ("**Коля** — до 2026-11-15", 8, None),
        ("**Коля** — через пару недель", 8, None), ("**Коля** — через десять дней", 8, None),
        ("**Коля** — через две недели", 30, "срок"),
        ("**Коля** — выгрузить данные с марта по июнь", 8, None),
        # «по июнь» за маркером читается как срок следующего года — держит строку (ошибка только задерживает)
        ("**Коля** — выгрузить данные с марта по июнь", 65, None),
        ("**Коля** — написать в Майкрософт", 8, "срок"), ("**Коля** — сдать отчёт 15.01", 8, None),
        ("**Коля** — до 5-го февраля", 8, None), ("**Коля** — провести разбор, срок не назван", 8, "срок"),
        # «до конца июля» — один знак: месяц за маркером, следующий год; покрыт разобранной датой
        ("**Коля** — согласовать до конца июля", 8, None), ("**Коля** — до конца июля", 400, "срок"),
        # круг 4: неразобранный знак не снимает раньше названного дальнего срока
        ("**Коля** — до 15.12 внедрить, статус каждую неделю", 61, None),
        ("**Коля** — до 15.12 внедрить, статус каждую неделю", 296, "давно"),
        ("**Коля** — через 5 лет вернуться к архитектуре", 8, None), ("**Коля** — через пару лет вернуться", 8, None),
        ("**Коля** — через тридцать дней сдать", 10, None), ("**Коля** — через месяц-два", 40, None),
        ("**Коля** — вернуться к вопросу осенью", 8, None), ("**Коля** — сдать не позднее 25", 8, None),
        ("**Коля** — дедлайн 15 окт", 8, None), ("**Коля** — после майских", 8, None),
        ("**Коля** — сверить с выгрузкой 2025-12-01", 8, None),
        ("**Коля** — обновить до версии 3.4.35", 91, "давно"),
        # «3 сентября» — день, а не конец месяца (Sonnet, круг 4)
        ("**Коля** — обсудим 3 сентября", 29, "срок"),
        ("**Коля** — развернуть в тестовой среде", 8, "срок"),
        ("**Коля** — через 2 часа прислать", 8, "срок"),
        # дальний знак без разбора держит строку, но не вечно
        ("**Коля** — в следующем полугодии", 20, None), ("**Коля** — до 15", 91, "давно"),
        # круг 5: «среда» — знак в любой форме; место «в … среде» и «среда разработки» гасит таблица
        ("**Коля** — к следующей среде подготовить", 8, None), ("**Коля** — к следующей среде подготовить", 91, "давно"),
        ("**Коля** — срок: среда", 8, None), ("**Коля** — до ближайшей среды", 8, None),
        ("**Коля** — перенести на среду", 8, None), ("**Коля** — поднять среду разработки", 8, None),
        ("**Коля** — среда разработки готова, проверить", 8, "срок"),
        ("**Коля** — в среду", 8, "срок"), ("**Коля** — в среду", 7, None),
        ("**Коля** — в тестовой среде, созвон к следующей среде", 8, None),
        # круг 5: разобранная дальняя дата — срок, как названа; «3.4.35» без маркера — версия
        ("**Коля** — до 31.12.2027 внедрить", 61, None), ("**Коля** — к 2027-10-01", 61, None),
        ("**Коля** — через 13 месяцев", 61, None), ("**Коля** — через 13 месяцев", 396, None),
        ("**Коля** — через 13 месяцев", 397, "срок"),
        # круг 5: тире после срока — разделитель; диапазон — дефис вплотную или тире с числом
        ("**Коля** — через неделю — прислать отчёт", 13, None), ("**Коля** — через неделю — прислать отчёт", 30, "срок"),
        ("**Коля** — через месяц — два", 40, None), ("**Коля** — через месяц - полтора", 40, None),
        # круг 5: число за маркером с единицей счёта — не срок; «до 15 числа» — срок
        ("**Коля** — собрать до 5 человек", 8, "срок"), ("**Коля** — по 3 пункта на каждого", 8, "срок"),
        ("**Коля** — до 15 числа", 8, None),
        # круг 6: верхняя граница диапазона словом или цифрой — знак, строка держится до потолка
        ("**Коля** — через месяц — максимум два", 40, None), ("**Коля** — через месяц — максимум два", 91, "давно"),
        ("**Коля** — через неделю или две", 15, None), ("**Коля** — через неделю или 2", 15, None),
        ("**Коля** — в течение недели, максимум двух", 15, None),
        # круг 6: окно срока — прошлый год за маркером и даль за два года не срок, а потолок
        ("**Коля** — обновить Python до 3.11.10", 8, None), ("**Коля** — обновить Python до 3.11.10", 91, "давно"),
        ("**Коля** — к 2019-05-01", 8, None), ("**Коля** — до 31.12.2099", 91, "давно"),
        ("**Коля** — обновить до 3.4.35", 91, "давно"), ("**Коля** — через 1000 дней", 91, None),
        ("**Коля** — через 1000 дней", 401, "давно"), ("**Коля** — через 99999999999 дней", 401, "давно"),
        ("**Коля** — заложить бюджет на много лет вперёд", 8, None),
        # круг 7: разобранная нижняя граница держит строку, даже если дальше во фразе есть число
        # круг 9: счётное число после срока — кандидат границы, снятие позже (цена «ошибка только задерживает»)
        ("**Коля** — через 3 месяца перенести 5 сервисов", 156, None),
        ("**Коля** — через 3 месяца перенести 5 сервисов", 157, "срок"),
        ("**Коля** — в течение 2 месяцев мигрировать 10 ТБ", 306, None),
        ("**Коля** — в течение 2 месяцев мигрировать 10 ТБ", 307, "срок"),
        # круг 7: диапазон держит до верхней границы; «другую», «не позже 20-го» — знаки
        ("**Коля** — через 3 месяца или 4", 126, None), ("**Коля** — через 3 месяца или 4", 127, "срок"),
        ("**Коля** — через неделю — другую подготовить отчёт", 20, None),
        ("**Коля** — через неделю, но не позже 20-го", 20, None),
        # круг 7: фантомный срок («в среду тестирования»), ссылка «от 11.09» не снимают раньше возраста
        ("**Коля** — доработать по замечаниям от 11.09", 7, None),
        ("**Коля** — доработать по замечаниям от 11.09", 8, "срок"),
        ("**Иван** — задеплоить в среду тестирования", 14, None),
        ("**Иван** — задеплоить в среду тестирования", 15, "срок"),
        # круг 7: долгие единицы разбираются, долгий знак держит строку LONG_CAP_DAYS
        ("**Коля** — через квартал вернуться", 97, None), ("**Коля** — через квартал вернуться", 98, "срок"),
        ("**Коля** — через полгода", 188, None), ("**Коля** — через полгода", 189, "срок"),
        ("**Коля** — вернуться к вопросу к осени", 91, None),
        ("**Коля** — вернуться к вопросу к осени", 401, "давно"),
        ("**Коля** — через месяц — максимум три", 96, None), ("**Коля** — через месяц — максимум три", 97, "давно"),
        ("**Коля** — в следующем году", 730, "срок"), ("**Коля** — в следующем полугодии", 365, "срок"),
        ("**Коля** — в IV квартале", 357, "срок"),
        # круг 8: связки «возможно», «в худшем случае»; граница со своей единицей; «до» с датой — не граница;
        # без разделителя число — не граница («2 раза»), счёт через слова («5 серверов за месяц») — не пара
        ("**Коля** — через месяц, возможно пять", 156, None), ("**Коля** — через месяц, возможно пять", 157, "давно"),
        ("**Коля** — через 2 месяца, возможно 4", 67, None), ("**Коля** — через 2 месяца, возможно 4", 127, "срок"),
        ("**Коля** — через месяц, в худшем случае 3", 96, None),
        ("**Коля** — через месяц, в худшем случае 3", 97, "срок"),
        ("**Коля** — через неделю, максимум 20 дней", 91, "давно"),
        ("**Коля** — в течение месяца до 15 числа", 456, None), ("**Коля** — в течение месяца до 15 числа", 457, "давно"),
        ("**Коля** — через неделю до 10 сентября", 38, "срок"),
        ("**Коля** — через неделю 2 раза напомнить", 20, None), ("**Коля** — через неделю 2 раза напомнить", 21, "срок"),
        ("**Коля** — перенести 5 серверов за месяц", 91, "давно"),
        # круг 9: вводное в запятых, скобка, второй хвост, связка-маркер с датой, «мес.», формы единиц,
        # голая запятая без единицы — не граница, но число — кандидат
        ("**Коля** — через месяц, возможно, 3", 96, None), ("**Коля** — через месяц, возможно, 3", 97, "срок"),
        ("**Коля** — через месяц (максимум 3)", 96, None), ("**Коля** — через месяц (максимум 3)", 97, "срок"),
        ("**Коля** — через месяц, возможно, пять", 156, None), ("**Коля** — через месяц, возможно, пять", 157, "давно"),
        ("**Коля** — через месяц-два, в худшем случае четыре", 126, None),
        ("**Коля** — через месяц-два, в худшем случае четыре", 127, "давно"),
        ("**Коля** — через неделю, не позже 10 сентября", 30, "срок"),
        ("**Коля** — через неделю, максимум 4 мес.", 126, None), ("**Коля** — через неделю, максимум 4 мес.", 127, "давно"),
        ("**Коля** — через неделю, максимум 3 годных варианта", 91, "давно"),
        ("**Коля** — через день, максимум 20 недельных отчётов", 91, "давно"),
        # маркер держит число без единицы перед ним; числительное словом — знак (принятая ложная задержка, круг 6)
        ("**Коля** — сдать не позже 20", 8, None), ("**Коля** — перевести два последних источника", 8, None),
        ("**Коля** — сдать отчёт 20-го", 8, None),
        # круг 10: кандидат за окном — долгий знак; «2,5»; «2-х» — количество; проценты — не кандидат.
        # Круг 11: число за единицей не гасится ни маркером, ни суффиксом — «не позднее 20» и «до 15 числа»
        # дают кандидата в единице срока (задержка, принятая цена), «не позже 3» за месяцами — граница
        ("**Коля** — через год, а может и 3", 400, None), ("**Коля** — через год, а может и 3", 401, "давно"),
        ("**Коля** — через месяц, а может и 2,5", 81, None), ("**Коля** — через месяц, а может и 2,5", 82, "срок"),
        ("**Коля** — в течение недели, край 2-х", 20, None), ("**Коля** — в течение недели, край 2-х", 21, "срок"),
        ("**Коля** — через неделю, не позднее 20", 146, None), ("**Коля** — через неделю, не позднее 20", 147, "давно"),
        ("**Коля** — через месяц подготовить отчёт, показатель 5%", 37, "срок"),
        ("**Коля** — через 3 месяца или 4 месяца", 126, None), ("**Коля** — через 3 месяца или 4 месяца", 127, "давно"),
        ("**Коля** — через 2 месяца, не позже 3 подготовить отчёт", 96, None),
        ("**Коля** — через 2 месяца, не позже 3 подготовить отчёт", 97, "давно"),
        ("**Коля** — через месяц, край на 3-й подготовить отчёт", 96, None),
        ("**Коля** — через месяц, край на 3-й подготовить отчёт", 97, "срок"),
        ("**Коля** — через 10 дней, максимум пятнадцать", 90, None), ("**Коля** — через 10 дней, максимум пятнадцать", 91, "давно"),
        ("**Коля** — через месяц обновить Python до 3.11.10", 90, None),
        ("**Коля** — через месяц обновить Python до 3.11.10", 91, "давно"),
        ("**Коля** — в течение месяца (созвоны днём), максимум 3", 96, None),
        ("**Коля** — в течение месяца (созвоны днём), максимум 3", 97, "срок"),
        ("**Коля** — через неделю, 2 письма отправить", 20, None), ("**Коля** — через неделю, 2 письма отправить", 21, "срок"),
        ("**Коля** — через месяц; возможно, 3", 96, None), ("**Коля** — через месяц; возможно, 3", 97, "срок"),
        # круг 12: цифра со слитным суффиксом и у знака препинания — кандидат, как дефисная; своя единица
        # числа — ещё одно прочтение рядом с единицей срока; составные числительные, десятки, творительный
        ("**Коля** — через месяц, край на 3й подготовить отчёт", 96, None),
        ("**Коля** — через месяц, край на 3й подготовить отчёт", 97, "срок"),
        ("**Коля** — в течение недели, край 2х", 20, None), ("**Коля** — в течение недели, край 2х", 21, "срок"),
        ("**Коля** — через неделю, максимум 3мес", 96, None), ("**Коля** — через неделю, максимум 3мес", 97, "срок"),
        ("**Коля** — через месяц, макс.3", 96, None), ("**Коля** — через месяц, макс.3", 97, "срок"),
        ("**Коля** — через месяц; возможно, 4 дня", 126, None), ("**Коля** — через месяц; возможно, 4 дня", 127, "давно"),
        ("**Коля** — через неделю; возможно, 18 месяцев", 546, None),
        ("**Коля** — через неделю; возможно, 18 месяцев", 547, "давно"),
        ("**Коля** — через двадцать пять недель", 181, None), ("**Коля** — через двадцать пять недель", 182, "срок"),
        ("**Коля** — через неделю; возможно, двадцать пять", 181, None),
        ("**Коля** — через неделю; возможно, двадцать пять", 182, "давно"),
        ("**Коля** — через неделю, максимум сорок недель", 286, None),
        ("**Коля** — через неделю, максимум сорок недель", 287, "давно"),
        ("**Коля** — через сорок дней", 46, None), ("**Коля** — через сорок дней", 47, "срок"),
        ("**Коля** — сдать с пятнадцатью днями отсрочки", 90, None),
        ("**Коля** — сдать с пятнадцатью днями отсрочки", 91, "давно"),
        # счёт «число × единица» дальше потолка — долгий знак и там, где единицы нет в словаре долгих
        # (недели, дни): кандидат своей единицы держит только до своей даты (мутация r7, 24.09)
        ("**Коля** — подготовить за 20 недель", 400, None), ("**Коля** — подготовить за 20 недель", 401, "давно"),
        ("**Коля** — сделать за 100 дней", 400, None), ("**Коля** — сделать за 100 дней", 401, "давно"),
    ]
    return [f"правило: «{t}», {n} дн.: ждали {want}, получили {got}"
            for t, n, want in cases if (got := decide(t, d(n), today, "Иван")) != want]


def _selftest_monotone() -> list[str]:
    """Сторож монотонности (Opus, круги 5–7): ошибка разбора только задерживает снятие.

    Первое снятие — пара (день, причина) по шкале возраста до LONG_CAP_DAYS с запасом. Исходная фраза
    снимается не раньше названного срока плюс неделя. Её вариант (другой маркер, вставленное
    определение, слово до или после) — не раньше исходной, без исключений: исключение «давно не
    раньше потолка» узаконивало снятие раньше дальнего срока (Opus, круг 7). Вариант без исходной
    фразы, который гасит таблица `_NOT_TIME`, — другой смысл («на среде» — место), он не сравнивается:
    сузить знак можно только явной строкой таблицы. Вариант, где исходная фраза стоит целиком («на
    встрече к среде»), сравнивается всегда. Диапазон («через неделю или две») держится до верхней
    границы: опознанный — «давно», неопознанный — «срок» по кандидату (круг 9). Фраза без срока — с числом за маркером, фантомным днём
    недели, ссылкой на дату до встречи — снимается не раньше правила возраста."""
    anchor, horizon = dt.date(2026, 9, 7), LONG_CAP_DAYS + 10     # понедельник

    def first_close(phrase: str) -> tuple[int, str | None]:
        text = f"**Коля** — {phrase} подготовить отчёт"
        for age in range(horizon + 1):
            why = decide(text, anchor, anchor + dt.timedelta(days=age), "Иван")
            if why:
                return age, why
        return horizon + 1, None

    bases = {
        "к среде": dt.date(2026, 9, 9), "до среды": dt.date(2026, 9, 9), "в среду": dt.date(2026, 9, 9),
        "до пятницы": dt.date(2026, 9, 11), "до 30 сентября": dt.date(2026, 9, 30),
        "до 15.10": dt.date(2026, 10, 15), "до конца месяца": dt.date(2026, 9, 30),
        "в октябре": dt.date(2026, 10, 31), "до конца года": dt.date(2026, 12, 31),
        "в IV квартале": dt.date(2026, 12, 31), "через неделю": dt.date(2026, 9, 14),
        "через две недели": dt.date(2026, 9, 21), "через 13 месяцев": anchor + dt.timedelta(days=390),
        "до 31.12.2027": dt.date(2027, 12, 31), "к 2027-10-01": dt.date(2027, 10, 1),
        "через 3 месяца перенести 5 сервисов": anchor + dt.timedelta(days=90),
        "через квартал": anchor + dt.timedelta(days=91), "через полгода": anchor + dt.timedelta(days=182),
        "через двадцать пять недель": anchor + dt.timedelta(days=175), "через сорок дней": anchor + dt.timedelta(days=40),
    }
    markers = ("до", "к", "ко", "по", "на", "в", "во", "с", "срок:", "срок —", "дедлайн", "не позднее")
    fillers = ("следующей", "ближайшего", "этой", "самого")
    around = (("примерно ", ""), ("на встрече ", ""), ("в той же задаче ", ""), ("", " включительно"),
              ("", " или раньше"), ("", " — прислать"))
    ranges = {                                        # верхняя граница названа, разбору не даётся
        "через неделю или две": dt.date(2026, 9, 21), "через неделю или 2": dt.date(2026, 9, 21),
        "через месяц — максимум два": anchor + dt.timedelta(days=60),
        "в течение недели, максимум двух": dt.date(2026, 9, 21), "через неделю-другую": dt.date(2026, 9, 21),
        "до конца месяца или двух": dt.date(2026, 10, 31), "через 3 месяца или 4": anchor + dt.timedelta(days=120),
        "через неделю — другую": dt.date(2026, 9, 21), "через неделю, но не позже 20-го": dt.date(2026, 9, 20),
        "через неделю, но не позже 20": dt.date(2026, 9, 20), "через неделю, крайний день 20-го": dt.date(2026, 9, 20),
        "через месяц — максимум три": anchor + dt.timedelta(days=90),
        "через месяц, возможно пять": anchor + dt.timedelta(days=150),
        "через 2 месяца, возможно 4": anchor + dt.timedelta(days=120),
        "через месяц, в худшем случае 3": anchor + dt.timedelta(days=90),
        "через месяц, возможно, 3": anchor + dt.timedelta(days=90), "через месяц (максимум 3)": anchor + dt.timedelta(days=90),
        "через месяц; возможно, 3": anchor + dt.timedelta(days=90), "через месяц: максимум 3": anchor + dt.timedelta(days=90),
        "через месяц возможно 3": anchor + dt.timedelta(days=90),
        "через месяц-два, в худшем случае четыре": anchor + dt.timedelta(days=120),
        "через месяц, а может и 3": anchor + dt.timedelta(days=90), "через месяц, а может и 2,5": anchor + dt.timedelta(days=75),
        "через год, а может и 3": anchor + dt.timedelta(days=1095),
        "через 2 месяца, не позже 3": anchor + dt.timedelta(days=90), "через полгода, не позже 9": anchor + dt.timedelta(days=270),
        "через месяц, край на 3-й": anchor + dt.timedelta(days=90),
        "через месяц, край на 3й": anchor + dt.timedelta(days=90), "в течение недели, край 2х": dt.date(2026, 9, 21),
        "через неделю, максимум 3мес": anchor + dt.timedelta(days=90), "через месяц, макс.3": anchor + dt.timedelta(days=90),
        "через неделю, максимум двадцать пять": anchor + dt.timedelta(days=175),
        "через неделю, максимум сорок недель": anchor + dt.timedelta(days=280),
    }
    # одна граница в разных записях — один день снятия: дефис, слитно, пробел, цифрой и словом (Opus M2, круг 12)
    forms = (("через месяц, край на 3-й", "через месяц, край на 3й"),
             ("в течение недели, край 2-х", "в течение недели, край 2х"),
             ("через неделю, максимум 3 месяца", "через неделю, максимум 3мес", "через неделю, максимум 3 мес."),
             ("через месяц, максимум 3", "через месяц, макс.3", "через месяц,3", "через месяц, максимум три"),
             ("через 25 недель", "через двадцать пять недель"), ("через 15 дней", "через пятнадцать дней"))
    no_due = ("обновить Python до 3.11.10", "обновить до 3.4.35", "перейти на версию 3.4.35", "к 2019-05-01",
              "собрать до 5 человек", "сделать в 2 этапа", "увеличить в 1.5 раза", "по 3 пункта на каждого",
              "до 31.12.2099", "через 1000 дней", "доработать по замечаниям от 04.09",
              "задеплоить в среду тестирования", "ответить на письмо от 01.09")
    bad = []
    early = lambda got, floor: got[0] < floor
    for phrase, due in {**bases, **ranges}.items():
        floor = min((due - anchor).days + OVERDUE_DAYS, horizon + 1)
        if (due - anchor).days > HORIZON_DAYS:
            floor = LONG_CAP_DAYS + 1                 # дальше окна — долгий потолок, принятая цена (решение 23.09)
        base = first_close(phrase)
        if early(base, floor):                        # диапазон — не раньше верхней границы, причина любая
            bad.append(f"монотонность: «{phrase}» снимается на {base[0]}-й день ({base[1]}), срок позволяет с {floor}-го")
        head, _, rest = phrase.partition(" ")
        variants = ({f"{m} {rest}" for m in markers} | {f"{head} {f} {rest}" for f in fillers}
                    | {f"{pre}{phrase}{post}" for pre, post in around}
                    | {phrase.replace("15.10", "15.10.2026"), phrase.replace("IV", "4-м")})
        for variant in sorted(variants - {phrase}):
            if phrase not in variant and _NOT_TIME.search(variant.lower()):
                continue
            got = first_close(variant)
            if early(got, base[0]):
                bad.append(f"монотонность: «{variant}» снимается на {got[0]}-й день ({got[1]}), "
                           f"исходная «{phrase}» — на {base[0]}-й")
    for group in forms:
        days = {phrase: first_close(phrase)[0] for phrase in group}
        if len(set(days.values())) > 1:
            bad.append(f"монотонность: одна граница в разных записях снимается в разные дни: {days}")
    for phrase in no_due:
        got = first_close(phrase)
        if got[0] <= OTHERS_DAYS:
            bad.append(f"монотонность: «{phrase}» без срока снимается на {got[0]}-й день ({got[1]}), раньше возраста")
    return bad


def _selftest_restore_race() -> list[str]:
    """Откат в гонке (Opus, круг 5, I1): реестр — по тексту, который правда записан.

    Строки сдвинуты, снятое «Б» стоит дважды — откат его не узнаёт. Первая попытка записи
    проигрывает, между попытками человек удаляет один дубль, и вторая попытка возвращает «Б». Сразу
    после записи файла процесс обрывается: реестр к этому моменту уже на диске, и следующий скан
    возвращённое не снимает."""
    import contextlib
    import io
    import runpy
    bad = []
    today = dt.date.today()
    rel = f"Встречи-архив/{today - dt.timedelta(days=20):%Y-%m-%d} 10-00 — Гонка/{MINUTES}"
    with tempfile.TemporaryDirectory() as tmp:
        graph, data = pathlib.Path(tmp) / "граф", pathlib.Path(tmp) / "data"
        path = graph / rel
        path.parent.mkdir(parents=True)
        path.write_text("- [ ] **Коля** — А\n- [ ] **Коля** — Б\n- [ ] **Коля** — Б\n", encoding="utf-8")
        applied, _, backup = apply(graph, scan(graph, "Иван", today)[0], data)
        path.write_text("# Сверху\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        real_write, argv, calls = safe_write.write_text, sys.argv, []

        def racy(p, text, **kw):
            calls.append(p)
            if len(calls) == 1:
                lines = p.read_text(encoding="utf-8").split("\n")
                lines.remove(next(line for line in lines if line.startswith("- [-] **Коля** — Б")))
                p.write_text("\n".join(lines), encoding="utf-8")
                return False
            if not real_write(p, text, **kw):
                return False
            raise RuntimeError("обрыв сразу после записи файла")

        safe_write.write_text, sys.argv = racy, [str(backup / "restore.py"), "--apply"]
        crashed = False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(backup / "restore.py"), run_name="__main__")
        except RuntimeError:
            crashed = True
        finally:
            safe_write.write_text, sys.argv = real_write, argv
        body = path.read_text(encoding="utf-8")
        if len(applied) != 3 or not crashed or body.count("- [ ] **Коля** —") != 2:
            bad.append(f"гонка отката: снято {len(applied)}, обрыв {crashed}, текст {body!r}")
        again = scan(graph, "Иван", today, load_returned(backup.parent))[0]
        if again:
            bad.append(f"гонка отката: возвращённое вне реестра, снимется снова: {sum(len(v) for v in again.values())}")
        # оборванный хвост реестра не съедает следующую запись: «А» снова снята, откат пишет её после хвоста
        registry = backup.parent / "returned.jsonl"
        if not registry.exists():
            return bad + ["гонка отката: реестр не создан"]
        registry.write_bytes(registry.read_bytes() + '{"file": "об'.encode("utf-8") + b"\xd0")  # обрыв посреди буквы
        try:
            load_returned(backup.parent)
        except ValueError as e:
            bad.append(f"реестр с хвостом, оборванным посреди буквы, роняет контроль: {e!r}")
        rows = [json.loads(line) for line in (backup / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        closed_a = next(r["new"] for r in rows if r["old"] == "- [ ] **Коля** — А")
        path.write_text(path.read_text(encoding="utf-8").replace("- [ ] **Коля** — А", closed_a), encoding="utf-8")
        subprocess.run([sys.executable, str(backup / "restore.py"), "--apply"], capture_output=True, check=False)
        try:
            last = json.loads(registry.read_bytes().rstrip(b"\n").rsplit(b"\n", 1)[-1].decode("utf-8"))
        except ValueError:
            last = None
        if not isinstance(last, dict) or last.get("old") != "- [ ] **Коля** — А":
            bad.append(f"реестр: запись после оборванного хвоста испорчена: {last!r}")
        # оборванная строка манифеста (прогон убит посреди дозаписи) не роняет откат всего прогона
        with (backup / "manifest.jsonl").open("ab") as f:
            f.write('{"file": "об'.encode("utf-8") + b"\xd0")
        out = subprocess.run([sys.executable, str(backup / "restore.py")], capture_output=True, text=True, check=False)
        # Код 1 — есть не найденные строки (их правили после прогона): откат частичный.
        if out.returncode != 1 or "битых строк манифеста: 1" not in out.stdout:
            bad.append(f"манифест с оборванной строкой: код {out.returncode}, {out.stdout.strip()!r}")
    return bad


def _selftest_graph() -> list[str]:
    """Сводка вкладки, границы записи и полный круг apply → restore → повторный скан."""
    bad = []
    today = dt.date.today()
    old, mid, fresh = (today - dt.timedelta(days=n) for n in (20, 3, 1))
    stamp = lambda d: f"{d:%Y-%m-%d} 10-00"
    files = {
        f"Встречи-архив/{stamp(fresh)} — Тема/{MINUTES}":
            "## Поручения\n- [ ] **Коля** — сделать отчёт (из ревизии)\n- [x] **Оля** — готово\n"
            "- [-] **Коля** — старое _(снято по сроку 01.09)_\n- ~~**Петя** — лишнее~~ _(снято ревизией: не звучало)_\n",
        f"Встречи-архив/{stamp(fresh)} — Тема/Ревизия.md":
            "- [ ] **Коля** — сделать отчёт\n- [ ] **Саша** — не участник встречи\n- [ ] **Коля** — старое\n"
            "- [ ] **Петя** — лишнее\n",
        f"Документация/Стенограммы встреч/{fresh:%Y-%m-%d}_1000_Тема_ревизия.md":
            "- [ ] **Коля** — сделать отчёт\n- [ ] **Саша** — не участник встречи\n",
        f".obsidian/{stamp(fresh)}/скрытое.md": "- [ ] **Коля** — не видно\n",
        "Задачи.md": "- [ ] **Иван** — личное без даты\n",
        f"Дневник/{old:%Y-%m-%d} 2300 заметка.md": "- [ ] купить хлеб\n",
        f"Встречи-архив/{stamp(mid)} — Средняя/{MINUTES}":
            "- [ ] **Коля** — дело\n  - [ ] собрать цифры\n",
        f"Встречи-архив/{stamp(old)} — Старая/{MINUTES}":
            "- [ ] **Коля** — давнее\n- [ ] **Иван** — моё давнее\n- [ ] **Собеседник 3** — ничьё\n"
            "- [ ] **Коля** — с табом\tвнутри\n- [ ] **Коля** — вернули _(снято по сроку 01.09)_\n"
            "```\n- [ ] **Коля** — пример формата\n```\n~~~\n```\n- [ ] **Коля** — пример в тильдах\n~~~\n",
        f"Документация/Стенограммы встреч/{old:%Y-%m-%d}_1000_Старая_minutes.md":
            "- [ ] **Коля** — давнее\n- [ ] **Иван** — моё давнее\n- [ ] **Собеседник 3** — ничьё\n",
    }
    with tempfile.TemporaryDirectory() as tmp:
        graph, data = pathlib.Path(tmp) / "граф", pathlib.Path(tmp) / "data"
        for rel, text in files.items():
            (graph / rel).parent.mkdir(parents=True, exist_ok=True)
            (graph / rel).write_text(text, encoding="utf-8")
        before = {rel: (graph / rel).read_bytes() for rel in files}
        plan, stats, tab = scan(graph, "Иван", today)
        # вкладка (склейка 0.84.1): свежие минутки 1 + из отчёта ревизии только «Саша» (прочие минутки
        # знают: задачей, снятым `[-]`, снятым ревизией `~~`), его копия склеена + Задачи 1 + дневник 1
        # + средняя 2 + старая 7 (два примера в код-блоках — вкладка их видит); копия старых минуток
        # склеена. Мои — 2, Старые — дневник и шесть чужих старых.
        want_tab = {"во вкладке": 13, "Мои": 2, "Старые": 7}
        if tab != want_tab:
            bad.append(f"сводка вкладки: ждали {want_tab}, получили {tab}")
        want = {"срок": 3, "ничьё": 1, "ваших": 1, "возвращено": 1}
        if {k: stats[k] for k in want} != want:
            bad.append(f"счёт снимаемого: ждали {want}, получили {dict(stats)}")
        planned = sorted(line.strip() for edits in plan.values() for _, line, _, _ in edits)
        want_lines = sorted(["- [ ] **Коля** — давнее"] * 2 + ["- [ ] **Иван** — моё давнее"] * 2
                            + ["- [ ] **Собеседник 3** — ничьё"] * 2 + ["- [ ] **Коля** — с табом\tвнутри"])
        if planned != want_lines:
            bad.append(f"план: ждали {want_lines}, получили {planned}")
        # первая попытка записи каждого файла проигрывает гонку: преобразование идёт дважды
        real_write, lost = safe_write.write_text, set()

        def racy_write(path, text, **kw):
            if kw.get("expect") is not None and path not in lost:
                lost.add(path)
                return False
            return real_write(path, text, **kw)

        safe_write.write_text = racy_write
        try:
            applied, skipped, backup = apply(graph, plan, data)
        finally:
            safe_write.write_text = real_write
        # журнал двухфазный: «намерение» до записи файла + «сделано» после (7 + 7 строк)
        rows = [json.loads(line) for line in (backup / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        states = collections.Counter(row.get("state") for row in rows)
        intents = {(r["file"], r["old"], r["new"]) for r in rows if r.get("state") == "intent"}
        dones = {(r["file"], r["old"], r["new"]) for r in rows if r.get("state") == "done"}
        if states != {"intent": 7, "done": 7} or not dones <= intents:
            bad.append(f"манифест после повторной попытки: {len(rows)} строк, состояния {dict(states)}, "
                       f"подтверждено без намерения: {len(dones - intents)}")
        if len(applied) != 7 or skipped or scan(graph, "Иван", today)[0]:
            bad.append(f"запись: строк {len(applied)}, пропущено {skipped}, после записи план не пуст")
        # руками после прогона: одну снятую строку отметили, в другой файл вписали строку сверху
        hand = graph / f"Встречи-архив/{stamp(old)} — Старая/{MINUTES}"
        hand.write_text(hand.read_text(encoding="utf-8").replace(
            "- [-] **Коля** — давнее", "- [x] **Коля** — давнее"), encoding="utf-8")
        copy = graph / f"Документация/Стенограммы встреч/{old:%Y-%m-%d}_1000_Старая_minutes.md"
        copy.write_text("# Сверху\n" + copy.read_text(encoding="utf-8"), encoding="utf-8")
        out = subprocess.run([sys.executable, str(backup / "restore.py"), "--apply"],
                             capture_output=True, text=True, check=False)
        # Код 1 — часть строк правили после прогона: откат частичный, и это не «всё вернул».
        if out.returncode != 1 or "вернул: 6" not in out.stdout or "не найдено (правили после прогона): 1" not in out.stdout:
            bad.append(f"откат: код {out.returncode}, вывод {out.stdout.strip()!r} {out.stderr.strip()[-300:]!r}")
        returned = load_returned(backup.parent)
        again = scan(graph, "Иван", today, returned)[0]
        if again:
            bad.append(f"возвращённое снимается снова: {sum(len(v) for v in again.values())} строк")
        # план, снятый ДО отката (агент ждал замка), под замком сверяется с реестром и ничего не пишет
        stale, _, _ = apply(graph, plan, data)
        if stale:
            bad.append(f"устаревший план после отката записал {len(stale)} строк")
        for rel in files:
            if rel.endswith((f"Старая/{MINUTES}", "Старая_minutes.md")):
                continue
            if (graph / rel).read_bytes() != before[rel]:
                bad.append(f"откат или запись задели чужой файл: {rel}")
    return bad


def selftest(quiet: bool = False) -> int:
    bad = _selftest_rules() + _selftest_monotone() + _selftest_graph() + _selftest_restore_race()
    if not quiet or bad:
        for line in bad:
            print("ПРОВАЛ:", line)
        print("самопроверка:", "чисто" if not bad else f"провалов {len(bad)}")
    return 1 if bad else 0


def run(args, log: pathlib.Path, stamp: str) -> int:
    def record(line: str) -> None:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")

    graph, owner = config(DATA)
    today = dt.date.today()
    if args.apply:
        if live_meeting(DATA):
            record("живая встреча — пропуск")
            print("живая встреча — ничего не пишу")
            return 0
        if selftest(quiet=True):
            raise RuntimeError("самопроверка не прошла — не пишу")
    base = charoite_paths.graph_backups(graph, "tasks_control", root=DATA)
    plan, stats, tab = scan(graph, owner, today, load_returned(base))
    extra = f"; срок не разобран {stats['срок не разобран']}, возвращённых {stats['возвращено']}"
    print(f"граф: {graph}\nсегодня: {today}")
    print(summary_line(stats, tab, applied=False) + extra)
    print(f"строк к правке (минутки, ревизии и их копии): {sum(len(v) for v in plan.values())} в {len(plan)} файлах")
    if not args.apply:
        print("пробный прогон: ничего не записано")
        return 0
    if not plan:
        record(summary_line(stats, tab, applied=True) + extra + "; строк 0")
        print("снимать нечего")
        return 0
    guard_stopped: list[bool] = []

    def guard() -> bool:
        try:
            ok = not live_meeting(DATA)                   # встреча могла начаться, пока ждали замок
        except Exception:                                 # noqa: BLE001 — нечитаемый сигнал = не пишем
            ok = False
        if not ok:
            guard_stopped.append(True)
        return ok

    write_errors: list[str] = []
    applied, skipped, backup = apply(graph, plan, DATA, note=record, guard=guard,
                                     on_error=write_errors.append)
    done: collections.Counter = collections.Counter()
    for why, canonical, mine in applied:
        done[why] += canonical
        done["ваших"] += canonical and mine
    tab_after = scan(graph, owner, today, load_returned(base))[2]
    stale = max(0, sum(len(v) for v in plan.values()) - len(applied) - skipped)   # строки, изменённые между сканом и записью
    short = (summary_line(done, tab_after, applied=True) + (f"; НЕ записано строк: {skipped}" if skipped else "")
             + (f"; устарело строк: {stale}" if stale else ""))
    line = short + extra + f"; строк {len(applied)}; откат: {backup / 'restore.py'}"
    record(line)
    if write_errors:
        fail = f"контроль задач: сбой записи графа ({len(write_errors)}): {write_errors[0]}"
        record(f"ОШИБКА: {fail}")
        if not args.quiet:
            notify(fail)
        print(f"ошибка: {fail}")
        return HANDLED
    if guard_stopped and not applied:
        print(line)
        return 0
    if skipped and not applied:
        fail = f"контроль задач не отработал: НЕ записано строк: {skipped}, снято 0"
        record(f"ОШИБКА: {fail}")
        if not args.quiet:
            notify(fail)
        print(f"ошибка: {fail}")
        return HANDLED                                # run.sh: уже в логе и в уведомлении
    if not args.quiet:
        notify(short)
    print(line)
    return 0


def main() -> int:
    charoite_paths.harden_umask()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон)")
    ap.add_argument("--selftest", action="store_true", help="правила, сводка и откат на временном графе")
    ap.add_argument("--quiet", action="store_true", help="без уведомления macOS")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    global DATA
    DATA = charoite_paths.name_data_root_or_exit(__file__, guess_from_code=True)
    log = charoite_paths.secure_dir(DATA / "logs") / "tasks_control.log"
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        return run(args, log, stamp)
    except Exception as e:  # noqa: BLE001 — агент launchd без этого падает молча
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} ОШИБКА: {type(e).__name__}: {e}\n")
        if not args.quiet:
            notify(f"контроль задач не отработал: {e}")
        print(f"ошибка: {type(e).__name__}: {e}")
        return HANDLED                                # run.sh: уже в логе и в уведомлении


if __name__ == "__main__":
    sys.exit(main())

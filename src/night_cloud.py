"""Ночная облачная ревизия у провайдера с аттестацией (Tinfoil) — одна дверь.

Ядра графа — темы рабочих встреч — уходят за машину. У Anthropic путь свой
(`claude -p`, scripts/nightly_claude_cores.py); здесь — второй провайдер,
выбранный владельцем ключом `sufler.cloud_night_provider: tinfoil`.

Что даёт аттестация и чего не даёт (PRIVACY.md, №792): конструктор SDK
проверяет подпись AMD SEV-SNP и отпечаток кода роутера против подписанного
релиза (Sigstore) и только потом отдаёт клиента — провал любого шага бросает
исключение, текст не уходит. Открытый текст видит роутер внутри энклава;
энклав модели за ним клиент сам не проверяет.

Правила двери:
- первым делом гейт privacy (разрешение) и провайдер; без них ни импорта SDK,
  ни ключа, ни сети;
- сторонние пакеты (tinfoil, openai, httpx) импортируются только внутри функций
  после гейта: модуль тянут тесты и CI без сети и без SDK;
- повторов нет (`max_retries=0`): повтор после отправки — вторая копия текста;
- весь обмен с провайдером (аттестация, документ, поток) — одна функция
  `_exchange` в одном потоке под одним сроком `Deadline`; истёкший срок — не
  провал аттестации, а «ночь кончилась» (свой класс);
- переход к отправке — одно место под замком вместе с проверкой срока:
  сторож срока знает, ушёл ли запрос (`sent` у исключения — для строки шага);
- класс ошибки обмена — два, правило одно — докстрока `_classify` (решения
  главной по out4–out6): «аттестация» или «недоступен»;
- в файле состояния свободного текста нет: перечисление исхода, HTTP-код,
  finish_reason, отпечаток и релиз — форма проверяется одним писателем
  (`conform_entry`) и при записи, и при чтении брифом; бриф пишется в граф;
- текст причины — только в исключении (строка шага, лог ночи), собранный в
  одной точке `_cause`: без ключа и без путей машины;
- исход каждой попытки — в файл состояния по графу; утренний бриф берёт
  сигнал аттестации только оттуда, не из текста модели.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
import re
import ssl
import sys
import threading
import time
from typing import Callable

import cloud
import file_locks
import live_gate
import privacy
import threads

#: Потолок ответа. Замер 25.10: 400000 шлюз принимает; рассуждение на max
#: съедает потолок, поэтому с запасом, а обрыв по длине ловит finish_reason.
MAX_TOKENS = 128000
#: Предел ожидания между байтами потока: молчащий поток держит шаг не дольше.
READ_TIMEOUT_S = 60.0
#: Заголовок, который в ответе модели означает подделку сигнала аттестации.
ATTESTATION_HEADER = "## Аттестация"
#: Тот же заголовок любого уровня и регистра: «###  аттестация» — та же подделка.
#: Заголовок ATX («## Аттестация») и Setext («Аттестация» над строкой из = или -):
#: Obsidian показывает оба (opus M1).
_ATTESTATION_HEADING = re.compile(r"^\s*#+\s*аттестация\b|^\s*аттестация\b.*\n\s*(?:=+|-+)\s*$",
                                  re.IGNORECASE | re.MULTILINE)
#: Шаги документа аттестации SDK 0.15.0. Нет шага — не «всё прошло», а
#: «проверено не всё»: SDK, переименовавший шаг, даёт громкий отказ (out1 S-I1).
REQUIRED_STEPS = ("fetch_digest", "verify_code", "verify_enclave", "compare_measurements")
#: Признак сбоя проверки сертификата в тексте SSLError — тот же, что у SDK 0.15.0
#: (`tinfoil.client._CERTIFICATE_VERIFY_ERROR_MARKERS`).
_CERTIFICATE_VERIFY_MARKERS = ("certificate_verify_failed", "certificate verify failed")


class NightCloudError(Exception):
    """Исход двери, отличный от принятого ответа. Причина — без ключа.

    `sent` — начата ли отправка запроса до исхода; ставит `send` (out2 C1, C2).
    Читает только строка шага; в файл состояния и бриф не идёт (решение по out4).
    """
    sent: bool = False


class AttestationFailed(NightCloudError):
    """Документ аттестации не прошёл проверку двери (`_verified`), проверку
    самого SDK (`_attestation_error`, `_report_error`) или сбой сертификата по
    правилу SDK (`_certificate_error`).

    Документ проверяется до отправки — тогда ни байта не ушло. Сбой
    сертификата SDK по своей докстроке даёт при установке соединения; ветка
    потока классифицирует по тому же правилу, и `sent` у исключения говорит,
    начинался ли переход к отправке.
    """


class ProviderUnavailable(NightCloudError):
    """Всё, что не аттестация: нет пакета, ключа, сети; отказ конструктора SDK
    без сбоя сертификата; HTTP-отказ или обрыв потока."""


class ReplyRejected(NightCloudError):
    """Ответ пришёл, но это не ревизия: обрыв по длине, пусто, подделка раздела."""


class DeadlineExpired(NightCloudError):
    """Срок шага вышел: ночь кончилась, это не сбой провайдера."""


@dataclasses.dataclass(frozen=True)
class Deadline:
    """Монотонный срок шага. Сон машины его не тратит; стенной потолок ночи
    (`CHAROITE_NIGHTLY_UNTIL`) дверь проверяет отдельно перед отправкой (opus M3)."""
    at: float
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def after(cls, seconds: float, clock: Callable[[], float] = time.monotonic) -> "Deadline":
        return cls(clock() + max(0.0, seconds), clock)

    def remaining(self) -> float:
        return max(0.0, self.at - self.clock())


@dataclasses.dataclass(frozen=True)
class Reply:
    text: str
    provider: str
    model: str
    attestation: str
    fingerprint: str = ""
    release: str = ""


def _scrub(text: str, secret: str) -> str:
    """Текст ошибки без ключа: SDK и openai вправе процитировать заголовок."""
    text = str(text)
    return text.replace(secret, "<ключ>") if secret else text


#: Потолок строки причины в логе ночи: тело ответа шлюза может цитировать запрос (out6 S-M2).
_CAUSE_LIMIT = 2000


def _cause(text, secret: str = "") -> str:
    """Текст причины для строки шага — одна точка сборки (решения главной по out4, out5).

    Сначала ключ и пути машины (`privacy.scrub_local_paths`), потом потолок
    длины: обрезка до чистки оставляла обрывок пути (out5 C), после чистки
    пути в тексте уже нет. Чистка не решает исход: упала — причина скрыта
    целиком, а не отдана сырой.
    """
    try:
        text = privacy.scrub_local_paths(_scrub(text, secret))[0]
    except Exception as e:  # noqa: BLE001 — уборка не подменяет исход (out5 DS-M3)
        return f"<причина скрыта: пути не вычищены, {type(e).__name__}>"
    if len(text) > _CAUSE_LIMIT:
        text = f"{text[:_CAUSE_LIMIT]}… (обрезано {len(text) - _CAUSE_LIMIT} зн.)"
    return text


def _reason(e: BaseException, secret: str) -> str:
    code = getattr(e, "status_code", None)
    head = f"{type(e).__name__}" + (f" HTTP {code}" if code else "")
    return f"{head}: {_scrub(e, secret)}"


def _http_status(e: BaseException) -> int | None:
    """HTTP-код из исключения и его явных причин (`__cause__`; openai
    `APIStatusError.status_code`) — для состояния. Неявный контекст не берётся:
    ошибка, брошенная в обработчике чужого 503, своего кода не имеет (out6 DS-M3)."""
    seen: list[BaseException] = []
    while e is not None and e not in seen and len(seen) < 16:
        seen.append(e)
        code = getattr(e, "status_code", None)
        if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
            return code
        e = e.__cause__
    return None


#: Имя рабочего потока обмена (литерал в spawn — его читает сторож detached-потоков);
#: тесты ждут выхода рабочего по этому имени и сверяют его с живым потоком.
EXCHANGE_THREAD = "night-cloud-exchange"


def _under_deadline(fn: Callable, args: tuple, deadline: Deadline, box: dict):
    """Блокирующий обмен под сроком. Один помощник на дверь (in3 K1, out1 S-I2).

    Поток — daemon: брошенный по сроку не держит выход процесса. Результат
    брошенного потока не читается никогда, исключение потока — в контейнер,
    наружу его поднимает вызывающий поток. `box` общий с `fn`: по сроку здесь
    под `box["lock"]` ставится `cancel` и читается `sent` — после этого рабочий
    поток отправку не начнёт, а сторож знает, начал ли (out2 C1); открытый
    поток ответа закрывается — молчащий сервер не держит шаг дольше срока.
    """
    def run():
        try:
            box["value"] = fn(*args, box)
        except BaseException as e:  # noqa: BLE001 — переносим в свой поток как есть
            box["error"] = e

    worker = threads.spawn(run, name="night-cloud-exchange", role="process",
                           detached="брошенный по сроку обмен с провайдером: результат не читается, "
                                    "ночной шаг выходит, не дожидаясь сети")
    worker.join(deadline.remaining())
    if worker.is_alive():
        with box["lock"]:
            box["cancel"] = True
            sent, stream = box.get("sent", False), box.get("stream")
        _close_quietly(stream)
        where = ("после отправки запроса" if sent else
                 "до отправки, после аттестации" if "attested" in box else "во время аттестации")
        raise DeadlineExpired(f"срок шага вышел {where}")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _close_quietly(stream) -> None:
    """Уборка не решает исход (out1 DS-M2): сбой close() не подменяет ни ответ, ни срок."""
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001, S110 — закрытие best-effort
            pass


def _causes(e: BaseException) -> list[BaseException]:
    """Цепочка причин так же, как её обходит SDK (`tinfoil.client._is_certificate_error`,
    0.15.0): `__cause__ or __context__` — правило SDK целиком (решение главной по out5)."""
    out: list[BaseException] = []
    while e is not None and e not in out and len(out) < 16:
        out.append(e)
        e = e.__cause__ or e.__context__
    return out


def _pin_error(x: BaseException) -> bool:
    """Закреплённый ключ энклава не тот (`_PinMismatchError` SDK) — по имени и
    модулю: верхний уровень двери SDK не импортирует (in3 K6)."""
    return type(x).__name__ == "_PinMismatchError" and type(x).__module__.split(".")[0] == "tinfoil"


def _certificate_error(x: BaseException) -> bool:
    """Сбой проверки сертификата — правило самого SDK (`tinfoil.client._is_certificate_error`,
    0.15.0): несовпадение закреплённого ключа (`_PinMismatchError`),
    SSLCertVerificationError, SSLError с «certificate verify failed». SDK зовёт
    его на закреплённом соединении с энклавом — там же его зовёт и дверь (`_classify`)."""
    if _pin_error(x):
        return True
    if isinstance(x, ssl.SSLCertVerificationError):
        return True
    if isinstance(x, ssl.SSLError):
        return any(m in str(x).lower() for m in _CERTIFICATE_VERIFY_MARKERS)
    return False


def _attestation_error(x: BaseException) -> bool:
    """Отказ проверки самого SDK: класс `AttestationError` из `tinfoil.attestation.*`
    или его наследник (`MeasurementMismatchError` — релиз не тот, режим Sigstore;
    опыт wrong_repo 26.10). По имени и модулю в MRO — SDK не импортируется (in3 K6)."""
    return any(k.__name__ == "AttestationError" and k.__module__.startswith("tinfoil.attestation")
               for k in type(x).__mro__)


#: Провал проверки отчёта железа в SDK 0.15.0: модуль → класс. Они не наследники
#: `AttestationError`; SDK оборачивает их в ValueError (`attestation_sev.py:88`,
#: `attestation_tdx.py:268`).
_REPORT_ERRORS = {"tinfoil.attestation.attestation_sev": "SevAttestationError",
                  "tinfoil.attestation.attestation_tdx": "TdxAttestationError"}


#: HTTP-ответы службы ключей, которые значат «сервис занят», а не «такого отчёта нет».
_RETRYABLE_HTTP = (408, 429)


def _transport_error(x: BaseException) -> bool:
    """Ответа нет или сервис занят: ввод-вывод (OSError — сокет, таймаут,
    requests.*, URLError, чтение кэша), кроме HTTP-ответа 4xx. Ответ 4xx службы
    ключей (KDS, Intel PCS) — по данным отчёта, а их задаёт проверяемая сторона:
    выдуманный chip_id даёт 404 (out7 S-I1), и это провал отчёта, а не сеть."""
    if not isinstance(x, OSError):
        return False
    code = getattr(getattr(x, "response", None), "status_code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        return code >= 500 or code in _RETRYABLE_HTTP
    return True


def _report_error(x: BaseException) -> bool:
    """Отчёт железа SEV-SNP/TDX не прошёл проверку SDK — по имени и модулю в MRO."""
    return any(_REPORT_ERRORS.get(k.__module__) == k.__name__ for k in type(x).__mro__)


def _classify(e: BaseException, *, pinned: bool) -> type[NightCloudError]:
    """Два класса по правилам SDK (решение главной по out4, п. 2 с расширениями
    по опытам out5, out6, opus). Цепочка — `__cause__ or __context__`, как у SDK
    (`_causes`):
    - `AttestationError` SDK или несовпадение закреплённого ключа — аттестация;
    - сбой сертификата по правилу SDK — аттестация только на закреплённом
      соединении (`pinned`: `create` и поток), где правило зовёт сам SDK. В
      конструкторе запросы идут по публичному TLS (адреса роутера, документ,
      KDS, Sigstore), и сбой сертификата там — сеть: портал Wi-Fi, прокси с
      подменой TLS, уехавшие часы (opus I1, опыт с пустым набором CA 26.10);
    - провал отчёта железа SEV/TDX — аттестация, если в цепочке нет сбоя
      транспорта (`_transport_error`): сеть к KDS или коллатералу даёт тот же
      класс SDK, а это недоступность (опыт e_sev_chain 26.10). Ответ, который
      пришёл, но не читается (не DER, не JSON), — аттестация: строго;
    - всё прочее — недоступен. Голый ValueError закреплённого measurement=
      (режим pin) — недоступен: pin вне этапа 1. Отказ документа решает `_verified`."""
    chain = _causes(e)
    certificate = _certificate_error if pinned else _pin_error
    if any(certificate(x) or _attestation_error(x) for x in chain):
        return AttestationFailed
    if any(_report_error(x) for x in chain) and not any(_transport_error(x) for x in chain):
        return AttestationFailed
    return ProviderUnavailable


def _attest(cfg: dict, key: str):
    """Клиент Tinfoil: конструктор и есть аттестация (сеть, подпись, Sigstore).

    Исключения конструктора уходят наверх как есть: класс исхода решает
    `_classify` в `_exchange`, одна таблица на фабрику и на тесты.
    """
    if not privacy.cloud_enrich_enabled(cfg):
        raise privacy.PrivacyRefused("облако выключено: sufler.cloud_enrich")
    try:
        from tinfoil import TinfoilAI
    except ImportError as e:
        raise ProviderUnavailable(
            f"пакет tinfoil не установлен ({type(e).__name__}): pip install 'charoite[tinfoil]'") from None
    return TinfoilAI(api_key=key)


def _verified(client) -> tuple[str, str, str]:
    """(строка аттестации, отпечаток, релиз) — или AttestationFailed.

    Любой сбой чтения документа — тоже AttestationFailed: непроверенный
    документ не «сеть, повторите завтра» (out1 DS-I1).
    """
    try:
        doc = client.get_verification_document()
        if doc is None or getattr(doc, "security_verified", False) is not True:
            raise AttestationFailed("документ аттестации без security_verified")
        steps = getattr(doc, "steps", None)
        if not isinstance(steps, dict):
            raise AttestationFailed(f"документ аттестации: шаги не словарь ({type(steps).__name__})")
        missing = [name for name in REQUIRED_STEPS if name not in steps]
        if missing:
            raise AttestationFailed("документ аттестации без шагов: " + ", ".join(missing))
        bad = [name for name, st in steps.items() if getattr(st, "status", "") != "success"]
        if bad:
            raise AttestationFailed("шаги аттестации не success: " + ", ".join(sorted(map(str, bad))))
        fingerprint = str(getattr(doc, "code_fingerprint", "") or "").strip().lower()
        if not fingerprint:
            raise AttestationFailed("документ аттестации без отпечатка кода")
        if _HEX.fullmatch(fingerprint) is None:
            # отпечаток, который форма состояния не сохранит, гасил бы сигнал смены (out6 DS-I1)
            raise AttestationFailed("отпечаток кода не той формы (ждём hex)")
        release = str(getattr(doc, "release_tag", "") or "")
        host = str(getattr(doc, "enclave_host", "") or "").strip().lower()
        if host and _HOST.fullmatch(host) is None:
            # имя роутера уходит в отчёт графа: текст извне не печатаем (opus M2)
            print("night_cloud: имя роутера не той формы — в отчёте «?»", file=sys.stderr, flush=True)
            host = ""
        if release and _RELEASE.fullmatch(release) is None:
            # значение не печатаем: это текст извне; бриф покажет «релиз ?» (out7 H-M2)
            print("night_cloud: тег релиза не той формы — в состоянии и брифе «?»",
                  file=sys.stderr, flush=True)
            release = ""
    except AttestationFailed:
        raise
    except Exception as e:  # noqa: BLE001 — непроверяемый документ трактуем строго
        raise AttestationFailed(f"документ аттестации не читается: {type(e).__name__}") from None
    try:
        from importlib.metadata import version
        sdk = version("tinfoil")
    except Exception:  # noqa: BLE001 — версия для строки отчёта, не для решения
        sdk = "?"
    line = (f"роутер {host or '?'}, релиз {release or '?'}, отпечаток {fingerprint[:16] or '?'}, "
            f"SDK tinfoil {sdk}, сверка с подписанным релизом (Sigstore)")
    return line, fingerprint, release


#: Исходы попытки — перечисление; «refused» — отказ политики (out2 H-I1),
#: «delivered» — ответ пришёл, вызывающий его ещё не принял (out1 DS-M6).
OUTCOMES = ("accepted", "attestation", "unavailable", "deadline", "rejected", "refused", "delivered")
#: finish_reason по спецификации chat completions (openai SDK) — белый список.
FINISH_REASONS = ("stop", "length", "content_filter", "tool_calls", "function_call")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HEX = re.compile(r"[0-9a-f]{16,128}")
#: Релиз — semver, суффикс только «-rc N»: тег приходит извне, слов в нём быть не должно (out7 H-I1).
_RELEASE = re.compile(r"v?\d{1,6}\.\d{1,6}\.\d{1,6}(?:-rc\.?\d{1,4})?")
#: Имя хоста роутера (с портом): латиница, цифры, точка и дефис.
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::\d{1,5})?")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")


def _form(rx: re.Pattern):
    return lambda v: isinstance(v, str) and rx.fullmatch(v) is not None


def _empty_or(rx: re.Pattern):
    return lambda v: v == "" or _form(rx)(v)


def _http(v) -> bool:
    return v is None or (isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599)


_RECORD = {"at": _form(_STAMP), "date": _form(_DATE), "fingerprint": _empty_or(_HEX),
           "release": _empty_or(_RELEASE), "model": _empty_or(_MODEL)}
#: Форма записи графа в файле состояния: раздел → поле → проверка значения.
#: Свободного текста нет ни в одном поле (решение главной по out5).
_STATE_FORM = {
    "last_attempt": {**_RECORD, "provider": lambda v: v == "tinfoil",
                     "outcome": lambda v: v in OUTCOMES, "http_status": _http,
                     "finish_reason": lambda v: v is None or v in FINISH_REASONS,
                     "accepted": lambda v: isinstance(v, bool)},
    "last_accepted": _RECORD,
    "previous_accepted": _RECORD,
    "last_attestation_failure": {"at": _form(_STAMP), "date": _form(_DATE), "fingerprint": _empty_or(_HEX),
                                 "release": _empty_or(_RELEASE), "http_status": _http},
    "fingerprint_change": {"at": _form(_STAMP), "from": _empty_or(_HEX), "to": _empty_or(_HEX),
                           "from_release": _empty_or(_RELEASE), "to_release": _empty_or(_RELEASE)},
}


def conform_entry(entry) -> dict:
    """Запись графа в форме `_STATE_FORM`: чужой раздел, чужое поле или значение
    не той формы выпадает. Зовут оба конца: писатель перед записью и бриф при
    чтении — строке вне формы негде попасть в граф (out4 C, out5 C)."""
    out: dict = {}
    if not isinstance(entry, dict):
        return out
    for section, fields in _STATE_FORM.items():
        record = entry.get(section)
        if isinstance(record, dict):
            kept = {k: v for k, v in record.items() if k in fields and fields[k](v)}
            if kept:
                out[section] = kept
    return out


def read_entry(path: pathlib.Path, graph_key: str) -> dict:
    """Запись графа из файла состояния, уже в форме — для брифа."""
    return conform_entry(_read_state(path).get(graph_key))


def _read_state(path: pathlib.Path) -> dict:
    """Файл состояния, каждая запись — в форме: запись прежней версии с текстом
    причины не переживает ни чтения, ни следующей записи (out6 S-M4)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}   # битый файл = первый запуск (in3 DS-I4)
    if not isinstance(data, dict):
        return {}
    kept = {k: conform_entry(v) for k, v in data.items()}
    return {k: v for k, v in kept.items() if v}


def _update_state(path: pathlib.Path, graph_key: str, change: Callable[[dict], None]) -> None:
    """Прочитать — поправить запись графа — заменить атомарно, под замком файла."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path.with_name(path.name + ".lock")
    with open(lock, "a") as lf:
        if not file_locks.acquire_exclusive(lf, attempts=25):
            print(f"night_cloud: {path.name} занят — исход не записан", flush=True)
            return
        data = _read_state(path)
        entry = data.get(graph_key)
        entry = entry if isinstance(entry, dict) else {}
        change(entry)
        data[graph_key] = conform_entry(entry)
        tmp = path.with_name(path.name + ".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def _now() -> str:
    """Метка попытки с зоной: бриф судит по давности, не по календарной дате (out1 S-I5)."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _quietly(what: str, fn: Callable[[], None]) -> None:
    """Сбой записи состояния не подменяет исход ревизии (out1 S-M3, out2 DS-M1).

    Любой, не только OSError: вызов стоит в `finally` и подменил бы уже
    решённый класс исхода необработанным исключением.
    """
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — журнал исхода не решает исход
        print(f"night_cloud: {what} не записан: {type(e).__name__}", flush=True)


def _record(attempt: dict) -> Callable[[dict], None]:
    """Запись исхода попытки. Провал аттестации пишется ещё и отдельным
    разделом: следующая попытка затирает `last_attempt`, а сигнал о подмене
    должен дожить до брифа, как смена отпечатка (opus I2)."""
    def change(entry: dict) -> None:
        entry["last_attempt"] = attempt
        if attempt.get("outcome") == "attestation":
            entry["last_attestation_failure"] = {
                k: attempt.get(k) for k in ("at", "date", "fingerprint", "release", "http_status")}
    return change


def accept(state_path: pathlib.Path, graph_key: str, reply: Reply) -> None:
    """Ответ прошёл проверку содержания у вызывающего — запомнить отпечаток.

    Отдельно от send (in3 DS-I1): принятым ответ становится не на приходе, а
    после проверки секций. Смена отпечатка против прошлого принятого пишется
    отдельной записью с меткой времени — бриф показывает её, пока она свежая,
    а следующий приём того же отпечатка её не стирает (out1 H-M2).
    """
    now = _now()

    def change(entry: dict) -> None:
        prev = entry.get("last_accepted")
        if isinstance(prev, dict) and prev.get("fingerprint") \
                and prev.get("fingerprint") != reply.fingerprint:
            entry["previous_accepted"] = prev
            entry["fingerprint_change"] = {
                "at": now, "from_release": prev.get("release", ""), "to_release": reply.release,
                "from": str(prev.get("fingerprint"))[:16], "to": reply.fingerprint[:16]}
        entry["last_accepted"] = {"at": now, "date": now[:10], "fingerprint": reply.fingerprint,
                                  "release": reply.release, "model": reply.model}
        attempt = entry.get("last_attempt")
        if isinstance(attempt, dict):
            attempt.update(outcome="accepted", accepted=True)

    _quietly("приём ответа", lambda: _update_state(state_path, graph_key, change))


def reject(state_path: pathlib.Path, graph_key: str) -> None:
    """Ответ пришёл, но вызывающий его не принял (нет секций). Причину печатает
    вызывающий в строку шага: в состоянии текста нет (решение главной по out5)."""
    def change(entry: dict) -> None:
        attempt = entry.get("last_attempt")
        if isinstance(attempt, dict):
            attempt.update(outcome="rejected", accepted=False)

    _quietly("отказ в ответе", lambda: _update_state(state_path, graph_key, change))


def _exchange(cfg: dict, key: str, prompt: str, model: str, effort: str,
              deadline: Deadline, box: dict) -> tuple[str, str | None, str]:
    """Весь обмен с провайдером — в одном потоке под сроком `_under_deadline`.

    Гейт — первым оператором: функция и есть выход SDK в реестре сторожа.
    Возвращает (текст, finish_reason, строка аттестации); отпечаток и релиз —
    в `box["attested"]` сразу после проверки документа, чтобы исход попытки
    знал их и при сбое потока. `box["sent"]` ставится под замком в одном месте —
    перед `create`, вместе с последней проверкой срока и отмены.
    """
    if not privacy.cloud_enrich_enabled(cfg):
        raise privacy.PrivacyRefused("облако выключено: sufler.cloud_enrich")
    try:
        client = _attest(cfg, key)   # фабрика не параметр: подменить её нечем, кроме модуля (out3 S-M3)
    except (NightCloudError, privacy.PrivacyRefused):
        raise
    except Exception as e:  # noqa: BLE001 — класс решает таблица
        box["http_status"] = _http_status(e)
        cls = _classify(e, pinned=False)
        # слово от класса, не от места броска: «аттестация» — только у аттестации (out5 DS-M2)
        raise cls(("аттестация: " if cls is AttestationFailed else "клиент провайдера: ")
                  + _reason(e, key)) from None
    line, fingerprint, release = _verified(client)
    box["attested"] = (fingerprint, release)
    chunks: list[str] = []
    finish = None
    try:
        # max_retries=0: повтор после отправки — вторая копия текста; таймаут —
        # между байтами, общий срок держит _under_deadline снаружи
        api = client.client.with_options(
            timeout=max(0.001, min(deadline.remaining(), READ_TIMEOUT_S)), max_retries=0)
        with box["lock"]:
            # единственный переход к отправке: сторож, поставивший cancel под тем
            # же замком, либо видит sent, либо знает, что create не начнётся (out2 C1)
            # и стенной потолок ночи: монотонные часы во сне стоят, ночь — нет (opus M3)
            if box.get("cancel") or deadline.remaining() <= 0 or live_gate.night_is_over():
                raise DeadlineExpired("срок шага вышел до отправки")
            box["sent"] = True
        stream = api.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], stream=True,
            max_tokens=MAX_TOKENS, reasoning_effort=effort)
        try:
            with box["lock"]:
                # сторож закрывает поток, который видит; не увидел — закрываем здесь
                box["stream"] = stream
                cancelled = box.get("cancel")
            if cancelled:
                raise DeadlineExpired("срок шага вышел после отправки запроса")
            for chunk in stream:
                if box.get("cancel") or deadline.remaining() <= 0:
                    raise DeadlineExpired("срок шага вышел после отправки запроса")
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                piece = getattr(choices[0].delta, "content", None)
                if piece:
                    chunks.append(piece)
                finish = getattr(choices[0], "finish_reason", None) or finish
        finally:
            _close_quietly(stream)
    except NightCloudError:
        raise
    except Exception as e:  # noqa: BLE001 — openai/httpx без импорта: класс решает таблица
        box["http_status"] = _http_status(e)
        raise _classify(e, pinned=True)("поток: " + _reason(e, key)) from None
    return "".join(chunks).strip(), finish, line


#: Класс исхода → запись в состоянии; сверху вниз, первое совпадение.
_OUTCOMES = ((AttestationFailed, "attestation"), (DeadlineExpired, "deadline"),
             (ReplyRejected, "rejected"), (ProviderUnavailable, "unavailable"))


def send(cfg: dict, prompt: str, *, deadline: Deadline, state_path: pathlib.Path,
         graph_key: str) -> Reply:
    """Отправить промпт ночной ревизии провайдеру Tinfoil и вернуть ответ.

    Фабрика клиента — всегда аттестующий `_attest` (тесты подменяют его в
    модуле): параметра «свой транспорт» у двери нет — в коде продукта нечего
    сторожить (out2 S-M2). Исключения — классы двери и PrivacyRefused; у класса
    двери `sent` — начиналась ли отправка. Исход пишется всегда, и отказ гейта
    тоже (out2 H-I1).
    """
    now = _now()
    attempt = {"at": now, "date": now[:10], "provider": "tinfoil", "model": "",
               "outcome": "unavailable", "http_status": None, "finish_reason": None,
               "fingerprint": "", "release": "", "accepted": False}
    key = ""
    box: dict = {"lock": threading.Lock()}
    try:
        if not privacy.cloud_enrich_enabled(cfg):
            raise privacy.PrivacyRefused("облако выключено: sufler.cloud_enrich")
        if privacy.night_provider(cfg) != "tinfoil":
            raise privacy.PrivacyRefused("ночной провайдер не tinfoil — дверь Tinfoil не отправляет")
        model = cloud.model(cfg, "cloud_night_tinfoil_model")
        attempt["model"] = model
        key_path = cloud.tinfoil_key_path(cfg)
        key = cloud.read_secret(key_path)
        if not key:
            raise ProviderUnavailable(f"нет ключа Tinfoil: {key_path}")
        if deadline.remaining() <= 0:
            raise DeadlineExpired("срок шага вышел до аттестации")
        try:
            text, finish, line = _under_deadline(
                _exchange, (cfg, key, prompt, model, cloud.tinfoil_effort(cfg), deadline),
                deadline, box)
        finally:
            fingerprint, release = box.get("attested", ("", ""))
            attempt.update(fingerprint=fingerprint, release=release)
        attempt["finish_reason"] = finish
        if finish != "stop":
            raise ReplyRejected(f"finish_reason={finish}, {len(text)} зн.")
        if not text:
            raise ReplyRejected("пустой ответ")
        if _ATTESTATION_HEADING.search(text):
            raise ReplyRejected(f"ответ модели содержит «{ATTESTATION_HEADER}» — подделка сигнала")
        # «доставлено» — не «принято»: принимает вызывающий после проверки секций (out1 DS-M6)
        attempt["outcome"] = "delivered"
        return Reply(text=text, provider="tinfoil", model=model, attestation=line,
                     fingerprint=fingerprint, release=release)
    except NightCloudError as e:
        e.sent = bool(box.get("sent"))
        e.args = (_cause(e, key),)   # строка шага — без ключа и путей
        attempt["outcome"] = next((o for cls, o in _OUTCOMES if isinstance(e, cls)), "unavailable")
        raise
    except privacy.PrivacyRefused as e:
        e.args = (_cause(e),)
        attempt["outcome"] = "refused"
        raise
    except Exception as e:  # noqa: BLE001 — непредвиденное: исход записан, наружу классом двери
        attempt["http_status"] = _http_status(e)
        err = ProviderUnavailable(_cause(_reason(e, key)))
        err.sent = bool(box.get("sent"))
        raise err from None
    finally:
        if attempt["http_status"] is None:
            attempt["http_status"] = box.get("http_status")
        # исход пишется всегда (in3 S-I4), но сбой записи не подменяет исход попытки
        _quietly("исход попытки", lambda: _update_state(state_path, graph_key, _record(attempt)))

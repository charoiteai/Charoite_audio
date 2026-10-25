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
  сторож срока знает, ушёл ли запрос, и это пишется в исход (`sent`);
- класс исхода по исключению и стадии обмена решает одна таблица `_classify`;
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
import threading
import time
from typing import Callable

import cloud
import file_locks
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
_ATTESTATION_HEADING = re.compile(r"^\s*#+\s*аттестация\b", re.IGNORECASE | re.MULTILINE)
#: Шаги документа аттестации SDK 0.15.0. Нет шага — не «всё прошло», а
#: «проверено не всё»: SDK, переименовавший шаг, даёт громкий отказ (out1 S-I1).
REQUIRED_STEPS = ("fetch_digest", "verify_code", "verify_enclave", "compare_measurements")
#: Начало текста ValueError у SDK 0.15.0, когда недоступен список роутеров:
#: это сеть, а не подмена (замер 25.10, E2).
_ROUTER_LIST_FAILED = "Failed to fetch router"
#: Признак сбоя проверки сертификата в тексте SSLError — тот же, что у SDK 0.15.0
#: (`tinfoil.client._CERTIFICATE_VERIFY_ERROR_MARKERS`).
_CERTIFICATE_VERIFY_MARKERS = ("certificate_verify_failed", "certificate verify failed")


class NightCloudError(Exception):
    """Исход двери, отличный от принятого ответа. Причина — без ключа.

    `sent` — начата ли отправка запроса до исхода; ставит `send` (out2 C1, C2).
    """
    sent: bool = False


class AttestationFailed(NightCloudError):
    """Подпись, отпечаток или сверка с релизом не сошлись.

    До отправки (конструктор, документ) — ни байта не ушло. На отправке (TLS
    не сошёлся при `create`) — `sent=True`: тело могло уйти после рукопожатия.
    """


class ProviderUnavailable(NightCloudError):
    """Нет пакета, ключа, сети; HTTP-отказ или обрыв потока."""


class ReplyRejected(NightCloudError):
    """Ответ пришёл, но это не ревизия: обрыв по длине, пусто, подделка раздела."""


class DeadlineExpired(NightCloudError):
    """Срок шага вышел: ночь кончилась, это не сбой провайдера."""


@dataclasses.dataclass(frozen=True)
class Deadline:
    """Монотонный срок шага. Сон машины его не тратит (как step_left)."""
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


def _reason(e: BaseException, secret: str) -> str:
    code = getattr(e, "status_code", None)
    head = f"{type(e).__name__}" + (f" HTTP {code}" if code else "")
    return f"{head}: {_scrub(e, secret)[:200]}"


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


def _chain(e: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    while e is not None and e not in out and len(out) < 16:
        out.append(e)
        # `raise … from None` прячет __context__: подавленная причина класс не решает (out2 DS-M2)
        e = e.__cause__ if e.__suppress_context__ else (e.__cause__ or e.__context__)
    return out


def _certificate_error(x: BaseException) -> bool:
    """Сбой проверки сертификата — правило самого SDK (`tinfoil.client._is_certificate_error`,
    0.15.0): несовпадение закреплённого ключа, SSLCertVerificationError, SSLError
    с «certificate verify failed». Такие случаются при установке соединения, до
    байтов запроса; прочие SSLError — ввод-вывод посреди обмена (out3 S-I1, DS-I2).
    Любое исключение модуля tinfoil.* — тоже: происхождение решает раньше стадии
    (out3 H-C1)."""
    if type(x).__module__.split(".")[0] == "tinfoil":
        return True
    name = type(x).__name__
    if isinstance(x, ssl.SSLCertVerificationError) or name == "SSLCertVerificationError":
        return True
    if isinstance(x, ssl.SSLError) or name == "SSLError":
        return any(m in str(x).lower() for m in _CERTIFICATE_VERIFY_MARKERS)
    return False


def _classify(e: BaseException, stage: str) -> type[NightCloudError]:
    """Одна таблица «исключение → класс исхода» (out1 S-I3, H-I2).

    Стадии `_exchange`: attest (конструктор), connect (`create` — рукопожатие и
    отправка), stream (ответ уже идёт). Сверху вниз, первое совпадение по
    цепочке причин:
    - сбой проверки сертификата (`_certificate_error`) — аттестация на любой стадии;
    - attest: любой TLS-сбой — аттестация (до отправки, сигнал громкий);
    - stream — недоступен: запрос ушёл и принят, обрыв посреди ответа —
      транспорт, а не подмена (out2 C2);
    - нет пакета внутри SDK, битый JSON, сеть (OSError, прочие SSLError) — недоступен;
    - attest: ValueError «Failed to fetch router» — сеть, прочее — аттестация;
    - connect: прочее (HTTP-отказ, обрыв) — недоступен.
    """
    chain = _chain(e)
    if any(_certificate_error(x) for x in chain):
        return AttestationFailed
    if stage == "attest" and any(isinstance(x, ssl.SSLError) or type(x).__name__ == "SSLError" for x in chain):
        return AttestationFailed
    if stage == "stream":
        return ProviderUnavailable
    if isinstance(e, (ImportError, json.JSONDecodeError, OSError)):
        return ProviderUnavailable
    if stage == "attest":
        if isinstance(e, ValueError) and str(e).startswith(_ROUTER_LIST_FAILED):
            return ProviderUnavailable
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
        fingerprint = str(getattr(doc, "code_fingerprint", "") or "")
        if not fingerprint:
            raise AttestationFailed("документ аттестации без отпечатка кода")
    except AttestationFailed:
        raise
    except Exception as e:  # noqa: BLE001 — непроверяемый документ трактуем строго
        raise AttestationFailed(f"документ аттестации не читается: {type(e).__name__}") from None
    release = str(getattr(doc, "release_tag", "") or "")
    host = str(getattr(doc, "enclave_host", "") or "")
    try:
        from importlib.metadata import version
        sdk = version("tinfoil")
    except Exception:  # noqa: BLE001 — версия для строки отчёта, не для решения
        sdk = "?"
    line = (f"роутер {host}, релиз {release or '?'}, отпечаток {fingerprint[:16] or '?'}, "
            f"SDK tinfoil {sdk}, сверка с подписанным релизом (Sigstore)")
    return line, fingerprint, release


def _read_state(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}   # битый файл = первый запуск (in3 DS-I4)
    return data if isinstance(data, dict) else {}


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
        attempt = entry.get("last_attempt")
        if isinstance(attempt, dict) and isinstance(attempt.get("reason"), str):
            # причину читает утренний бриф, а бриф пишется в граф: путь с именем
            # учётки туда не уходит (out1 DS-I3) — чистит единственный писатель
            attempt["reason"] = privacy.scrub_local_paths(attempt["reason"])[0]
        data[graph_key] = entry
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


def reject(state_path: pathlib.Path, graph_key: str, reason: str) -> None:
    """Ответ пришёл, но вызывающий его не принял (нет секций)."""
    def change(entry: dict) -> None:
        attempt = entry.get("last_attempt")
        if isinstance(attempt, dict):
            attempt.update(outcome="rejected", reason=reason[:300], accepted=False)

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
        raise _classify(e, "attest")("аттестация: " + _reason(e, key)) from None
    line, fingerprint, release = _verified(client)
    box["attested"] = (fingerprint, release)
    chunks: list[str] = []
    finish = None
    stage = "connect"
    try:
        # max_retries=0: повтор после отправки — вторая копия текста; таймаут —
        # между байтами, общий срок держит _under_deadline снаружи
        api = client.client.with_options(
            timeout=max(0.001, min(deadline.remaining(), READ_TIMEOUT_S)), max_retries=0)
        with box["lock"]:
            # единственный переход к отправке: сторож, поставивший cancel под тем
            # же замком, либо видит sent, либо знает, что create не начнётся (out2 C1)
            if box.get("cancel") or deadline.remaining() <= 0:
                raise DeadlineExpired("срок шага вышел до отправки")
            box["sent"] = True
        stream = api.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], stream=True,
            max_tokens=MAX_TOKENS, reasoning_effort=effort)
        stage = "stream"
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
        raise _classify(e, stage)("поток: " + _reason(e, key)) from None
    return "".join(chunks).strip(), finish, line


def _record(attempt: dict) -> Callable[[dict], None]:
    """Запись исхода попытки. Провал аттестации пишется ещё и отдельно: следующая
    ночь без сети перезапишет `last_attempt`, а тревога о подмене должна дожить
    до брифа, как и смена отпечатка (out3 DS-I1)."""
    def change(entry: dict) -> None:
        entry["last_attempt"] = attempt
        if attempt.get("outcome") == "attestation":
            entry["attestation_failure"] = {k: attempt.get(k) for k in ("at", "reason", "sent")}
    return change


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
               "outcome": "unavailable", "reason": "", "fingerprint": "", "release": "",
               "sent": False, "accepted": False}
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
        if finish != "stop":
            raise ReplyRejected(f"finish_reason={finish}, {len(text)} зн.")
        if not text:
            raise ReplyRejected("пустой ответ")
        if _ATTESTATION_HEADING.search(text):
            raise ReplyRejected(f"ответ модели содержит «{ATTESTATION_HEADER}» — подделка сигнала")
        # «доставлено» — не «принято»: принимает вызывающий после проверки секций (out1 DS-M6)
        attempt.update(outcome="delivered", reason="")
        return Reply(text=text, provider="tinfoil", model=model, attestation=line,
                     fingerprint=fingerprint, release=release)
    except NightCloudError as e:
        e.sent = bool(box.get("sent"))
        outcome = next((o for cls, o in _OUTCOMES if isinstance(e, cls)), "unavailable")
        attempt.update(outcome=outcome, reason=_scrub(e, key)[:300])
        raise
    except privacy.PrivacyRefused as e:
        attempt.update(outcome="refused", reason=str(e)[:300])
        raise
    except Exception as e:  # noqa: BLE001 — непредвиденное: исход записан, наружу классом двери
        attempt.update(outcome="unavailable", reason=_reason(e, key))
        err = ProviderUnavailable(_reason(e, key))
        err.sent = bool(box.get("sent"))
        raise err from None
    finally:
        # исход пишется всегда (in3 S-I4), но сбой записи не подменяет исход попытки
        attempt["sent"] = bool(box.get("sent"))
        _quietly("исход попытки", lambda: _update_state(state_path, graph_key, _record(attempt)))

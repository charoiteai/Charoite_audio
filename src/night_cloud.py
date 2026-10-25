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
- один срок `Deadline` на аттестацию и поток; истёкший срок — не провал
  аттестации, а «ночь кончилась» (свой класс);
- исход каждой попытки — в файл состояния по графу; утренний бриф берёт
  сигнал аттестации только оттуда, не из текста модели.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
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
#: Начало текста ValueError у SDK 0.15.0, когда недоступен список роутеров:
#: это сеть, а не подмена (замер 25.10, E2).
_ROUTER_LIST_FAILED = "Failed to fetch router"


class NightCloudError(Exception):
    """Исход двери, отличный от принятого ответа. Причина — без ключа."""


class AttestationFailed(NightCloudError):
    """Подпись, отпечаток или сверка с релизом не сошлись: ни байта не ушло."""


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


def _under_deadline(fn: Callable, args: tuple, deadline: Deadline):
    """Блокирующий вызов под сроком. Один помощник на дверь (in3 K1).

    Поток — daemon: брошенный по сроку не держит выход процесса. Результат
    брошенного потока не читается никогда, исключение потока — в контейнер,
    наружу его поднимает вызывающий поток.
    """
    box: dict = {}

    def run():
        try:
            box["value"] = fn(*args)
        except BaseException as e:  # noqa: BLE001 — переносим в свой поток как есть
            box["error"] = e

    worker = threads.spawn(run, name="night-cloud-attest", role="process",
                           detached="брошенная по сроку аттестация: результат не читается, "
                                    "ночной шаг выходит, не дожидаясь сети")
    worker.join(deadline.remaining())
    if worker.is_alive():
        raise DeadlineExpired("срок шага вышел во время аттестации")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _attest(cfg: dict, key: str):
    """Клиент Tinfoil: конструктор и есть аттестация (сеть, подпись, Sigstore)."""
    if not privacy.cloud_enrich_enabled(cfg):
        raise privacy.PrivacyRefused("облако выключено: sufler.cloud_enrich")
    try:
        from tinfoil import TinfoilAI
    except ImportError as e:
        raise ProviderUnavailable(
            f"пакет tinfoil не установлен ({type(e).__name__}): pip install 'charoite[tinfoil]'") from None
    try:
        return TinfoilAI(api_key=key)
    except OSError as e:   # requests ConnectionError/ProxyError — сеть, не подмена
        raise ProviderUnavailable("аттестация: " + _reason(e, key)) from None
    except ValueError as e:
        if str(e).startswith(_ROUTER_LIST_FAILED):
            raise ProviderUnavailable("аттестация: " + _reason(e, key)) from None
        raise AttestationFailed(_reason(e, key)) from None
    except Exception as e:  # noqa: BLE001 — неизвестное в аттестации трактуем строго
        raise AttestationFailed(_reason(e, key)) from None


def _verified(client) -> tuple[str, str, str]:
    """(строка аттестации, отпечаток, релиз) — или AttestationFailed."""
    doc = client.get_verification_document()
    if doc is None or getattr(doc, "security_verified", False) is not True:
        raise AttestationFailed("документ аттестации без security_verified")
    steps = getattr(doc, "steps", {}) or {}
    bad = [name for name, st in steps.items() if getattr(st, "status", "") != "success"]
    if bad:
        raise AttestationFailed("шаги аттестации не success: " + ", ".join(sorted(bad)))
    fingerprint = str(getattr(doc, "code_fingerprint", "") or "")
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


def accept(state_path: pathlib.Path, graph_key: str, reply: Reply) -> None:
    """Ответ прошёл проверку содержания у вызывающего — запомнить отпечаток.

    Отдельно от send (in3 DS-I1): принятым ответ становится не на приходе, а
    после проверки секций; прежний принятый отпечаток остаётся рядом — бриф
    сравнивает два, а не верит строке в отчёте.
    """
    today = dt.date.today().isoformat()

    def change(entry: dict) -> None:
        prev = entry.get("last_accepted")
        if isinstance(prev, dict) and prev.get("date") != today:
            entry["previous_accepted"] = prev
        entry["last_accepted"] = {"date": today, "fingerprint": reply.fingerprint,
                                  "release": reply.release, "model": reply.model}
        attempt = entry.get("last_attempt")
        if isinstance(attempt, dict):
            attempt["accepted"] = True

    _update_state(state_path, graph_key, change)


def reject(state_path: pathlib.Path, graph_key: str, reason: str) -> None:
    """Ответ пришёл, но вызывающий его не принял (нет секций)."""
    def change(entry: dict) -> None:
        attempt = entry.get("last_attempt")
        if isinstance(attempt, dict):
            attempt.update(outcome="rejected", reason=reason[:300], accepted=False)

    _update_state(state_path, graph_key, change)


def send(cfg: dict, prompt: str, *, deadline: Deadline, state_path: pathlib.Path,
         graph_key: str, transport: Callable | None = None) -> Reply:
    """Отправить промпт ночной ревизии провайдеру Tinfoil и вернуть ответ.

    `transport` — только для тестов: фабрика `(cfg, key) -> клиент` вместо
    аттестующего конструктора. Исключения — классы двери и PrivacyRefused.
    """
    if not privacy.cloud_enrich_enabled(cfg):
        raise privacy.PrivacyRefused("облако выключено: sufler.cloud_enrich")
    if privacy.night_provider(cfg) != "tinfoil":
        raise privacy.PrivacyRefused("ночной провайдер не tinfoil — дверь Tinfoil не отправляет")
    model = cloud.model(cfg, "cloud_night_tinfoil_model")
    effort = cloud.tinfoil_effort(cfg)
    key_path = cloud.tinfoil_key_path(cfg)
    attempt = {"date": dt.date.today().isoformat(), "provider": "tinfoil", "model": model,
               "outcome": "unavailable", "reason": "", "fingerprint": "", "release": "",
               "accepted": False}
    key = ""
    try:
        key = cloud.read_secret(key_path)
        if not key:
            raise ProviderUnavailable(f"нет ключа Tinfoil: {key_path}")
        client = _under_deadline(transport or _attest, (cfg, key), deadline)
        line, fingerprint, release = _verified(client)
        attempt.update(fingerprint=fingerprint, release=release)
        left = deadline.remaining()
        if left <= 0:
            raise DeadlineExpired("срок шага вышел до отправки")
        # max_retries=0: повтор после отправки — вторая копия текста; таймаут —
        # между байтами, общий срок держит проверка остатка на каждом куске
        api = client.client.with_options(timeout=max(0.001, min(left, READ_TIMEOUT_S)),
                                         max_retries=0)
        chunks: list[str] = []
        finish = None
        try:
            stream = api.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}], stream=True,
                max_tokens=MAX_TOKENS, reasoning_effort=effort)
            try:
                for chunk in stream:
                    if deadline.remaining() <= 0:
                        raise DeadlineExpired("срок шага вышел посреди ответа")
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    piece = getattr(choices[0].delta, "content", None)
                    if piece:
                        chunks.append(piece)
                    finish = getattr(choices[0], "finish_reason", None) or finish
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
        except NightCloudError:
            raise
        except Exception as e:  # noqa: BLE001 — openai/httpx без импорта: класс и код в причине
            raise ProviderUnavailable("поток: " + _reason(e, key)) from None
        text = "".join(chunks).strip()
        if finish != "stop":
            raise ReplyRejected(f"finish_reason={finish}, {len(text)} зн.")
        if not text:
            raise ReplyRejected("пустой ответ")
        if ATTESTATION_HEADER in text:
            raise ReplyRejected(f"ответ модели содержит «{ATTESTATION_HEADER}» — подделка сигнала")
        attempt.update(outcome="ok", reason="")
        return Reply(text=text, provider="tinfoil", model=model, attestation=line,
                     fingerprint=fingerprint, release=release)
    except AttestationFailed as e:
        attempt.update(outcome="attestation", reason=_scrub(e, key)[:300])
        raise
    except DeadlineExpired as e:
        attempt.update(outcome="deadline", reason=str(e))
        raise
    except ReplyRejected as e:
        attempt.update(outcome="rejected", reason=_scrub(e, key)[:300])
        raise
    except ProviderUnavailable as e:
        attempt.update(outcome="unavailable", reason=_scrub(e, key)[:300])
        raise
    except privacy.PrivacyRefused as e:
        attempt.update(outcome="refused", reason=str(e)[:300])
        raise
    except Exception as e:  # noqa: BLE001 — непредвиденное: исход записан, наружу классом двери
        attempt.update(outcome="unavailable", reason=_reason(e, key))
        raise ProviderUnavailable(_reason(e, key)) from None
    finally:
        # исход пишется всегда (in3 S-I4), но сбой записи не подменяет исход попытки
        try:
            _update_state(state_path, graph_key, lambda entry: entry.update(last_attempt=attempt))
        except OSError as e:
            print(f"night_cloud: исход не записан в {state_path.name}: {type(e).__name__}", flush=True)

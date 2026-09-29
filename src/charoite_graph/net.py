"""Транспорт к адресу на этой машине: напрямую, мимо прокси окружения и системы.

Прокси из `http_proxy`/`HTTPS_PROXY` и системных настроек macOS не трогают
loopback только если владелец не забыл `no_proxy`; исключения системы по
умолчанию loopback не покрывают. Сервер модели и память живут на 127.0.0.1, и
текст встречи не должен уходить на чужой прокси из-за забытой настройки.
Только stdlib: модуль читает и пакет, и приложение (№525).
"""
from __future__ import annotations

import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request

# localhost — не IP, ip_address() его не разбирает, а это самый частый адрес в конфиге.
_LOCAL_NAMES = ("localhost",)


def is_loopback_host(host: str | None) -> bool:
    """127.0.0.0/8, ::1 и localhost (без учёта регистра, с корневой точкой или без)."""
    if not host:
        return False
    if host.lower().rstrip(".") in _LOCAL_NAMES:
        return True
    try:        # IP разбирается как написан: «127.0.0.1.» — уже DNS-имя, а не адрес
        return ipaddress.ip_address(host).is_loopback
    except ValueError:      # имя машины, .local, домен — что угодно не-IP
        return False


class AmbiguousAddress(ValueError):
    """Адрес, который разные разборщики читают по-разному (`\\`, userinfo, пробелы в authority)."""


def url_host(url: str) -> str | None:
    """Хост адреса — единственный разбор для гейта приватности и выбора транспорта.

    `urlsplit` не считает `\\` концом authority и берёт хост после последнего `@`, а
    urllib3 обрывает authority на `\\`: `http://evil.example\\@127.0.0.1:11434` для
    первого — 127.0.0.1, для второго — evil.example. Такой адрес — отказ, а не свой и
    не чужой (финальный Opus по №525, I3).
    """
    # urlsplit молча выкидывает \t \r \n из адреса, а другой разбор их не выкинет: смотрим сырую строку
    m = re.match(r"[^/?#]*://([^/?#]*)", url)
    netloc = m.group(1) if m else urllib.parse.urlsplit(url).netloc
    if "\\" in netloc or "@" in netloc or any(c.isspace() or ord(c) < 32 for c in netloc):
        raise AmbiguousAddress(f"адрес {url!r}: authority неоднозначна (обратная косая, userinfo или пробел)")
    return urllib.parse.urlsplit(url).hostname


def loopback_url(url: str) -> bool:
    """Указывает ли адрес на эту машину; неоднозначный адрес — нет."""
    try:
        return is_loopback_host(url_host(url))
    except AmbiguousAddress:
        return False


class _NoRedirectOffHost(urllib.request.HTTPRedirectHandler):
    """Редирект с loopback на нелокальную цель — отказ, а не переход."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not loopback_url(target):
            raise urllib.error.HTTPError(
                req.full_url, code, f"редирект с этой машины на {urllib.parse.urlsplit(target).hostname!r} отклонён",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _direct_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectOffHost())


def open_url(request: urllib.request.Request | str, timeout: float):
    """`urlopen`, который для адреса на этой машине не знает прокси.

    Нелокальный адрес идёт обычным `urllib.request.urlopen` (прокси по правилам
    окружения — как было).
    """
    url = request if isinstance(request, str) else request.full_url
    if loopback_url(url):
        return _direct_opener().open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)  # nosemgrep — вызывающий проверил схему

"""Транспорт к адресу на этой машине: напрямую, мимо прокси окружения и системы.

Прокси из `http_proxy`/`HTTPS_PROXY` и системных настроек macOS не трогают
loopback только если владелец не забыл `no_proxy`; исключения системы по
умолчанию loopback не покрывают. Сервер модели и память живут на 127.0.0.1, и
текст встречи не должен уходить на чужой прокси из-за забытой настройки.
Только stdlib: модуль читает и пакет, и приложение (№525).
"""
from __future__ import annotations

import ipaddress
import urllib.error
import urllib.parse
import urllib.request

# localhost — не IP, ip_address() его не разбирает, а это самый частый адрес в конфиге.
_LOCAL_NAMES = ("localhost",)


def is_loopback_host(host: str | None) -> bool:
    """127.0.0.0/8, ::1 и localhost (без учёта регистра, с корневой точкой или без)."""
    if not host:
        return False
    name = host.lower().rstrip(".")
    if name in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:      # имя машины, .local, домен — что угодно не-IP
        return False


class _NoRedirectOffHost(urllib.request.HTTPRedirectHandler):
    """Редирект с loopback на нелокальную цель — отказ, а не переход."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        if not is_loopback_host(target.hostname):
            raise urllib.error.HTTPError(
                req.full_url, code, f"редирект с этой машины на {target.hostname!r} отклонён",
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
    if is_loopback_host(urllib.parse.urlsplit(url).hostname):
        return _direct_opener().open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)  # nosemgrep — вызывающий проверил схему

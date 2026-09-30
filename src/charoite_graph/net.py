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

from charoite_graph.own_network import is_own_host

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


#: Authority по белой грамматике: имя из букв (в том числе не латинских: IDN), цифр, точки, дефиса и `_` либо IPv6 в скобках
#: без zone id, порт цифрами. Всё остальное (`\\`, `@`, `%`, пробелы, юникод) разные разборщики читают
#: по-разному: urlsplit, `unquote` в urllib.request, http.client и urllib3 (№525, I3 и C1 круга 2).
_AUTHORITY = re.compile(r"(?:(?P<name>[\w.-]+)|\[(?P<v6>[0-9A-Fa-f:.]+)\])(?::[0-9]{0,5})?\Z")


def url_host(url: str) -> str | None:
    """Хост адреса — единственный разбор для гейта приватности и выбора транспорта.

    Authority берётся из сырой строки до первого `/`, `?` или `#` и обязана лечь в белую
    грамматику, иначе `AmbiguousAddress`: `http://evil.example\\@127.0.0.1:11434` для urlsplit —
    127.0.0.1, для urllib3 — evil.example, а `http://[::1%5D.evil.example]:1` после `unquote`
    в urllib.request — имя в зоне evil.example. Адрес без authority — `None`.
    """
    m = re.match(r"[^/?#]*://([^/?#]*)", url)
    authority = m.group(1) if m else urllib.parse.urlsplit(url).netloc
    if not authority:
        return None
    g = _AUTHORITY.match(authority)
    if g is None:
        raise AmbiguousAddress(f"адрес {url!r}: authority вне белой грамматики (имя или IPv6 в скобках, порт цифрами)")
    v6 = g.group("v6")
    if v6 is not None:
        try:        # набор символов — не IPv6: «[127.0.0.1]» urllib3 отвергает, http.client снимает скобки
            ipaddress.IPv6Address(v6)
        except ValueError as e:
            raise AmbiguousAddress(f"адрес {url!r}: в скобках не IPv6") from e
    return (g.group("name") or v6).lower()


def loopback_url(url: str) -> bool:
    """Указывает ли адрес на эту машину; неоднозначный адрес — нет."""
    try:
        return is_loopback_host(url_host(url))
    except (AmbiguousAddress, ValueError):
        return False


def direct_url(url: str) -> bool:
    """Идти ли к адресу напрямую, мимо прокси окружения и системы.

    Да — адрес на этой машине и открытый http в свою сеть (`own_network`): политика
    пустила его ровно потому, что текст остаётся в локальной сети, и прокси увёл бы
    его наружу открытым текстом (финальный круг Opus по №522, I1). https во внешний
    мир и в свою сеть идёт по правилам прокси, как раньше; неоднозначный адрес — нет.
    """
    try:
        host = url_host(url)
    except (AmbiguousAddress, ValueError):
        return False
    if is_loopback_host(host):
        return True
    return urllib.parse.urlsplit(url).scheme.lower() == "http" and is_own_host(host)


def _same_host(a: str, b: str) -> bool:
    try:
        return url_host(a) == url_host(b)
    except (AmbiguousAddress, ValueError):
        return False


class _NoRedirectOffHost(urllib.request.HTTPRedirectHandler):
    """Редирект с прямого адреса наружу — отказ, а не переход.

    С этой машины — только на эту машину; с открытого http в свою сеть — только на тот
    же хост тем же прямым путём: иначе редирект увёл бы запрос мимо проверки политики.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        allowed = (loopback_url(target) if loopback_url(req.full_url)
                   else direct_url(target) and _same_host(target, req.full_url))
        if not allowed:
            raise urllib.error.HTTPError(
                req.full_url, code, f"редирект с этой машины на {urllib.parse.urlsplit(target).hostname!r} отклонён",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _direct_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectOffHost())


def open_url(request: urllib.request.Request | str, timeout: float):
    """`urlopen`, который для прямого адреса (`direct_url`) не знает прокси.

    Остальные адреса идут обычным `urllib.request.urlopen` (прокси по правилам
    окружения — как было).
    """
    url = request if isinstance(request, str) else request.full_url
    if direct_url(url):
        return _direct_opener().open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)  # nosemgrep — вызывающий проверил схему

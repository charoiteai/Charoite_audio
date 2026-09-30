"""Политика адреса модели: можно ли слать тексты на этот адрес (№522).

Одна функция решения на пакет и приложение. Пакет зовёт её из фабрики
векторизатора (`embed_door.ollama_embedder`), приложение — из
`privacy._guarded_url`: там конфиг и рубильник превращаются в аргументы, а отказ —
в свой текст с ключом конфига. Своих веток решения у потребителей нет.

Порядок решений: адрес разбирается строго (`net.url_host`, неоднозначный — отказ)
→ схема только http(s), и для этой машины тоже → адрес на этой машине — да →
`offline` — отказ любому другому адресу → открытый http только в своей сети
(частный, link-local IP; имя без точки или из домашних доменов — если DNS
резолвит его целиком в свою сеть) → иначе только при `allow_remote is True`.

Окружения модуль не читает: «взведён ли рубильник» вызывающий передаёт значением
(`offline`), имён переменных пакет не знает. DNS спрашивается только в ветке
открытого http к имени — https и отказ по рубильнику резолва не делают.
"""
from __future__ import annotations

import functools
import ipaddress
import socket
import urllib.parse

from charoite_graph.net import AmbiguousAddress, is_loopback_host, url_host

#: Адрес Ollama по умолчанию — сервер на этой машине.
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

#: Домашние домены: имя из них — кандидат в свою сеть (решает резолв). RFC 8375 — .home.arpa.
HOME_SUFFIXES = (".local", ".lan", ".home", ".internal", ".home.arpa")

#: Виды отказа: вызывающий строит по ним свой текст (ключ конфига, флаг командной строки).
KINDS = ("ambiguous", "scheme", "offline", "cleartext", "remote")


class AddressRefused(ValueError):
    """Политика отказала адресу.

    `kind` — вид из `KINDS`, `url` — адрес как передан, `scheme` — его схема
    (для `scheme`), `detail` — причина разбора (для `ambiguous`: текст
    `AmbiguousAddress` или `ValueError` разборщика). Текст исключения — общий,
    без имён параметров, ключей конфига и флагов: как разрешить удалённый адрес
    (`allow_remote=True`, `llm.allow_remote`, `--allow-remote`), называет
    потребитель по `kind`.
    """

    def __init__(self, kind: str, url: str, *, scheme: str = "", detail: str = ""):
        if kind not in KINDS:
            raise ValueError(f"неизвестный вид отказа адресу: {kind!r}")
        self.kind, self.url, self.scheme, self.detail = kind, url, scheme, detail
        super().__init__(_TEXT[kind](self))


_TEXT = {
    "ambiguous": lambda e: f"адрес {e.url}: {e.detail}",
    "scheme": lambda e: f"адрес {e.url}: схема «{e.scheme or '—'}» не поддерживается, нужен http(s)",
    "offline": lambda e: f"адрес {e.url} указывает не на эту машину, а выход наружу запрещён",
    "cleartext": lambda e: (f"адрес {e.url} — вне своей сети по открытому http: текст ушёл бы по сети "
                            "открытым текстом. Для удалённого адреса нужен https (разрешение на удалённый "
                            "адрес этого не снимает)"),
    "remote": lambda e: (f"адрес {e.url} указывает не на эту машину: чтобы слать туда тексты, нужно явное "
                         "разрешение на удалённый адрес"),
}


def _ip_private(ip) -> bool:
    # «::ffff:8.8.8.8» — IPv4 в одежде IPv6: Python до 3.11.x/3.12.x без делегирования
    # считал весь ::ffff:0:0/96 частным, и публичный адрес проходил бы как своя сеть
    # (выходной круг 1 по №522, Sonnet M3; опыт: 3.9 — is_private True, 3.12.13 — False).
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_private or ip.is_link_local or ip.is_loopback


@functools.lru_cache(maxsize=64)
def _resolves_private(host: str) -> bool:
    """Имя своей сети обязано и резолвиться в свою сеть: имя без точки на macOS
    дополняется search domain, и «ollama» в корпоративной сети — чужой хост
    (круг-1 по #562, GLM I1). Не резолвится или публичный адрес — отказ."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    ips = {str(info[4][0]).split("%")[0] for info in infos}
    try:
        return bool(ips) and all(_ip_private(ipaddress.ip_address(ip)) for ip in ips)
    except ValueError:
        return False


def is_private_host(host: str | None) -> bool:
    """Адрес своей сети: частный, link-local или loopback IP; имя из домашних
    доменов (.local, .home.arpa и подобные) или без точек — если резолвится в
    такой же адрес. Для него http допустим."""
    if not host:
        return False
    try:
        return _ip_private(ipaddress.ip_address(host))
    except ValueError:
        h = host.lower().rstrip(".")       # FQDN с корневой точкой — то же имя (DS I2)
        if "." not in h or h.endswith(HOME_SUFFIXES):
            return _resolves_private(h)
        return False


def guard_model_url(url: str, *, allow_remote: object = False, offline: bool = False) -> str:
    """Адрес модели без хвостового `/` — или `AddressRefused`.

    `allow_remote` разрешает только строгое `True`: строка «true», единица и
    прочее — не разрешение. `offline` — выход наружу запрещён целиком (рубильник
    приложения); адрес на этой машине он не трогает.
    """
    url = url.rstrip("/")
    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        host = url_host(url)
    except (AmbiguousAddress, ValueError) as e:     # «http://[::1» — тоже отказ, а не голый ValueError
        raise AddressRefused("ambiguous", url, detail=str(e)) from e
    if scheme not in ("http", "https"):     # и для loopback: транспорт такую схему не поймёт (DS M7)
        raise AddressRefused("scheme", url, scheme=scheme)
    if is_loopback_host(host):
        return url
    if offline:
        raise AddressRefused("offline", url)
    # Схема — часть политики, не только адрес: allow_remote разрешал http на
    # чужую машину, и текст шёл бы по сети открытым текстом (аудит 13.09,
    # DS M3). Своя сеть (RFC 1918, link-local, .local) — http допустим: Ollama
    # на соседнем Mac TLS не умеет; всё, что дальше, — только https.
    if scheme == "http" and not is_private_host(host):
        raise AddressRefused("cleartext", url, scheme=scheme)
    if allow_remote is True:
        return url
    raise AddressRefused("remote", url, scheme=scheme)

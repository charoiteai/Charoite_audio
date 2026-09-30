"""«Своя сеть»: адрес, до которого открытый http не уходит в интернет (№522).

Один предикат на двух потребителей: политика адреса (`address_policy`) решает по
нему, можно ли слать открытым http, транспорт (`net.open_url`, `privacy.proxies_for`) —
идти ли мимо прокси. Пока предикат был только у политики, http «в свою сеть»
пропускался и тут же уходил на прокси окружения или системы (финальный круг Opus
по №522, I1). Только stdlib; окружение не читается — но системный резолвер для
имён читает своё: на Linux glibc смотрит `HOSTALIASES`, `LOCALDOMAIN`, `RES_OPTIONS`.

Своя сеть — явный список сетей, а не `ipaddress.is_private`: у stdlib «private»
значит «IANA не считает глобально достижимым», и туда попадают туннели в интернет —
Teredo 2001::/32, 6to4 2002::/16, NAT64 64:ff9b::/96 (финальный круг Opus, I2);
состав `_private_networks` к тому же меняется от выпуска к выпуску Python.
"""
from __future__ import annotations

import functools
import ipaddress
import socket

#: Сети, открытый http в которые остаётся в локальной сети: RFC 1918, link-local,
#: ULA, loopback. IPv4 в одежде IPv6 (::ffff:a.b.c.d) разворачивается до сверки.
OWN_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8",
    "fc00::/7", "fe80::/10", "::1/128",
))

#: Домашние домены: имя из них — кандидат в свою сеть (решает резолв). RFC 8375 — .home.arpa.
HOME_SUFFIXES = (".local", ".lan", ".home", ".internal", ".home.arpa")


def ip_is_own(ip) -> bool:
    """IP из `OWN_NETWORKS`; `::ffff:a.b.c.d` судится как a.b.c.d."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return any(ip.version == net.version and ip in net for net in OWN_NETWORKS)


@functools.lru_cache(maxsize=64)
def _resolves_own(host: str) -> bool:
    """Имя своей сети обязано и резолвиться в свою сеть: имя без точки на macOS
    дополняется search domain, и «ollama» в корпоративной сети — чужой хост
    (круг-1 по #562, GLM I1). Не резолвится, не адрес или хоть один адрес чужой — нет."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    ips = {str(info[4][0]).split("%")[0] for info in infos}
    try:
        return bool(ips) and all(ip_is_own(ipaddress.ip_address(ip)) for ip in ips)
    except ValueError:
        return False


def is_own_host(host: str | None) -> bool:
    """Хост своей сети: IP из `OWN_NETWORKS`; имя без точки или из домашних доменов —
    если резолвится целиком в свою сеть. Остальное — нет."""
    if not host:
        return False
    try:
        return ip_is_own(ipaddress.ip_address(host))
    except ValueError:
        h = host.lower().rstrip(".")       # FQDN с корневой точкой — то же имя (DS I2)
        if "." not in h or h.endswith(HOME_SUFFIXES):
            return _resolves_own(h)
        return False

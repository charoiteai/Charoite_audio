"""Политика адреса модели: можно ли слать тексты на этот адрес (№522).

Одна функция решения на пакет и приложение. Пакет зовёт её из фабрики
векторизатора (`embed_door.ollama_embedder`), приложение — из
`privacy._guarded_url`: там конфиг и рубильник превращаются в аргументы, а отказ —
в свой текст с ключом конфига. Своих веток решения у потребителей нет.

Порядок решений: адрес разбирается строго (`net.url_host`, неоднозначный — отказ)
→ схема только http(s), и для этой машины тоже → адрес на этой машине — да →
`offline` — отказ любому другому адресу → открытый http только в своей сети
(`own_network`: RFC 1918, link-local, ULA — явным списком; имя без точки или
из домашних доменов — если DNS резолвит его целиком в свою сеть) → иначе только при `allow_remote is True`.

Окружения модуль не читает: «взведён ли рубильник» вызывающий передаёт значением
(`offline`), имён переменных пакет не знает. DNS спрашивается только в ветке
открытого http к имени — https и отказ по рубильнику резолва не делают.
"""
from __future__ import annotations

import urllib.parse

from charoite_graph.net import AmbiguousAddress, is_loopback_host, url_host
from charoite_graph.own_network import is_own_host

#: Адрес Ollama по умолчанию — сервер на этой машине.
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

#: `detail` отказа `cleartext` для 0.0.0.0 и ::: вызывающий добавляет совет «укажите 127.0.0.1».
UNSPECIFIED = "unspecified"
UNSPECIFIED_HINT = " 0.0.0.0 и :: — не адрес сервера, а «слушать все адреса»: укажите 127.0.0.1."

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
    # AmbiguousAddress уже называет адрес («адрес 'x': …»), ValueError разборщика — нет
    "ambiguous": lambda e: e.detail if e.detail.startswith("адрес ") else f"адрес {e.url}: {e.detail}",
    "scheme": lambda e: f"адрес {e.url}: схема «{e.scheme or '—'}» не поддерживается, нужен http(s)",
    "offline": lambda e: f"адрес {e.url} указывает не на эту машину, а выход наружу запрещён",
    "cleartext": lambda e: (f"адрес {e.url} — вне своей сети по открытому http: текст ушёл бы по сети "
                            "открытым текстом. Для удалённого адреса нужен https (разрешение на удалённый "
                            "адрес этого не снимает)" + (UNSPECIFIED_HINT if e.detail == UNSPECIFIED else "")),
    "remote": lambda e: (f"адрес {e.url} указывает не на эту машину: чтобы слать туда тексты, нужно явное "
                         "разрешение на удалённый адрес"),
}


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
    if scheme == "http" and not is_own_host(host):
        # 0.0.0.0 и :: — не адрес сервера, а «слушать всё» из OLLAMA_HOST: совет назвать 127.0.0.1
        raise AddressRefused("cleartext", url, scheme=scheme,
                             detail=UNSPECIFIED if host in ("0.0.0.0", "::") else "")
    if allow_remote is True:
        return url
    raise AddressRefused("remote", url, scheme=scheme)

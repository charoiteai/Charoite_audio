"""Схема хранилища графа: имена папок, разделов и сырья — значением, а не
литералами по месту.

Одно имя папки узла жило литералом в десятке мест: конвейер писал в «Люди»,
поиск исключал «Встречи-архив», досье читало «Досье», а сторож раскладки знал
те же строки ещё раз своей таблицей. Пока имя не собрано в значение, переезд
папки — это правка всех копий, и любая забытая копия молчит: граф просто
перестаёт видеть кусок хранилища. Здесь схема объявлена значением, а список
полей читает сторож литералов (`scripts/layout_map.py`), сверяя его с самими
константами, пока те живы (PR B переводит потребителей на это значение).

Схема — хранилище Чароита, а не вкус запуска: значения приходят из
`charoite_schema.CHAROITE`, а инварианты ниже отвергают схему, которой нельзя
верить, — имя-регулярку, папку досье внутри исключения, роль в двух ролях.
Проверки — в `__post_init__`, отказ `ValueError`: у полей НЕТ значений по
умолчанию, и «значение Чароита» ровно одно — в `CHAROITE`.

Модуль — член пакета `charoite_graph`: импортирует только stdlib и членов
пакета, окружения не знает.
"""
from __future__ import annotations

import dataclasses
import unicodedata

#: Поля-имена: каждое их значение — одно имя (папка, раздел, префикс). Форма
#: одна на все — `_name_problem`. `exclude_dirs` — пути, поэтому форма
#: спрашивается у каждого сегмента отдельно.
_NAME_FIELDS = ("node_folders", "people_folders", "core_folders", "meeting_folders",
                "dossier_dir", "meeting_dir", "service_prefixes", "history_heads",
                "meeting_link_prefixes")

#: Поля-литералы: значение ищется в тексте как ПОДСТРОКА/суффикс, поэтому оно не
#: регулярка. Метки `\ ^ $ |` в литерале — либо опечатка, либо тайная регулярка,
#: которая сравнивается буквально и не находит ничего.
_LITERAL_FIELDS = ("raw_markers", "raw_suffixes", "history_heads")
_LITERAL_METACHARS = ("\\", "^", "$", "|")

#: Роли попарно не пересекаются — объявлением пар, а не россыпью `if`: пара с
#: забытой проверкой не заводится, а видна списком. Сравнение — по
#: нормализованному имени (NFC + casefold): «Люди» и «люди» — одна роль.
_DISJOINT_PAIRS = (
    ("people_folders", "core_folders"),
    ("node_folders", "meeting_folders"),
    ("dossier_dir", "node_folders"),
    ("dossier_dir", "meeting_folders"),
)


def _norm(value: str) -> str:
    """Имя роли для сравнения: сборка Unicode + casefold."""
    return unicodedata.normalize("NFC", value).casefold()


def _name_problem(value: str) -> str | None:
    """Форма имени: непустое, не из одних пробелов, без «/», не с точки."""
    if not value.strip():
        return "имя непустое и не из одних пробелов"
    if "/" in value:
        return "имя без «/»"
    if value.startswith("."):
        return "имя не начинается с точки"
    return None


def _literal_problem(value: str) -> str | None:
    """Литерал: непустой и без меток регулярки «\\ ^ $ |»."""
    if not value:
        return "литерал непустой"
    метка = next((c for c in _LITERAL_METACHARS if c in value), None)
    if метка is not None:
        return f"литерал без метки регулярки {метка!r} — это подстрока, не шаблон"
    return None


def _as_names(value: object) -> tuple[str, ...]:
    """Значение поля как кортеж имён: строка — одно имя."""
    return (value,) if isinstance(value, str) else tuple(value)  # type: ignore[arg-type]


@dataclasses.dataclass(frozen=True)
class GraphSchema:
    """Схема хранилища графа: имена папок и разделов — кортежами, как на диске.

    У полей нет значений по умолчанию: умолчание сделало бы схему частичной, и
    забытое поле молча взяло бы чужое имя. Единственное значение — `CHAROITE`."""

    node_folders: tuple[str, ...]
    people_folders: tuple[str, ...]
    core_folders: tuple[str, ...]
    meeting_folders: tuple[str, ...]
    dossier_dir: str
    meeting_dir: str
    exclude_dirs: tuple[str, ...]
    service_prefixes: tuple[str, ...]
    history_heads: tuple[str, ...]
    raw_markers: tuple[str, ...]
    raw_suffixes: tuple[str, ...]
    meeting_link_prefixes: tuple[str, ...]

    def __post_init__(self) -> None:
        self._check_names()
        self._check_literals()
        self._check_roles()

    def _check_names(self) -> None:
        """Инвариант 1: форма имени — у полей-имён и у каждого сегмента путей."""
        for имя in _NAME_FIELDS:
            for value in _as_names(getattr(self, имя)):
                беда = _name_problem(value)
                if беда:
                    raise ValueError(f"{имя}: {value!r} — {беда}")
        for path in self.exclude_dirs:
            for segment in path.split("/"):
                беда = _name_problem(segment)
                if беда:
                    raise ValueError(f"exclude_dirs: {path!r} — сегмент {segment!r} — {беда}")

    def _check_literals(self) -> None:
        """Инвариант 2: сырьё и головы — литералы без меток регулярки."""
        for имя in _LITERAL_FIELDS:
            for value in _as_names(getattr(self, имя)):
                беда = _literal_problem(value)
                if беда:
                    raise ValueError(f"{имя}: {value!r} — {беда}")

    def _names(self, имя: str) -> frozenset[str]:
        """Нормализованные имена роли — и для строки-папки, и для кортежа."""
        return frozenset(_norm(v) for v in _as_names(getattr(self, имя)))

    def _check_roles(self) -> None:
        """Инварианты 3–7: подмножества, членство, досье против исключений,
        исключения против узлов и попарная непересекаемость ролей."""
        nodes = self._names("node_folders")
        if not self._names("people_folders").issubset(nodes):
            raise ValueError("people_folders не подмножество node_folders")
        if not self._names("core_folders").issubset(nodes):
            raise ValueError("core_folders не подмножество node_folders")
        if _norm(self.meeting_dir) not in self._names("meeting_folders"):
            raise ValueError(f"meeting_dir {self.meeting_dir!r} не из meeting_folders")
        dossier = _norm(self.dossier_dir)
        for path in self.exclude_dirs:
            segments = tuple(_norm(s) for s in path.split("/"))
            if dossier in segments:
                # досье — одно имя (инвариант 1), поэтому «исключение внутри досье»
                # и «досье внутри исключения» совпадают с наличием имени в пути
                raise ValueError(f"исключение {path!r} и папка досье {self.dossier_dir!r} "
                                 f"не вкладываются друг в друга")
            first = segments[0]
            if first in nodes and first not in self._names("meeting_folders"):
                raise ValueError(f"исключение {path!r} пересекается с папками узлов "
                                 f"(законное пересечение — только архив встреч)")
        for left, right in _DISJOINT_PAIRS:
            общие = sorted(self._names(left) & self._names(right))
            if общие:
                raise ValueError(f"роли {left} и {right} пересекаются: {', '.join(общие)}")

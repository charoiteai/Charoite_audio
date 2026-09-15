"""Запись файла, которая не оставляет пустоты вместо данных.

`write_text` открывает файл на «w» и УСЕКАЕТ его до нуля прежде, чем что-то
записать. Полный том, исчерпанная квота iCloud, kill ночного цикла посреди
записи — и вместо узла графа, копившегося год, остаётся файл в 0 байт.
Восстанавливать неоткуда: это единственный экземпляр.

Паттерн «во временный файл, потом replace» проект знал и раньше — он был
скопирован руками в пяти местах (стенограмма, минутки, досье, забывание
встречи, wav), а десяток записей в граф жили без него. Здесь он один на
всех, потому что копия рядом с копией разъезжается: в одной есть finally,
в другой нет.

`replace` на POSIX атомарен в пределах файловой системы: читатель видит либо
старую версию целиком, либо новую целиком. Временный файл — рядом с целевым,
не в /tmp: перенос между томами атомарным не бывает.
"""
from __future__ import annotations

import os
import pathlib
import stat


def stat_snapshot(path: pathlib.Path) -> tuple[int, int] | None:
    """Снимок (mtime_ns, size) ОДНИМ stat — для expect-гейта write_text.

    Два подряд вызова stat дают химеру: чужой replace между ними — и mtime
    от старой версии склеивается с размером новой (DS r2 по #464).
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def write_text(path: pathlib.Path, text: str, *, encoding: str = "utf-8",
               expect: tuple[int, int] | None = None,
               expect_absent: bool = False) -> bool:
    """Записать текст так, чтобы обрыв не уничтожил прежнее содержимое.

    `expect` — снимок `stat_snapshot`, взятый ДО чтения исходника: если к
    моменту записи файл уже не тот, запись не делается и возвращается False.
    `expect_absent` — гейт для «файла не было»: `expect=None` означает
    свободную запись, а не «пиши, только если его по-прежнему нет» — и
    документ, появившийся за время долгой генерации, затирался бы молча
    (GLM Critical по #483). С флагом чужой файл, возникший в окне, остаётся.
    Гейт потери обновления жил копиями в демоне и пересборке (у каждого свой
    протокол — критика DS по #464); здесь он один на всех писателей.
    Проверка — перед самым replace: окно гонки сжато до минимума, но не до
    нуля — это защита от затирания, не замок.
    """
    # Симлинк в графе ведёт к настоящему файлу, и писать надо в него: иначе
    # `replace` подменил бы саму ссылку обычным файлом, а цель осталась со
    # старым текстом (DS, круг-1 по PR #441).
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # PID в имени: два процесса, пишущие один узел, не должны собирать файл
    # друг за другом. Кто заменит последним — тот и победил, но целиком.
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(text, encoding=encoding)
        _carry_over_metadata(path, tmp)
        if expect is not None and stat_snapshot(path) != expect:
            return False
        if expect_absent and path.exists():
            return False
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)   # replace уже унёс файл — это не ошибка
    return True


def claim(path: pathlib.Path) -> bool:
    """Занять имя файла: `O_CREAT|O_EXCL` — единственная атомарная проверка «файла
    не было» в POSIX. `write_text(expect_absent=True)` сжимает окно гонки, но
    проверка и `replace` там — две операции: две диктовки в одну минуту с одним
    заголовком могли обе пройти «файла нет» и затереть друг друга (DS r2 M2 /
    GLM r2 M4 по #559). Занятое имя — False, вызывающий берёт следующее. Файл
    остаётся пустым до `write_text`, который заменит его целиком."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def _carry_over_metadata(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Перенести на новый файл права и метки Finder со старого.

    `replace` создаёт новый inode, и без этого шага узел после первой же
    правки терял бы цветные метки, комментарии Spotlight и выставленные
    вручную права (GLM, круг-1 по PR #441). Ни одна из потерь не роняет
    конвейер, поэтому сбой переноса не должен рушить саму запись.

    Времена НЕ переносим — только права и атрибуты. Иначе узел, обновлённый
    сегодня, но записанный до того неделю назад, выглядел бы недельным, а по
    mtime ночь отбирает работу: `tier3.changed_since` берёт ядра свежее
    прошлого прогона, и такое ядро выпадало бы из инкремента до следующего
    полного прохода. Тем же mtime живёт кэш `graph_nodes.NodeIndex`
    (DS, круг-2 по PR #441).

    Права ставим явным `chmod`, а не `shutil.copymode`: они делают одно и то
    же, но здесь важно, чего мы НЕ делаем, и это должно читаться в строке, а
    не в документации shutil. `copystat` в этом месте — готовая ловушка:
    отличается одной буквой, а тащит времена.
    """
    if not src.exists():
        return
    try:
        os.chmod(dst, stat.S_IMODE(src.stat().st_mode))
    except OSError:
        pass
    try:                              # расширенные атрибуты: теги и комментарии
        for name in os.listxattr(src):
            try:
                os.setxattr(dst, name, os.getxattr(src, name))
            except OSError:
                continue
    except (OSError, AttributeError):
        pass


class LostRace(RuntimeError):
    """Запись не сделана, на диске осталась чужая версия. Машинный сигнал
    вызывающему: лог не должен выдавать это за «нечего дописывать» (GLM I2 /
    DS I2 по #553); строки с PREFIX в списках `dropped` — тот же сигнал там,
    где исключение не проходит.

    Причина — ЗНАЧЕНИЕ (`kind`), а не текст: ТРИ вида, и каждый требует от
    вызывающего разного.

    `CHANGED` — файл сменился под рукой дважды подряд: наша правка не легла на
    живой файл, поверх писал кто-то ещё. `GONE` — файла нет (исчез до снимка
    или между снимком и чтением): править нечего, и упрекнуть его не в чем. Это
    конец состояния, а не отсутствие истории: на второй попытке `GONE` приходит
    ПОСЛЕ проигранной записи, то есть чужая правка была — но файла всё равно
    больше нет (GLM M1 r1 по №277). `UNREACHABLE` — до файла не дотянулись
    (права, том, ввод-вывод), он остался как был, и сказать об этом надо вслух;
    подробность системы лежит в `detail` ОТДЕЛЬНЫМ полем.

    Человеческий текст (`reason`, и через него сообщение) собирается здесь, в
    одном месте, из вида и подробности. Вызывающий спрашивает `gone` /
    `unreachable` / `kind` и НИКОГДА не разбирает текст: он за один круг
    менялся дважды, и сравнение с литералом в чужом модуле ломалось бы молча.
    Первая версия этого набора оставила `UNREACHABLE` префиксом текста — и
    третий вид немедленно снова начали опознавать через `startswith`, то есть
    дефект воспроизвёлся внутри решения (DS C1/I3 и GLM I1, критика 1, r1)."""

    PREFIX = "запись не состоялась: "
    CHANGED = "changed"
    GONE = "gone"
    UNREACHABLE = "unreachable"

    _SAID = {CHANGED: "сменились под рукой",
             GONE: "файла нет",
             UNREACHABLE: "снимок не снят"}

    def __init__(self, path: pathlib.Path, what: str, kind: str, detail: str = ""):
        # Вид обязателен и без умолчания: умолчанием был CHANGED — самая
        # обвинительная из трёх причин, и новое место отказа получало
        # «поверх писал кто-то ещё» бесплатно, ничем не подтверждённое
        # (DS I2 r2). Неизвестный вид — своя ошибка, а не KeyError поверх
        # настоящей причины отказа записи (DS M3 r2).
        said = self._SAID.get(kind)
        if said is None:
            raise ValueError(f"неизвестный вид причины: {kind!r}")
        self.path = path
        self.kind = kind              # вид причины — значение, его и спрашивают
        self.detail = detail          # подробность системы, отдельно от вида
        self.reason = said + (f": {detail}" if detail else "")
        super().__init__(f"{self.PREFIX}{path.name} {self.reason} — {what}")

    @property
    def gone(self) -> bool:
        """Файла нет — в нём нечего править и не в чем его упрекнуть."""
        return self.kind == self.GONE

    @property
    def unreachable(self) -> bool:
        """До файла не дотянулись — он остался как был, и это надо сказать."""
        return self.kind == self.UNREACHABLE


def _snapshot_or_raise(path: pathlib.Path, what: str) -> tuple[int, int]:
    """Снимок ОДНИМ stat — или LostRace с причиной из ошибки этого же stat.

    «Файла нет» и «не дотянулись» — разные факты, и вызывающему важна именно
    эта разница: в исчезнувшем файле нечего править, а недоступный остаётся
    как был. Второй опрос диска для различения не годится — это тот же `stat`,
    который только что отказал, и он либо соврёт на гонке, либо бросит сам
    (DS r4 и r5 по №273). Поэтому причину берём прямо из ошибки первого.

    Бросает сама, а не возвращает пару «снимок или причина»: причина без
    исключения — значение, которое вызывающий может забыть проверить, и
    «причины нет» пришлось бы кодировать пустой строкой, неотличимой от
    настоящей (DS M5 r6).
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        raise LostRace(path, what, kind=LostRace.GONE) from None
    except OSError as exc:
        raise LostRace(path, what, kind=LostRace.UNREACHABLE,
                       detail=str(exc.strerror or exc)) from None
    return st.st_mtime_ns, st.st_size


def rewrite_file(path: pathlib.Path, transform, what: str) -> int:
    """Чтение → преобразование → запись с гейтом expect по снимку до чтения,
    две попытки — как canonize_file и restamp_minutes пересборки: чужой
    процесс замка демона не видит, а одноразовый прогон ревизии повторять
    некому (DS I3 по #553). `transform(text) -> (new_text, n)`; n == 0 —
    менять нечего, записи нет. Снимок не снялся — отказ, не свободная запись
    (DS M5). После второй неудачи — LostRace. Читаем ВСЕГДА строго: файл
    переписывается целиком, и замена нечитаемого байта записала бы «�» в него
    навсегда. Параметра `errors` здесь нет намеренно — именно лояльное чтение
    у вызывающего и было дефектом №263, а параметр документировал бы способ
    вернуть его одной правкой вызова (GLM r2, критика 1). Живёт здесь, рядом с
    write_text: гейт потери обновления один на всех писателей, и цикл повтора
    тоже (критика DS r2 по #553)."""
    for _attempt in (1, 2):
        snap = _snapshot_or_raise(path, what)
        try:
            before = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Файл исчез между снимком и чтением. Это тот же факт «файла нет»,
            # и называть его надо так же: иначе вызывающий обвинит в мёртвых
            # ссылках файл, которого нет (DS r5 Critical 2).
            raise LostRace(path, what, kind=LostRace.GONE) from None
        after, n = transform(before)
        if not n:
            return 0
        if write_text(path, after, expect=snap):
            return n
    raise LostRace(path, what, kind=LostRace.CHANGED)

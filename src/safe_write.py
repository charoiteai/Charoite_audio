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
    """Файл сменился между чтением и записью дважды подряд (или снимок не
    снялся) — запись не сделана, чужая версия осталась. Машинный сигнал
    вызывающему: лог не должен выдавать это за «нечего дописывать» (GLM I2 /
    DS I2 по #553); строки с PREFIX в списках `dropped` — тот же сигнал там,
    где исключение не проходит. `reason` — что именно случилось: «сменились
    под рукой» против «снимок не снят: файла нет или он недоступен» (DS M3 r2)."""

    PREFIX = "запись не состоялась: "

    def __init__(self, path: pathlib.Path, what: str, reason: str = "сменились под рукой"):
        self.path = path
        self.reason = reason          # чтобы вызывающий не разбирал текст сообщения
        super().__init__(f"{self.PREFIX}{path.name} {reason} — {what}")


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
        snap = stat_snapshot(path)
        if snap is None:
            # «Файла нет» и «не дотянулись» — разные факты, и вызывающему
            # важна именно эта разница: в исчезнувшем файле нечего править, а
            # недоступный остаётся как был. Различаем ЗДЕСЬ, в момент отказа:
            # опрос диска потом — второй источник истины и своя гонка, а в
            # обработчике он ещё и сам умеет бросить (DS r4 Critical).
            reason = "файла нет" if not path.exists() else "снимок не снят: файл недоступен"
            raise LostRace(path, what, reason=reason)
        before = path.read_text(encoding="utf-8")
        after, n = transform(before)
        if not n:
            return 0
        if write_text(path, after, expect=snap):
            return n
    raise LostRace(path, what)

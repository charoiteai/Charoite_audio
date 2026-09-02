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

"""Данные встреч закрыты от других учёток машины.

Стенограмма — и есть чувствительные данные продукта. При этом конвейер
писал её с правами по умолчанию (0644), а каталоги оставлял 0755: на Mac
с несколькими учётными записями любой второй пользователь читал чужие
переговоры целиком, не запросив ни одного разрешения (аудит 16.08).

Обещание PRIVACY.md «ничего не покидает вашу машину» ничего не говорит про
границу МЕЖДУ пользователями машины — а для банка или клиники это ровно та
же граница. Здесь она и проверяется.

Тест-сторож, а не косметика: обход маски делается одной строкой в новой
точке входа, и заметить это в ревью нечем.
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from charoite_paths import (  # noqa: E402
    DATA_UMASK,
    PRIVATE_DIRS,
    harden_existing,
    harden_umask,
    secure_dir,
)
import layout_map  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Где точки входа сторожатся: код продукта и скрипты. Сами точки — из
#: инвентаря `layout_map` (python с гвардом `__main__`), а не рукописным
#: списком: список отставал — шесть писателей с маской и `meeting_archive`
#: без неё в него так и не попали (№385).
ENTRY_AREAS = ("src/", "scripts/")

#: Точки входа, которым маска не нужна: не пишут данных встреч и владельца.
#: Каждая — с причиной одной строкой. Точка вне этого списка обязана закрыть
#: маску первым делом; новая точка без классификации краснит сторож.
UMASK_FORGIVEN = {
    "scripts/bench_models.py": "не пишет данных встреч и владельца: замер скорости моделей, вывод в stdout",
    "scripts/check_private_markers.py": "не пишет данных встреч и владельца: читает дерево репозитория",
    "scripts/check_test_assertions.py": "не пишет данных встреч и владельца: читает тесты репозитория",
    "scripts/diar_bench.py": "не пишет данных встреч и владельца: синтетический диалог голосом say",
    "scripts/doctor.py": "не пишет данных встреч и владельца: диагностика, вывод в stdout",
    "scripts/get_models.py": "не пишет данных встреч и владельца: качает веса моделей",
    "scripts/layout_map.py": "не пишет данных встреч и владельца: артефакт и карта раскладки кода",
    "scripts/lock_runtime_deps.py": "не пишет данных встреч и владельца: замок зависимостей репозитория",
    "scripts/mutate_check.py": "не пишет данных встреч и владельца: мутанты кода во временной копии",
    "scripts/sign_release_manifest.py": "не пишет данных встреч и владельца: подпись манифеста релиза",
    "scripts/stt_bench.py": "не пишет данных встреч и владельца: синтетические фразы голосом say",
    "scripts/wait_for_idle.py": "не пишет данных встреч и владельца: только ждёт освобождения машины",
    "src/dictate.py": "не пишет данных встреч и владельца: текст диктовки уходит родителю в stdout",
}

#: Что может идти в блоке `__main__` раньше маски: называние корня данных. Оно
#: только читает окружение и при беде выходит с рецептом — файлов не создаёт.
ROOT_DOOR = "name_data_root_or_exit"

#: Обёртки над `main()`, после которых путь исполнения всё равно идёт в `main`.
EXIT_WRAPPERS = ("exit", "SystemExit")


def _call_named(node: ast.AST | None, name: str) -> bool:
    """`name(...)` или `x.name(...)` — вызов по имени или атрибутом."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == name) or (isinstance(f, ast.Attribute) and f.attr == name)


def _is_harden(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and _call_named(stmt.value, "harden_umask")


def _is_prologue(stmt: ast.stmt) -> bool:
    """Импорт или `[root =] name_data_root_or_exit(...)` — ничего не пишут."""
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return True
    return isinstance(stmt, (ast.Expr, ast.Assign)) and _call_named(stmt.value, ROOT_DOOR)


def _calls_main(stmt: ast.stmt) -> bool:
    """`main(...)`, `sys.exit(main(...))`, `raise SystemExit(main(...))`."""
    expr = stmt.exc if isinstance(stmt, ast.Raise) else stmt.value if isinstance(stmt, ast.Expr) else None
    if isinstance(expr, ast.Call) and any(_call_named(expr, w) for w in EXIT_WRAPPERS) and len(expr.args) == 1:
        expr = expr.args[0]
    return isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "main"


def umask_verdict(tree: ast.Module) -> str | None:
    """Почему точка входа не закрывает маску первым делом; `None` — закрывает.

    Идём по пути исполнения, а не ищем текст: от блока `__main__` до
    `harden_umask()` допустимы только импорты и называние корня; вызов `main()`
    — спуск в её тело, где маска обязана быть первым оператором (докстринг не
    в счёт). Прежний сторож искал вызов в первых 400 знаках после якоря:
    длинный комментарий над вызовом краснил его зря, а вызов во вложенной
    функции или запись до `main()` в эти знаки помещались (№385)."""
    guard = layout_map.main_guard(tree)
    if guard is None:
        return "нет блока __main__"
    for stmt in guard.body:
        if _is_harden(stmt):
            return None
        if _is_prologue(stmt):
            continue
        if not _calls_main(stmt):
            return f"строка {stmt.lineno}: «{ast.unparse(stmt)[:60]}» исполняется раньше harden_umask()"
        main = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
        if main is None:
            return f"строка {stmt.lineno}: main() зовётся, но на верхнем уровне модуля не определена"
        body = main.body
        if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            body = body[1:]
        if body and _is_harden(body[0]):
            return None
        where = body[0].lineno if body else main.lineno
        return f"строка {where}: первый оператор main() — не harden_umask()"
    return "блок __main__ не зовёт ни harden_umask(), ни main()"


def umask_problems(entries: dict[str, ast.Module], forgiven: dict[str, str]) -> list[str]:
    """Расхождения сторожа маски строками: точка без маски и без прощения,
    прощение без причины и прощение точки, которой больше нет."""
    out = [f"{rel}: {why} — закрыть маску первым делом или простить с причиной в UMASK_FORGIVEN"
           for rel, tree in sorted(entries.items())
           if rel not in forgiven and (why := umask_verdict(tree)) is not None]
    out += [f"UMASK_FORGIVEN прощает {rel} без причины" for rel, why in sorted(forgiven.items())
            if not why.strip()]
    out += [f"UMASK_FORGIVEN прощает {rel}, а такой точки входа нет — снять"
            for rel in sorted(set(forgiven) - set(entries))]
    return out


def entry_points() -> dict[str, ast.Module]:
    """Python-точки входа продукта и скриптов — по инвентарю `layout_map`."""
    inv = layout_map.inventory(ROOT)
    return {rel: info.tree for rel, info in inv.files.items()
            if info.executable == layout_map.PY_ENTRY and info.tree is not None
            and rel.startswith(ENTRY_AREAS)}

@pytest.fixture
def keep_umask():
    """Маска — глобальное состояние процесса: вернуть как было."""
    prev = os.umask(0o022)
    yield
    os.umask(prev)


def test_маска_закрывает_группу_и_остальных(keep_umask):
    assert DATA_UMASK == 0o077, "маска должна убирать все права кроме владельца"
    prev = harden_umask()
    assert os.umask(prev) == DATA_UMASK, "harden_umask обязан поставить маску"


def test_файл_созданный_под_маской_читает_только_владелец(tmp_path, keep_umask):
    harden_umask()
    f = tmp_path / "2026-08-16_1200_Встреча.md"
    f.write_text("кто что решил", encoding="utf-8")
    assert f.stat().st_mode & 0o777 == 0o600, (
        "стенограмма доступна другим пользователям машины")


def test_secure_dir_закрывает_и_уже_существующий_каталог(tmp_path):
    d = tmp_path / "transcripts"
    d.mkdir(mode=0o755)
    assert d.stat().st_mode & 0o777 == 0o755  # как у установок до правки
    secure_dir(d)
    assert d.stat().st_mode & 0o777 == 0o700


def test_миграция_чинит_старые_данные(tmp_path):
    # установка, пожившая со старой маской
    for name in PRIVATE_DIRS:
        d = tmp_path / name
        d.mkdir(mode=0o755)
        (d / "старое.md").write_text("х", encoding="utf-8")
        (d / "старое.md").chmod(0o644)
    (tmp_path / "transcripts" / "вложенная").mkdir(mode=0o755)

    fixed = harden_existing(tmp_path)

    assert fixed > 0
    for name in PRIVATE_DIRS:
        d = tmp_path / name
        assert d.stat().st_mode & 0o777 == 0o700, f"{name} открыт другим учёткам"
        assert (d / "старое.md").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "transcripts" / "вложенная").stat().st_mode & 0o777 == 0o700


def test_миграция_не_падает_на_чужом_файле(tmp_path):
    """Чужой файл в каталоге данных не должен ронять запуск: встреча важнее."""
    d = tmp_path / "transcripts"
    d.mkdir()
    (d / "ок.md").write_text("х", encoding="utf-8")
    harden_existing(tmp_path)  # не бросает
    assert (d / "ок.md").stat().st_mode & 0o777 == 0o600


def test_каждая_точка_входа_закрывает_маску():
    """Новый скрипт, пишущий данные встреч, обязан закрыть маску первым делом.

    Без этого сторожа дыра возвращается тихо: код работает, тесты зелёные,
    а файлы снова 0644.
    """
    entries = entry_points()
    # выборка не пуста и держит известных писателей: иначе сторож зелен ни о чём
    assert {"src/daemon.py", "src/mcp_server.py", "src/meeting_archive.py"} <= set(entries)
    assert umask_problems(entries, UMASK_FORGIVEN) == []


def _tree(src: str) -> ast.Module:
    return ast.parse(textwrap.dedent(src))


@pytest.mark.parametrize("src", [
    # первым после длинного комментария — прежний сторож тут краснел зря
    """
    def main():
        \"\"\"Докстринг не в счёт.\"\"\"
        """ + "# " + "очень длинное объяснение, почему маска первой; " * 12 + """
        harden_umask()
        write()

    if __name__ == "__main__":
        from charoite_paths import name_data_root_or_exit
        name_data_root_or_exit(__file__)
        sys.exit(main())
    """,
    # атрибутом, в голом блоке после называния корня присваиванием
    """
    if __name__ == "__main__":
        import charoite_paths
        root = charoite_paths.name_data_root_or_exit(__file__)
        charoite_paths.harden_umask()
        write(root)
    """,
    """
    def main():
        harden_umask()

    if __name__ == "__main__":
        raise SystemExit(main())
    """,
    """
    def main(argv):
        harden_umask()

    if __name__ == "__main__":
        main(sys.argv)
    """,
])
def test_сторож_маски_пропускает_маску_первым_делом(src):
    assert umask_verdict(_tree(src)) is None


@pytest.mark.parametrize("src, said", [
    # вторым оператором: разбор аргументов успевает раньше маски
    ("""
    def main():
        ap = argparse.ArgumentParser()
        harden_umask()

    if __name__ == "__main__":
        main()
    """, "первый оператор main()"),
    # во вложенной функции: маска поставится, только если её позовут
    ("""
    def main():
        def inner():
            harden_umask()
        inner()

    if __name__ == "__main__":
        main()
    """, "первый оператор main()"),
    # под условием — тоже не первым делом
    ("""
    def main():
        if flag:
            harden_umask()

    if __name__ == "__main__":
        main()
    """, "первый оператор main()"),
    # запись в прологе __main__ до main(): файл создан со старой маской
    ("""
    def main():
        harden_umask()

    if __name__ == "__main__":
        from charoite_paths import name_data_root_or_exit
        name_data_root_or_exit(__file__)
        pathlib.Path("x.md").write_text("кто что решил")
        main()
    """, "исполняется раньше harden_umask()"),
    # чужой вызов, завёрнутый в exit, — не main
    ("""
    def main():
        harden_umask()

    if __name__ == "__main__":
        sys.exit(other())
    """, "исполняется раньше harden_umask()"),
    ("""
    if __name__ == "__main__":
        main()
    """, "не определена"),
    ("""
    def main():
        harden_umask()

    if __name__ == "__main__":
        import sys
    """, "ни harden_umask(), ни main()"),
    ("""
    def main():
        harden_umask()
    """, "нет блока __main__"),
])
def test_сторож_маски_краснеет_на_пути_мимо_маски(src, said):
    verdict = umask_verdict(_tree(src))
    assert verdict is not None and said in verdict, verdict


def test_неклассифицированная_точка_краснит_сторож():
    bare = _tree("""
    def main():
        write()

    if __name__ == "__main__":
        main()
    """)
    said = umask_problems({"scripts/новая_точка.py": bare}, {})
    assert len(said) == 1 and said[0].startswith("scripts/новая_точка.py: строка 3"), said
    # прощение с причиной снимает её; пустая причина — нет
    assert umask_problems({"scripts/новая_точка.py": bare}, {"scripts/новая_точка.py": "не пишет: x"}) == []
    assert umask_problems({"scripts/новая_точка.py": bare}, {"scripts/новая_точка.py": " "}) == [
        "UMASK_FORGIVEN прощает scripts/новая_точка.py без причины"]
    # прощение ушедшей точки — расхождение, а точка с маской прощения не требует
    good = _tree("""
    if __name__ == "__main__":
        harden_umask()
    """)
    assert umask_problems({"src/a.py": good}, {"src/ушла.py": "не пишет: y"}) == [
        "UMASK_FORGIVEN прощает src/ушла.py, а такой точки входа нет — снять"]


def test_точки_входа_берутся_из_инвентаря_по_области(tmp_path, monkeypatch):
    """Выборка — метка инвентаря и область, а не рукописный список: новая точка
    в `scripts/` попадает под сторож сама, тест и библиотека — нет."""
    for rel, src in {"scripts/новая.py": "if __name__ == '__main__':\n    pass\n",
                     "src/библиотека.py": "X = 1\n",
                     "tests/test_x.py": "if __name__ == '__main__':\n    pass\n"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(src, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    got = entry_points()
    assert set(got) == {"scripts/новая.py"}
    assert umask_problems(got, {}) == [
        "scripts/новая.py: строка 2: «pass» исполняется раньше harden_umask() — закрыть маску "
        "первым делом или простить с причиной в UMASK_FORGIVEN"]

def test_рубильник_запрещает_докачку_весов(monkeypatch):
    """CHAROITE_NO_CLOUD обязан перекрывать и ленивую загрузку моделей.

    «Ничего не покидает машину» до этого не касалось весов STT: с пустым
    кэшем демон уходил на huggingface.co посреди встречи (аудит 16.08).
    """
    import privacy

    env: dict[str, str] = {}
    privacy.enforce_offline_downloads(env)
    assert env == {}, "без рубильника ничего навязывать не должны"

    env = {"CHAROITE_NO_CLOUD": "1"}
    privacy.enforce_offline_downloads(env)
    assert env.get("HF_HUB_OFFLINE") == "1"
    assert env.get("TRANSFORMERS_OFFLINE") == "1"

    # исторический алиас рубильника работает так же
    env = {"SUFLER_NO_CLOUD": "1"}
    privacy.enforce_offline_downloads(env)
    assert env.get("HF_HUB_OFFLINE") == "1"

    # STT обязан звать это до загрузки модели
    text = (ROOT / "src/stt.py").read_text(encoding="utf-8")
    head = text.split("def __init__", 1)[1][:600]
    assert "enforce_offline_downloads" in head, (
        "STT снова качает веса мимо рубильника")


def test_рубильник_перебивает_заранее_выставленный_ноль():
    """`HF_HUB_OFFLINE=0` в окружении (профиль терминала, родительский
    скрипт) переживал рубильник: `setdefault` уважал чужое значение, и
    библиотека уходила в сеть (второе мнение по #324, 16.08). Рубильник —
    последнее слово."""
    import privacy

    env = {"CHAROITE_NO_CLOUD": "1", "HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}
    privacy.enforce_offline_downloads(env)
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"


def test_swift_честно_описывает_расхождение_с_питоном():
    """0.0.0.0 Swift разрешает, python — нет, и это осознанно.

    Как адрес назначения он ведёт на эту же машину: запрос никуда не
    уходит, приватность не страдает (тест RemoteHostPolicyTests фиксирует
    решение). Дырой был комментарий, обещавший дословное повторение
    `privacy._is_loopback`, — расхождение теперь названо вслух.
    """
    swift = (ROOT / "app/Sources/CharoiteApp/Models/AppSettings.swift"
             ).read_text(encoding="utf-8")
    head = swift.split("static func isLoopbackHost", 1)[0][-900:]
    assert "Повторяет `privacy._is_loopback`" not in head, (
        "комментарий снова обещает дословное повторение питона")
    assert "0.0.0.0" in head, "расхождение должно быть названо в комментарии"


def test_swift_создаёт_данные_закрытыми():
    """Swift-сторона пишет те же данные и обязана держать те же права."""
    helper = ROOT / "app/Sources/CharoiteApp/Models/PrivateFiles.swift"
    assert helper.exists(), "удалён общий помощник приватных прав"
    text = helper.read_text(encoding="utf-8")
    assert "0o600" in text and "0o700" in text

    # сырой звук встречи — самый чувствительный файл, проверяем поимённо
    capture = (ROOT / "app/Sources/CharoiteApp/Services/SystemAudioCapture.swift"
               ).read_text(encoding="utf-8")
    assert "createPrivateFile" in capture, "PCM встречи снова создаётся с 0644"
    assert "createPrivateDirectory" in capture

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

import os
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from charoite_paths import (  # noqa: E402
    DATA_UMASK,
    PRIVATE_DIRS,
    harden_existing,
    harden_umask,
    secure_dir,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Точки входа, которые пишут данные человека и обязаны закрывать маску.
WRITERS = (
    "src/daemon.py",
    "src/main.py",
    "src/rebuild_transcript.py",
    "src/graph_updater.py",
    "src/dictate_note.py",
    "src/transcribe_file.py",
)


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


@pytest.mark.parametrize("path", WRITERS)
def test_каждая_точка_входа_закрывает_маску(path):
    """Новый скрипт, пишущий стенограммы, обязан позвать harden_umask.

    Без этого сторожа дыра возвращается тихо: код работает, тесты зелёные,
    а файлы снова 0644.
    """
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "harden_umask" in text, f"{path} не закрывает маску прав"
    body = text.split("def main(", 1)
    assert len(body) == 2, f"{path}: нет main() — сторож нуждается в правке"
    head = body[1][:400]
    assert re.search(r"harden_umask\(\)", head), (
        f"{path}: harden_umask должен вызываться в начале main(), "
        "иначе часть файлов успевает создаться со старой маской")


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

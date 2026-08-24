"""Статусы тумблеров: стартовые синхронизации молчат, живые переключения — нет.

Механизм ломали и чинили дважды за два круга по PR #394 (сначала спец-случай
theses, потом metка quiet), а регресс-теста не было: откат «len(parts) in
(3, 4)» на «== 3» или потеря « quiet» в одной из Swift-строк молча вернули бы
статус-спам «⚙️ … выключены», затирающий «Запись прервалась — восстанавливаю»
при каждом авто-рестарте демона (Sonnet, medium-прогон 24.08). Тест — по
исходникам, как test_expand_protocol.py: контракт двух сторон в строках.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAEMON = (ROOT / "src" / "daemon.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services" /
           "SuflerService.swift").read_text(encoding="utf-8")


def test_daemon_accepts_quiet_and_silences_it():
    # Четвёртое слово — только quiet; без метки — живое переключение со статусом.
    assert 'len(parts) == 4 and parts[3] == "quiet"' in DAEMON
    assert "if changed and not quiet_sync" in DAEMON


def test_app_marks_startup_syncs_quiet():
    # Обе стартовые отправки — с меткой: сохранённые дефолты и безусловный theses.
    assert 'send("set \\(key) off quiet")' in SERVICE
    assert 'send("set theses off quiet")' in SERVICE


def test_live_toggle_stays_loud():
    # Живой клик по чипу идёт БЕЗ метки — статус обязан прийти.
    assert 'send("set \\(key) \\(on ? "on" : "off")")' in SERVICE

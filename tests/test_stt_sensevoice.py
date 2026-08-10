"""Китайский путь распознавания: SenseVoice вместо мультиязычного Whisper.

GigaAM закрывает русский, Parakeet — английский, а китайские встречи шли на
whisper-large-v3-turbo: универсал, который на китайском уступает
специализированным моделям. SenseVoice Small работает через тот же
sherpa-onnx, что уже стоит ради диаризации, — новой зависимости не появляется.

Здесь закреплено то, что можно проверить без модели на 228 МБ: выбор бэкенда,
понятная ошибка вместо трейсбека и то, что путь к модели берётся из конфига.
Качество распознавания моделью так не проверишь — для этого нужен звук; замер
на синтезированном китайском лежит в scripts/stt_bench.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import stt as stt_mod  # noqa: E402


def _cfg(backend: str, **stt_extra) -> dict:
    return {
        "stt": {"backend": backend, "language": "zh", **stt_extra},
        "sufler": {},
    }


def test_unknown_backend_names_the_valid_ones():
    """Опечатка в конфиге должна отвечать списком, а не трейсбеком."""
    with pytest.raises(ValueError) as e:
        stt_mod.STT(_cfg("sensevoise"))          # опечатка нарочно
    assert "sensevoice" in str(e.value), "в подсказке нет нового бэкенда"
    assert "gigaam" in str(e.value) and "whisper" in str(e.value)


def test_missing_model_explains_how_to_install(tmp_path):
    """Модель в поставку не входит — ошибка обязана давать команду установки."""
    with pytest.raises(FileNotFoundError) as e:
        stt_mod.STT(_cfg("sensevoice", sensevoice_model=str(tmp_path / "нет.onnx")))
    msg = str(e.value)
    assert "get_models.py --stt sensevoice" in msg, "нет команды установки"


def test_model_path_comes_from_config(tmp_path, monkeypatch):
    """Путь берётся из конфига; относительный — от рабочего корня."""
    # Кладём заглушку: до загрузки модели дело дойти не должно — проверяем,
    # что искали именно там, куда указали.
    model = tmp_path / "custom" / "sv.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"\x08fake")
    with pytest.raises(FileNotFoundError) as e:
        stt_mod.STT(_cfg("sensevoice", sensevoice_model=str(model)))
    # Модель есть, а словаря рядом нет — жалуемся на файл, который указали.
    assert str(model) in str(e.value)


def test_relative_path_is_resolved_against_the_working_root(monkeypatch, tmp_path):
    """`models/stt/…` в конфиге — это папка данных, а не текущий каталог."""
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    import importlib

    import charoite_paths
    importlib.reload(charoite_paths)
    with pytest.raises(FileNotFoundError) as e:
        stt_mod.STT(_cfg("sensevoice", sensevoice_model="models/stt/sensevoice.onnx"))
    assert str(tmp_path) in str(e.value), "относительный путь ушёл мимо CHAROITE_ROOT"


def test_doctor_warns_when_the_chosen_backend_has_no_model(tmp_path, monkeypatch, capsys):
    """Выбор бэкенда не должен упираться в ошибку демона на старте встречи.

    gigaam, parakeet и whisper тянут веса сами; SenseVoice ставится отдельной
    командой — значит доктор обязан сказать об этом заранее.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib

    import doctor
    importlib.reload(doctor)
    monkeypatch.setattr(doctor, "ROOT", tmp_path)

    doctor.check_stt({"stt": {"backend": "sensevoice"}})
    out = capsys.readouterr().out
    assert "get_models.py --stt sensevoice" in out, "нет команды установки"

    # Модель на месте — доктор молчит про проблему.
    model = tmp_path / "models" / "stt" / "sensevoice.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"\x08x")
    model.with_name("tokens.txt").write_text("t")
    doctor.check_stt({"stt": {"backend": "sensevoice"}})
    assert "SenseVoice" in capsys.readouterr().out

    # Другой бэкенд — проверка не вмешивается.
    doctor.check_stt({"stt": {"backend": "gigaam"}})
    assert capsys.readouterr().out == ""

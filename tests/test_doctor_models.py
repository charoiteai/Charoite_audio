"""Доктор: набор голосов поставлен только когда на месте оба файла.

Одни эмбеддинги раньше давали «✓»: без сегментации пересборка после встречи
голоса не размечает, а сигнала не было. Здесь — точный текст строки.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import charoite_paths  # noqa: E402
import doctor  # noqa: E402

_RECIPE = ".venv/bin/python scripts/get_models.py --diar"
_OK = " ✓ диаризация: models/diar/embedding.onnx, models/diar/segmentation.onnx\n"
_EMB_ONLY = (
    " – диаризация: нет models/diar/segmentation.onnx — "
    "голоса после встречи не размечаются заново\n"
    f"     → {_RECIPE}\n"
)
_SEG_ONLY = (
    " – диаризация: нет models/diar/embedding.onnx\n"
    f"     → {_RECIPE}\n"
)
_NONE = (
    " – диаризации нет (метки «Собеседник N» будут по каналам): "
    "нет models/diar/embedding.onnx и models/diar/segmentation.onnx\n"
    f"     → {_RECIPE}\n"
)


def _folder(tmp_path: Path) -> Path:
    root = charoite_paths.use_data_root(tmp_path, replace=True)
    folder = root / "models" / "diar"
    folder.mkdir(parents=True)
    return folder


def _see(capsys) -> str:
    doctor.issues = 0
    doctor.check_models()
    out = capsys.readouterr().out
    assert doctor.issues == 0, out
    return out


def test_both_voice_files_are_ok(capsys, tmp_path):
    folder = _folder(tmp_path)
    (folder / "embedding.onnx").write_bytes(b"")
    (folder / "segmentation.onnx").write_bytes(b"")
    assert _see(capsys) == _OK


def test_embeddings_without_segmentation_warn_about_the_loss(capsys, tmp_path):
    folder = _folder(tmp_path)
    (folder / "embedding.onnx").write_bytes(b"")
    out = _see(capsys)
    assert out == _EMB_ONLY
    assert "segmentation.onnx" in out


def test_no_voice_files_is_a_warning_naming_both(capsys, tmp_path):
    _folder(tmp_path)
    assert _see(capsys) == _NONE


def test_segmentation_without_embeddings_names_the_missing_file(capsys, tmp_path):
    folder = _folder(tmp_path)
    (folder / "segmentation.onnx").write_bytes(b"")
    out = _see(capsys)
    assert out == _SEG_ONLY
    assert "нет models/diar/embedding.onnx" in out

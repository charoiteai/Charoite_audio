"""Контракт fallback-загрузчика конфигурации (партия D-П7)."""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config_loader import load_user_or_example  # noqa: E402


MIGRATED = (
    "src/diarize.py",
    "src/dictate.py",
    "src/dictate_note.py",
    "src/transcribe_file.py",
    "src/retro_fill.py",
    "src/meeting_archive.py",
    "scripts/import_meeting.py",
    "scripts/nightly_claude_cores.py",
    "scripts/nightly_dossier.py",
    "scripts/nightly_dossier_review.py",
)


def _config_dir(root: pathlib.Path) -> pathlib.Path:
    path = root / "config"
    path.mkdir(parents=True)
    return path


def _две_папки(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Корень данных и корень кода — разные каталоги, как на установке."""
    данные, код = tmp_path / "данные", tmp_path / "код"
    _config_dir(данные)
    _config_dir(код)
    return данные, код


def test_user_config_wins_over_example(tmp_path):
    config = _config_dir(tmp_path)
    (config / "config.yaml").write_text("source: user\n", encoding="utf-8")
    (config / "config.example.yaml").write_text("source: example\n", encoding="utf-8")

    assert load_user_or_example(tmp_path) == {"source": "user"}


def test_missing_user_config_reads_example_from_the_code_root(tmp_path):
    """Конфиг владельца — из корня ДАННЫХ, образец — из корня КОДА.

    Образец приезжает с поставкой и в папке данных его не бывает вовсе.
    Пока корни были одним деревом, разницы не было видно; после разделения
    поиск образца в папке владельца ронял модуль на импорте
    (круг 18 по коду №327).
    """
    данные, код = _две_папки(tmp_path)
    (код / "config" / "config.example.yaml").write_text("source: пример\n", encoding="utf-8")

    assert load_user_or_example(данные, code=код) == {"source": "пример"}


def test_user_config_is_read_from_the_data_root_not_the_code_root(tmp_path):
    """Свой конфиг владельца сильнее образца поставки — и лежит в своём корне."""
    данные, код = _две_папки(tmp_path)
    (данные / "config" / "config.yaml").write_text("source: владелец\n", encoding="utf-8")
    (код / "config" / "config.example.yaml").write_text("source: пример\n", encoding="utf-8")

    assert load_user_or_example(данные, code=код) == {"source": "владелец"}


def test_empty_config_stays_none(tmp_path):
    config = _config_dir(tmp_path)
    (config / "config.yaml").write_text("", encoding="utf-8")
    (config / "config.example.yaml").write_text("source: example\n", encoding="utf-8")

    assert load_user_or_example(tmp_path) is None


def test_broken_user_config_does_not_fall_back(tmp_path):
    config = _config_dir(tmp_path)
    (config / "config.yaml").write_text("[broken", encoding="utf-8")
    (config / "config.example.yaml").write_text("source: example\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        load_user_or_example(tmp_path)


def test_missing_both_reports_example_path(tmp_path):
    """Нет ни конфига, ни образца — в ошибке назван путь образца В КОРНЕ КОДА."""
    данные, код = _две_папки(tmp_path)

    with pytest.raises(FileNotFoundError) as error:
        load_user_or_example(данные, code=код)

    assert error.value.filename == str(код / "config" / "config.example.yaml")


def test_code_root_defaults_to_the_installed_code(tmp_path):
    """Без явного `code` образец ищется там, где лежит сам загрузчик.

    Вызывающие передают только корень данных, поэтому дефолт — не украшение,
    а боевой путь: он и должен указывать на поставку, а не на папку владельца.
    """
    import charoite_paths
    данные = tmp_path / "данные"
    _config_dir(данные)                      # папка есть, config.yaml в ней нет

    assert load_user_or_example(данные) == yaml.safe_load(
        (charoite_paths.CODE_ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))


def test_existing_unreadable_path_does_not_fall_back(tmp_path):
    config = _config_dir(tmp_path)
    (config / "config.yaml").mkdir()
    (config / "config.example.yaml").write_text("source: example\n", encoding="utf-8")

    with pytest.raises(IsADirectoryError):
        load_user_or_example(tmp_path)


def test_all_migrated_call_sites_use_the_shared_loader():
    """Анти-дубль: десять прежних копий не должны вырасти снова."""
    for relative in MIGRATED:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "load_user_or_example" in source, relative
        assert "config.example.yaml" not in source, relative


def test_nightly_empty_config_policy_stays_at_call_sites():
    """`or {}` принадлежит двум ночным владельцам, не общему загрузчику."""
    for relative, function_name in (
        ("scripts/nightly_dossier.py", "cfg"),
        ("scripts/nightly_dossier_review.py", "_cfg"),
    ):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
        assert len(returns) == 1, relative
        assert isinstance(returns[0].value, ast.BoolOp), relative
        assert isinstance(returns[0].value.op, ast.Or), relative


def test_import_meeting_installs_the_recipe_hook_first():
    """deps.explain_missing обязан встать ДО импорта config_loader: тот
    тянет yaml, и свежий клон без .venv должен получить рецепт, а не
    трейсбек (круг-1 по #418, все три головы; порядок пиннится по
    тексту — простая перестановка строк ломала CLI при зелёных тестах)."""
    src = (ROOT / "scripts" / "import_meeting.py").read_text(encoding="utf-8")
    hook = src.index("deps.explain_missing()")
    loader = src.index("from config_loader import")
    assert hook < loader


def test_meeting_archive_imports_without_third_party():
    """meeting_archive обязан импортироваться на голом python3:
    morning_brief берёт из него только чистые функции (круг-1, GLM)."""
    import ast as _ast
    tree = _ast.parse((ROOT / "src" / "meeting_archive.py").read_text(encoding="utf-8"))
    top = {n.names[0].name.split(".")[0]
           for n in tree.body if isinstance(n, _ast.Import)} |           {(n.module or "").split(".")[0]
           for n in tree.body if isinstance(n, _ast.ImportFrom)}
    assert "yaml" not in top and "config_loader" not in top

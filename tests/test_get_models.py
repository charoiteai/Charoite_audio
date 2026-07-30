"""Диаризация из README не работает из коробки — модели просто нет.

`README` называет «Собеседник 1/2/…» второй фичей продукта, а `src/daemon.py`
включает трекер голосов только при наличии `models/diar/embedding.onnx`. Этот
файл не входит в поставку и до сих пор нигде не скачивался: STT-модель тянется
сама при первом запуске, а эмбеддер предлагалось найти в проекте 3D-Speaker и
«экспортировать/скачать как ONNX» — то есть человеку, который хочет метки по
голосам, выдавали ссылку на исследовательский репозиторий.

Отсюда `scripts/get_models.py`. Требования, которые держит этот файл:

    1. Скачивание — только по явной команде и с показанным URL. Это
       единственный сетевой вызов в продукте, кроме опционального облака, и
       он не смеет случиться сам по себе.
    2. Проверка модели работает БЕЗ сети: «этот файл подойдёт» — вопрос,
       который должен отвечаться на месте.
    3. Мусор вместо модели опознаётся до того, как демон встретит его на
       живой встрече.
    4. Ответ «чего не хватает» всегда содержит команду, которой это чинится.
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import get_models  # noqa: E402


def test_every_known_model_is_described_and_https():
    """Список моделей — часть документации: откуда, сколько весит, для чего."""
    assert get_models.MODELS, "список моделей пуст"
    for key, m in get_models.MODELS.items():
        assert m.url.startswith("https://"), f"{key}: не https"
        assert m.size_mb > 0, f"{key}: не указан размер"
        assert m.note, f"{key}: не сказано, чем эта модель отличается"
        assert m.source.startswith("https://"), f"{key}: не указан upstream-источник"
    assert get_models.DEFAULT in get_models.MODELS


def test_target_path_is_the_one_the_daemon_looks_at():
    """Путь должен совпадать с тем, что читает демон, иначе скачали в пустоту."""
    assert get_models.diar_target(REPO) == REPO / "models" / "diar" / "embedding.onnx"
    daemon = (REPO / "src" / "daemon.py").read_text(encoding="utf-8")
    assert '"diar" / "embedding.onnx"' in daemon, \
        "демон ищет модель по другому пути — сверьте diar_target()"


def test_missing_model_is_explained_with_a_command(tmp_path):
    problem = get_models.check(tmp_path / "нет.onnx")
    assert problem, "отсутствующая модель должна быть проблемой"
    assert "get_models.py" in problem, "в ответе нет команды, которой это чинится"


def test_garbage_is_not_accepted_as_a_model(tmp_path):
    """Скачали HTML страницы логина вместо модели — это должно быть видно."""
    fake = tmp_path / "embedding.onnx"
    fake.write_text("<!DOCTYPE html><html>login</html>", encoding="utf-8")
    problem = get_models.check(fake)
    assert problem and "onnx" in problem.lower(), problem


def test_truncated_download_is_not_accepted(tmp_path):
    """Обрыв связи на середине не должен оставить «модель», которую примут."""
    stub = tmp_path / "embedding.onnx"
    stub.write_bytes(b"\x08\x07")     # ONNX-магия и больше ничего
    problem = get_models.check(stub)
    assert problem, "двухбайтовый файл принят за модель эмбеддингов"


def test_check_mode_does_not_touch_the_network(monkeypatch, tmp_path):
    """`--check` отвечает на месте: ни одного обращения к сети."""
    def explode(*a, **kw):      # noqa: ANN002, ANN003
        raise AssertionError("проверка полезла в сеть")

    monkeypatch.setattr(get_models.urllib.request, "urlopen", explode)
    get_models.check(tmp_path / "нет.onnx")


def test_cli_check_exits_nonzero_and_prints_the_recipe():
    """Запуск как пользователь: без модели — код 1 и внятный рецепт."""
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "get_models.py"), "--diar", "--check",
         "--dest", "/nonexistent/embedding.onnx"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "get_models.py" in out and "--diar" in out


def test_cli_lists_models_without_downloading_anything():
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "get_models.py"), "--list"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    for key in get_models.MODELS:
        assert key in r.stdout, f"{key} не показан в списке"
    assert "3D-Speaker" in r.stdout, "не сказано, чей это upstream"


def test_doctor_points_at_the_command():
    """Диагностика обязана предлагать команду, а не ссылку на документацию."""
    doctor = (REPO / "scripts" / "doctor.py").read_text(encoding="utf-8")
    assert "get_models.py" in doctor, \
        "doctor всё ещё отправляет читать docs вместо одной команды"

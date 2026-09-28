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
import os
import pathlib
import subprocess
import sys
import textwrap

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


def test_resume_restarts_when_the_server_ignores_range(monkeypatch, tmp_path):
    """Докачка законна только на 206. Сервер, не знающий Range, отдаёт 200 и
    файл целиком — дописанный к хвосту .part он удваивал файл; по размеру
    тот проходил, а при своём зеркале (--url, без суммы) битым и оставался
    (аудит DeepSeek 16.08)."""
    import io

    body = b"x" * 3000
    dest = tmp_path / "tokens.txt"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(body[:1000])         # оборванная прошлая закачка

    class FakeResponse(io.BytesIO):
        status = 200                       # Range проигнорирован
        headers = {"Content-Length": str(len(body))}

        def __enter__(self):
            return self

        def __exit__(self, *a):            # noqa: ANN002
            self.close()

    seen: list[str | None] = []

    def fake_urlopen(req, timeout=0):      # noqa: ANN001, ARG001
        seen.append(req.get_header("Range"))
        return FakeResponse(body)

    monkeypatch.setattr(get_models.urllib.request, "urlopen", fake_urlopen)
    get_models.download("https://example.invalid/tokens.txt", dest, expect_mb=1, onnx=False)

    assert seen and seen[0] == "bytes=1000-", "докачка не запросила Range"
    assert dest.read_bytes() == body, "файл удвоен или не докачан"


def test_nemotron_weights_come_from_a_pinned_revision_with_sums(monkeypatch, tmp_path):
    """Два файла, каждый со своей суммой и из закреплённой ревизии (№474): `resolve/main`
    — подвижная цель, сумма поймала бы законное обновление как подмену."""
    calls = []
    monkeypatch.setattr(get_models, "download",
                        lambda url, dest, size, onnx=True, sha256="": calls.append((url, dest, size, onnx, sha256)))
    get_models.fetch_nemotron(tmp_path)
    base = f"https://huggingface.co/mlx-community/Nemotron-3-Diarization/resolve/{get_models.NEMOTRON_REVISION}"
    assert calls == [  # размер — строка «качаю N МБ» перед загрузкой
        (f"{base}/config.json", tmp_path / "config.json", 1, False, get_models.NEMOTRON_CONFIG_SHA256),
        (f"{base}/model.safetensors", tmp_path / "model.safetensors", 190, False, get_models.NEMOTRON.sha256)]
    assert len(get_models.NEMOTRON_REVISION) == 40 and "/main/" not in base


def test_nemotron_weights_on_disk_with_the_right_sum_are_not_fetched_again(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"w")
    sums = {"config.json": get_models.NEMOTRON_CONFIG_SHA256, "model.safetensors": "не та"}
    monkeypatch.setattr(get_models, "_digest", lambda path: sums[path.name])
    calls = []
    monkeypatch.setattr(get_models, "download", lambda url, dest, *a, **k: calls.append(dest.name))
    get_models.fetch_nemotron(tmp_path)
    assert calls == ["model.safetensors"]


def test_the_url_reaches_a_pipe_before_the_connection(tmp_path):
    """В канале (`| tee`, лог) адрес доходит до читателя раньше соединения: сеть здесь — urlopen
    в своём процессе, его подмена пишет отметку мимо буфера Python и обрывает загрузку (№481)."""
    driver = textwrap.dedent(f"""
        import os, sys, urllib.error
        sys.path[:0] = [{str(REPO / "scripts")!r}, {str(REPO / "src")!r}]
        import get_models

        def urlopen(*a, **k):
            os.write(1, b"CONNECT\\n")
            raise urllib.error.URLError("соединение подменено тестом")

        get_models.urllib.request.urlopen = urlopen
        sys.argv = ["get_models.py", "--diar"]
        sys.exit(get_models.main())
    """)
    # PYTHONUNBUFFERED у запускающего нет — как у команды из доков; доли мутатора её выставляют.
    env = {**{k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}, "CHAROITE_ROOT": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True, timeout=120, env=env)
    lines = out.stdout.splitlines()
    url = next(i for i, line in enumerate(lines) if line.strip().startswith("https://"))
    assert url < lines.index("CONNECT"), lines
    assert "не скачалось: <urlopen error соединение подменено тестом>" in out.stderr

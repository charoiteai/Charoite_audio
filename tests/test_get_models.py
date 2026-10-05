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

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import get_models  # noqa: E402
import charoite_paths  # noqa: E402 — src уже в пути: его вставил get_models


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
    # Окружение задаёт тест белым списком: переменные раннера (у долей мутатора — PYTHONUNBUFFERED=1)
    # не просачиваются и не делают вывод небуферизованным с правкой и без неё.
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "CHAROITE_ROOT": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True, timeout=120, env=env)
    lines = out.stdout.splitlines()
    url = next(i for i, line in enumerate(lines) if line.strip().startswith("https://"))
    assert url < lines.index("CONNECT"), lines
    # Отказ эмбеддингов набора ловится (№625): причина — в последней строке stdout, не в stderr.
    assert "не скачалось: <urlopen error соединение подменено тестом>" in lines[-1], lines
    assert lines[-1].startswith("эмбеддинги не поставлены: "), lines
    assert out.returncode == 1


# Набор `--diar` без своего адреса и пути: два файла, точный текст, один выход.
# Суммы и адреса зашиты здесь, а не читаются из констант скрипта: смена пина
# обязана покраснеть, а не переехать вместе с ожиданием.

_EMB_URL = ("https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/"
            "3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx")
_EMB_SHA = "e2d2048292e055f7b61cdec3db010503f35369b245bf0b3bbad021c9a91e4053"
_EN_URL = ("https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/"
           "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx")
_EN_SHA = "c59158379255ad66e161679cca6af8d52d51e389e3224ab7d7a7baae295c2db5"
_SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
_SEG_SHA = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
_LIVE = ("живая диаризация включится при следующем старте встречи "
         "(sufler.live_diarize уже true по умолчанию)")
_SEG_SKIPPED = "сегментация не ставилась — --segmentation"
_SEG_UNCHECKED = "сегментация не проверялась — --check --segmentation"
_RETRY = ".venv/bin/python scripts/get_models.py --diar"
_REFUSAL = ("get_models.py: error: один --url или --dest нельзя делить между "
            "несколькими моделями (--stt, --segmentation, --diar): "
            "один файл лёг бы во все места")
_EMB_BYTES = 5 * 1024 * 1024
_SEG_BYTES = 1024 * 1024


@pytest.fixture
def data_root(tmp_path):
    """Корень данных этого теста. Обвязка уже назвала свой — меняем его."""
    return charoite_paths.use_data_root(tmp_path, replace=True)


def _place_onnx(path: pathlib.Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as out:
        out.write(b"\x08")
        out.truncate(size)


def _missing(path: pathlib.Path) -> str:
    return (f"модели диаризации нет ({path}) — поставить: {_RETRY} "
            "(модели: --list)")


def _record(monkeypatch, fail=None):
    """Сеть подменена: загрузки записываются, соединение не открывается.

    `fail` — адрес, на котором загрузка бросает SystemExit, как настоящий download.
    """
    calls = []

    def fake(url, dest, expect_mb, onnx=True, sha256=""):  # noqa: ANN001, ARG001
        calls.append((url, pathlib.Path(dest), expect_mb, sha256))
        if fail is not None and url == fail:
            raise SystemExit("не скачалось: обрыв\nили укажите своё зеркало через --url")

    def explode(*_a, **_k):
        pytest.fail("проверка или отказ открыли соединение")

    monkeypatch.setattr(get_models, "download", fake)
    monkeypatch.setattr(get_models.urllib.request, "urlopen", explode)
    return calls


def _run(monkeypatch, capsys, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["get_models.py", *argv])
    try:
        code = get_models.main()
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    if code is None:
        code = 0
    return code, captured.out, captured.err


def _targets(root: pathlib.Path):
    emb = root / "models" / "diar" / "embedding.onnx"
    seg = root / "models" / "diar" / "segmentation.onnx"
    assert get_models.diar_target() == emb
    assert get_models.seg_target() == seg
    return emb, seg


def test_diar_with_embeddings_already_there_fetches_only_segmentation(data_root, monkeypatch, capsys):
    """Стоят эмбеддинги, сегментации нет: качается только она, код 0."""
    emb, seg = _targets(data_root)
    _place_onnx(emb, _EMB_BYTES)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar"])
    assert code == 0
    assert err == ""
    assert calls == [(_SEG_URL, seg, 7, _SEG_SHA)]
    assert out == f"модели диаризации нет ({seg})\n"
    assert "нечего делать" not in out


def test_diar_installs_embeddings_before_segmentation(data_root, monkeypatch, capsys):
    """Оба файла отсутствуют: сначала эмбеддинги, затем сегментация, по одному разу."""
    emb, seg = _targets(data_root)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar"])
    assert code == 0
    assert err == ""
    assert calls == [(_EMB_URL, emb, 40, _EMB_SHA), (_SEG_URL, seg, 7, _SEG_SHA)]
    assert out == (f"модели диаризации нет ({emb})\n{_LIVE}\n"
                   f"модели диаризации нет ({seg})\n")


def test_diar_with_segmentation_flag_still_fetches_segmentation_once(data_root, monkeypatch, capsys):
    """`--segmentation --diar` без своего пути ставит сегментацию один раз, не два."""
    emb, seg = _targets(data_root)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--segmentation", "--diar"])
    assert code == 0
    assert err == ""
    assert calls == [(_EMB_URL, emb, 40, _EMB_SHA), (_SEG_URL, seg, 7, _SEG_SHA)]
    assert out == (f"модели диаризации нет ({emb})\n{_LIVE}\n"
                   f"модели диаризации нет ({seg})\n")


def test_diar_keeps_a_segmentation_file_that_is_already_in_place(data_root, monkeypatch, capsys):
    """Сегментация уже стоит: ставится только эмбеддинг, второй загрузки нет."""
    emb, seg = _targets(data_root)
    _place_onnx(seg, _SEG_BYTES)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--segmentation", "--diar"])
    assert code == 0
    assert err == ""
    assert calls == [(_EMB_URL, emb, 40, _EMB_SHA)]
    assert out == (f"модели диаризации нет ({emb})\n{_LIVE}\n"
                   f"модель сегментации уже стоит: {seg}\n")


def test_diar_model_flag_picks_that_embedding(data_root, monkeypatch, capsys):
    """`--model` выбирает эмбеддинги, сегментация остаётся той же."""
    emb, seg = _targets(data_root)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar", "--model", "eres2net-en"])
    assert code == 0
    assert err == ""
    assert calls == [(_EN_URL, emb, 27, _EN_SHA), (_SEG_URL, seg, 7, _SEG_SHA)]
    assert out == (f"модели диаризации нет ({emb})\n{_LIVE}\n"
                   f"модели диаризации нет ({seg})\n")


def test_a_short_segmentation_file_is_fetched_again(data_root, monkeypatch, capsys):
    """Файл сегментации меньше порога — это не «уже стоит»."""
    emb, seg = _targets(data_root)
    _place_onnx(emb, _EMB_BYTES)
    _place_onnx(seg, 100)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar"])
    assert code == 0
    assert err == ""
    assert calls == [(_SEG_URL, seg, 7, _SEG_SHA)]
    assert out == "файл .onnx слишком мал: 100 байт (ждём хотя бы 1 МБ)\n"
    assert "нечего делать" not in out


def test_segmentation_download_failure_keeps_embeddings(data_root, monkeypatch, capsys):
    """Отказ сегментации не откатывает эмбеддинги: код 1, последняя строка — состояние."""
    emb, seg = _targets(data_root)
    calls = _record(monkeypatch, fail=_SEG_URL)

    def writing(url, dest, expect_mb, onnx=True, sha256=""):  # noqa: ANN001, ARG001
        calls.append((url, pathlib.Path(dest), expect_mb, sha256))
        if url == _SEG_URL:
            raise SystemExit("не скачалось: обрыв\nили укажите своё зеркало через --url")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            out.write(b"\x08")
            out.truncate(_EMB_BYTES)

    monkeypatch.setattr(get_models, "download", writing)
    code, out, err = _run(monkeypatch, capsys, ["--diar"])
    status = ("эмбеддинги стоят, сегментации нет: не скачалось: обрыв "
              f"или укажите своё зеркало через --url — повторить: {_RETRY}")
    assert code == 1
    assert err == ""
    assert emb.is_file()
    assert calls == [(_EMB_URL, emb, 40, _EMB_SHA), (_SEG_URL, seg, 7, _SEG_SHA)]
    assert out == (f"модели диаризации нет ({emb})\n{_LIVE}\n"
                   f"модели диаризации нет ({seg})\n{status}\n")
    assert out.splitlines()[-1] == status
    assert "сегментации нет" in out.splitlines()[-1]
    assert _RETRY in out.splitlines()[-1]


_EMB_FAILED = ("эмбеддинги не поставлены: не скачалось: обрыв "
               f"или укажите своё зеркало через --url — повторить: {_RETRY}")


def test_embeddings_download_failure_stops_before_segmentation(data_root, monkeypatch, capsys):
    """Отказ эмбеддингов: код 1, сегментацию не качаем, последняя строка — состояние."""
    emb, seg = _targets(data_root)
    calls = _record(monkeypatch, fail=_EMB_URL)
    code, out, err = _run(monkeypatch, capsys, ["--diar"])
    assert code == 1
    assert err == ""
    assert calls == [(_EMB_URL, emb, 40, _EMB_SHA)]
    assert _LIVE not in out
    assert out == f"модели диаризации нет ({emb})\n{_EMB_FAILED}\n"
    assert out.splitlines()[-1] == _EMB_FAILED


def test_embeddings_download_failure_with_segmentation_present(data_root, monkeypatch, capsys):
    """Сегментация уже стоит, эмбеддинги не скачались: та же последняя строка, одна загрузка."""
    emb, seg = _targets(data_root)
    _place_onnx(seg, _SEG_BYTES)
    calls = _record(monkeypatch, fail=_EMB_URL)
    code, out, err = _run(monkeypatch, capsys, ["--diar"])
    assert code == 1
    assert err == ""
    assert calls == [(_EMB_URL, emb, 40, _EMB_SHA)]
    assert _LIVE not in out
    assert out.splitlines()[-1] == _EMB_FAILED


def test_diar_with_both_files_present_has_nothing_to_do(data_root, monkeypatch, capsys):
    """Оба файла на месте: «нечего делать», загрузок нет, код 0."""
    emb, seg = _targets(data_root)
    _place_onnx(emb, _EMB_BYTES)
    _place_onnx(seg, _SEG_BYTES)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar"])
    assert code == 0
    assert err == ""
    assert calls == []
    assert out == f"модель уже стоит: {emb} и {seg} — нечего делать\n"


def test_check_diar_needs_both_files(data_root, monkeypatch, capsys):
    """`--check --diar`: 1 при одном файле, 0 только при обоих. Сети нет."""
    emb, seg = _targets(data_root)
    calls = _record(monkeypatch)

    _place_onnx(emb, _EMB_BYTES)
    code, out, err = _run(monkeypatch, capsys, ["--diar", "--check"])
    assert code == 1
    assert err == ""
    assert calls == []
    assert out == f"модель на месте: {emb}\n{_missing(seg)}\n"

    seg.unlink(missing_ok=True)
    emb.unlink()
    _place_onnx(seg, _SEG_BYTES)
    code, out, err = _run(monkeypatch, capsys, ["--check", "--diar"])
    assert code == 1
    assert err == ""
    assert out == f"{_missing(emb)}\nмодель сегментации на месте: {seg}\n"

    _place_onnx(emb, _EMB_BYTES)
    code, out, err = _run(monkeypatch, capsys, ["--check", "--diar"])
    assert code == 0
    assert err == ""
    assert out == f"модель на месте: {emb}\nмодель сегментации на месте: {seg}\n"

    emb.unlink()
    seg.unlink()
    code, out, err = _run(monkeypatch, capsys, ["--check", "--diar"])
    assert code == 1
    assert err == ""
    assert out == f"{_missing(emb)}\n{_missing(seg)}\n"
    assert calls == []


def test_check_segmentation_and_diar_fails_without_embeddings(data_root, monkeypatch, capsys):
    """`--check --segmentation --diar` при одной сегментации: код 1, сегментация один раз."""
    emb, seg = _targets(data_root)
    _place_onnx(seg, _SEG_BYTES)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--check", "--segmentation", "--diar"])
    assert code == 1
    assert err == ""
    assert calls == []
    assert out == f"{_missing(emb)}\nмодель сегментации на месте: {seg}\n"


def test_check_stt_and_diar_fails_without_the_voice_set(data_root, monkeypatch, capsys):
    """`--check --stt --diar`: распознавание на месте не отменяет нехватку голосов."""
    emb, seg = _targets(data_root)
    stt = data_root / "models" / "stt" / "sensevoice.onnx"
    assert get_models.stt_target() == stt
    _place_onnx(stt, 100 * 1024 * 1024)
    stt.with_name("tokens.txt").write_text("токены", encoding="utf-8")
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--check", "--stt", "--diar"])
    assert code == 1
    assert err == ""
    assert calls == []
    assert out == (f"модель распознавания на месте: {stt}\n"
                   f"{_missing(emb)}\n{_missing(seg)}\n")


def test_diar_with_dest_is_embeddings_only(data_root, monkeypatch, capsys):
    """`--diar --dest` — одна цель и строка, что сегментация не ставилась."""
    dest = data_root / "свои" / "embedding.onnx"
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar", "--dest", str(dest)])
    assert code == 0
    assert err == ""
    assert calls == [(_EMB_URL, dest, 40, _EMB_SHA)]
    assert out == (f"модели диаризации нет ({dest})\n{_LIVE}\n{_SEG_SKIPPED}\n")


def test_check_diar_with_dest_does_not_look_at_segmentation(data_root, monkeypatch, capsys):
    """`--check --diar --dest`: эмбеддинги по указанному пути, сегментация не проверялась."""
    dest = data_root / "свои" / "embedding.onnx"
    _place_onnx(dest, _EMB_BYTES)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar", "--check", "--dest", str(dest)])
    assert code == 0
    assert err == ""
    assert calls == []
    assert out == f"модель на месте: {dest}\n{_SEG_UNCHECKED}\n"


def test_diar_with_url_is_embeddings_only_and_skips_the_pin(data_root, monkeypatch, capsys):
    """Свой `--url` у `--diar` — одна цель, без контрольной суммы и без сегментации."""
    emb, _seg = _targets(data_root)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar", "--url", "https://example.invalid/mine.onnx"])
    assert code == 0
    assert err == ""
    assert calls == [("https://example.invalid/mine.onnx", emb, 40, "")]
    assert out == (f"модели диаризации нет ({emb})\n{_LIVE}\n{_SEG_SKIPPED}\n")


def test_custom_diar_already_present_still_names_the_skipped_segmentation(data_root, monkeypatch, capsys):
    """Свой путь, файл уже стоит: «нечего делать» про этот файл и строка про сегментацию."""
    dest = data_root / "свои" / "embedding.onnx"
    _place_onnx(dest, _EMB_BYTES)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--diar", "--dest", str(dest)])
    assert code == 0
    assert err == ""
    assert calls == []
    assert out == f"модель уже стоит: {dest} — нечего делать\n{_SEG_SKIPPED}\n"


def test_several_targets_with_one_url_are_refused_before_any_download(data_root, monkeypatch, capsys):
    """`--stt --diar --url` — отказ до сети, код 2."""
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--stt", "--diar", "--url", "https://example.invalid/x.onnx"])
    assert code == 2
    assert calls == []
    assert out == ""
    assert err.splitlines()[-1] == _REFUSAL


def test_several_targets_with_one_dest_are_refused_before_any_download(data_root, monkeypatch, capsys):
    """`--segmentation --diar --dest` — отказ до сети, код 2."""
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--segmentation", "--diar", "--dest", str(data_root / "один.onnx")])
    assert code == 2
    assert calls == []
    assert out == ""
    assert err.splitlines()[-1] == _REFUSAL


def test_segmentation_alone_does_not_fetch_embeddings(data_root, monkeypatch, capsys):
    """`--segmentation` без `--diar` ставит только сегментацию."""
    _emb, seg = _targets(data_root)
    calls = _record(monkeypatch)
    code, out, err = _run(monkeypatch, capsys, ["--segmentation"])
    assert code == 0
    assert err == ""
    assert calls == [(_SEG_URL, seg, 7, _SEG_SHA)]
    assert out == f"модели диаризации нет ({seg})\n"

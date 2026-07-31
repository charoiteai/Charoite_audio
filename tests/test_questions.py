"""Спросил — и вопрос исчез. Ни в графе, ни в досье его нет.

Проверено на живом графе владельца: файла с историей вопросов нет ни в
Application Support, ни рядом с графом. Всё, что человек спрашивал у
ассистента по архиву, существовало ровно до закрытия окна. Через неделю
вопрос «что там было по партициям» искать негде — искать нечего.

Второе, менее очевидное: «Вопросы и ответы» встречи в графе лежат, но в
досье не попадают. Кластер темы строится вокруг ядра из того, что на него
ссылается, а в этих файлах нет ни одной ссылки. На живом графе: 83 досье, ни
одно не использует ни один файл вопросов.

Отсюда правило: вопрос — это источник для темы, и связь с темой должна быть
не угадана по словам, а взята оттуда, где она уже посчитана. Поиск, отвечая,
УЖЕ знает, на каких узлах построен ответ. Эти узлы и становятся ссылками, а
ссылки — билетом в кластер досье.

Файл держит:

    1. Вопрос сохраняется узлом графа со ссылками на источники ответа.
    2. Ссылки в формате графа — иначе кластер их не увидит.
    3. Вопрос без найденных источников тоже сохраняется: список того, что
       спрашивали, а в архиве нет, — это заявка на пробел, а не мусор.
    4. Повторный вопрос не плодит файлы-близнецы.
    5. Личное (дневник) в рабочий граф не попадает.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import questions  # noqa: E402

SOURCES = ["Ядра/Платёжный провайдер.md", "Встречи/2026-07-15_1400.md"]


def test_question_becomes_a_node_with_links(tmp_path):
    path = questions.save(tmp_path, "Что решили по партициям?",
                          "Партиция №59 перекошена, добавили сегменты.",
                          sources=SOURCES, stamp="2026-07-30_1830")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Что решили по партициям?" in text
    assert "Партиция №59" in text
    # ссылки — тем же синтаксисом, каким живёт весь граф
    assert "[[Ядра/Платёжный провайдер]]" in text
    assert "[[Встречи/2026-07-15_1400]]" in text


def test_node_lands_in_the_questions_folder(tmp_path):
    path = questions.save(tmp_path, "Вопрос", "Ответ", sources=[],
                          stamp="2026-07-30_1830")
    assert path.parent.name == questions.FOLDER
    assert path.parent.parent == tmp_path


def test_unanswered_question_is_kept_too(tmp_path):
    """Спросил, а в архиве нет — это заявка на пробел, а не мусор."""
    path = questions.save(tmp_path, "Кто отвечает за ручную перезапись?",
                          "В архиве ничего не найдено.", sources=[],
                          stamp="2026-07-30_1900")
    text = path.read_text(encoding="utf-8")
    assert "ручную перезапись" in text
    assert questions.NO_SOURCES_TAG in text, \
        "вопрос без источников должен быть помечен — это список пробелов"


def test_same_question_twice_does_not_breed_twins(tmp_path):
    first = questions.save(tmp_path, "Что по партициям?", "Ответ раз",
                           sources=SOURCES, stamp="2026-07-30_1830")
    second = questions.save(tmp_path, "что по  Партициям?", "Ответ два",
                            sources=SOURCES, stamp="2026-07-30_1930")
    assert first == second, "повторный вопрос завёл второй файл"
    text = first.read_text(encoding="utf-8")
    assert "Ответ раз" in text and "Ответ два" in text, "история ответов потеряна"


def test_private_sphere_never_lands_in_the_work_graph(tmp_path):
    """Дневник — отдельная сфера. Личное не всплывает в рабочем поиске."""
    work, diary = tmp_path / "Работа", tmp_path / "Дневник"
    work.mkdir()
    diary.mkdir()
    path = questions.save(work, "Вопрос", "Ответ", sources=[],
                          stamp="2026-07-30_2000")
    assert diary not in path.parents


def test_title_is_readable_and_safe(tmp_path):
    """Имя файла — по вопросу: человек ищет глазами, а не по штампу."""
    path = questions.save(tmp_path, "Что решили по «Партициям»: 59 или 60?",
                          "Ответ", sources=[], stamp="2026-07-30_1830")
    name = path.name
    assert "Партиц" in name
    for bad in ("/", ":", "«", "»", "?"):
        assert bad not in name, f"опасный символ в имени файла: {bad}"


def test_sources_outside_the_graph_are_not_linked(tmp_path):
    """Ссылка ведёт по графу. Путь мимо графа ссылкой не становится."""
    path = questions.save(tmp_path, "Вопрос", "Ответ",
                          sources=["/etc/hosts", "Ядра/Тема.md"],
                          stamp="2026-07-30_1830")
    text = path.read_text(encoding="utf-8")
    assert "[[Ядра/Тема]]" in text
    assert "etc/hosts" not in text


def test_dossier_cluster_picks_the_question_up(tmp_path):
    """Главная проверка: вопрос доходит до досье, а не оседает в папке."""
    sys.path.insert(0, str(REPO / "src"))
    import dossier

    graph = tmp_path / "Работа"
    (graph / "Ядра").mkdir(parents=True)
    (graph / "Встречи").mkdir()
    (graph / "Ядра" / "Партиции.md").write_text(
        "# Партиции\n## Статус\nПеркос №59\n", encoding="utf-8")
    (graph / "Встречи" / "2026-07-15_1400.md").write_text(
        "# Встреча\n[[Ядра/Партиции]]\n", encoding="utf-8")

    questions.save(graph, "Что по партициям?", "Перекос на №59",
                   sources=["Ядра/Партиции.md"], stamp="2026-07-30_1830")

    files, backlinks = dossier.scan(graph)
    clusters = dossier.clusters(files, backlinks, min_size=2)
    assert "Партиции" in clusters, f"темы нет: {list(clusters)}"
    members = clusters["Партиции"]
    assert any("артиц" in m and m != "Партиции" for m in members), \
        f"вопрос не попал в кластер темы: {members}"

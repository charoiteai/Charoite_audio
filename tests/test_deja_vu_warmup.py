"""Прогрев кэша векторов дежавю — пачками двери, с накоплением (№358).

Одним вызовом на весь граф дверь эмбеддингов отвечает «всё или ничего», а
800 ядер не укладывались в 20 с на машине, занятой встречей: кэш не грелся
никогда, и каждый проход платил полной перепосылкой (круг 1 по коду, Opus I1).
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
import daemon  # noqa: E402
import llm  # noqa: E402


def _cores(tmp_path, n):
    out = []
    for i in range(n):
        p = tmp_path / f"Ядро {i:03d}.md"
        p.write_text("## Статус\nидёт\n", encoding="utf-8")
        out.append(p)
    return out


def test_warmup_keeps_every_good_batch_and_stops_at_the_first_refusal(tmp_path):
    cores = _cores(tmp_path, 4 * llm.EMBED_BATCH_TEXTS)
    вызовы = []

    def embed(payload):
        вызовы.append(len(payload))
        return [[1.0]] * len(payload) if len(вызовы) < 3 else []

    vecs: dict = {}
    added = daemon.warm_core_vectors(cores, vecs, embed, max_batches=4)
    assert added == 2 * llm.EMBED_BATCH_TEXTS and len(vecs) == added, "удачные пачки потеряны"
    assert вызовы == [llm.EMBED_BATCH_TEXTS] * 3, "после отказа проход продолжился: %s" % вызовы


def test_warmup_is_capped_per_pass_and_continues_next_pass(tmp_path):
    cores = _cores(tmp_path, 5 * llm.EMBED_BATCH_TEXTS)
    vecs: dict = {}

    def embed(payload):
        return [[1.0]] * len(payload)

    first = daemon.warm_core_vectors(cores, vecs, embed, max_batches=2)
    second = daemon.warm_core_vectors(cores, vecs, embed, max_batches=2)
    assert first == second == 2 * llm.EMBED_BATCH_TEXTS
    assert len(vecs) == 4 * llm.EMBED_BATCH_TEXTS, "второй проход начал не с того места"


def test_warmup_sends_the_status_line_without_annotations_capped_at_400(tmp_path):
    p = tmp_path / "Ядро.md"
    p.write_text("## Статус\nидёт _(было: «стоит», 01.09)_ " + "я" * 600 + "\n", encoding="utf-8")
    отправлено = []
    daemon.warm_core_vectors([p], {}, lambda payload: (отправлено.extend(payload), [[1.0]] * len(payload))[1])
    assert отправлено and отправлено[0].startswith("Ядро. идёт") and "_(" not in отправлено[0]
    assert len(отправлено[0]) == 400

"""Осколок голоса — не участник встречи.

13.08 встреча на троих дала в стенограмме шестнадцать меток: реальные
участники держали 92% текста, а тринадцать «собеседников» — по 26-193 знака,
то есть реплики в секунду-две («да», «угу», «согласен»). Строгая склейка их
не забирала: с короткого сигнала эмбеддинг шумный и до 0.72 не дотягивает,
а кластеры из одних коротких реплик вообще не получали вектора — фильтр
пропускал только сегменты длиннее двух секунд.

Разрыв между участником и осколком — два порядка по объёму речи, и на этом
стоит правило: кто наговорил меньше получаса секунд, тот кусок чужого
голоса. Тесты держат границы этого правила — оно опасно ровно тем, что при
переусердствовании склеит двух разных людей.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from diarize import MIN_SPEAKER_S, WEAK_THRESHOLD, assign_shards  # noqa: E402


def test_short_cluster_goes_to_the_closest_voice():
    talk = {1: 300.0, 2: 240.0, 3: 4.0}          # третий сказал «угу»
    sim = {(3, 1): 0.51, (3, 2): 0.62}
    assert assign_shards(talk, sim) == {3: 2}


def test_real_participants_are_never_merged():
    # Оба наговорили достаточно — даже при высокой похожести это два
    # человека, и решать за них склейкой мы не имеем права.
    talk = {1: 300.0, 2: 120.0}
    assert assign_shards(talk, {(1, 2): 0.9}) == {}


def test_stranger_shard_stays_alone():
    # Голос не похож ни на кого из присутствующих: это может быть реплика
    # из коридора или чужой человек. Приписать его наугад — исказить, кто
    # что сказал, а это хуже лишней метки.
    talk = {1: 300.0, 2: 3.0}
    assert assign_shards(talk, {(2, 1): WEAK_THRESHOLD - 0.05}) == {}


def test_all_quiet_means_hands_off():
    # Короткий обмен репликами, где никто не набрал минимума: слипать их
    # между собой наугад опаснее, чем оставить как есть.
    talk = {1: 8.0, 2: 6.0, 3: 5.0}
    assert assign_shards(talk, {(1, 2): 0.95, (2, 3): 0.9}) == {}


def test_shard_picks_the_best_of_several():
    talk = {1: 300.0, 2: 200.0, 3: 100.0, 4: 2.0}
    sim = {(4, 1): 0.50, (4, 2): 0.71, (4, 3): 0.66}
    assert assign_shards(talk, sim) == {4: 2}


def test_symmetry_of_similarity_keys():
    # Пары приходят из попарного обхода в одном порядке; функция не должна
    # зависеть от того, кто в ключе первым.
    talk = {1: 300.0, 9: 3.0}
    assert assign_shards(talk, {(1, 9): 0.8}) == {9: 1}
    assert assign_shards(talk, {(9, 1): 0.8}) == {9: 1}


def test_threshold_is_a_boundary_not_a_suggestion():
    talk = {1: 300.0, 2: 2.0}
    assert assign_shards(talk, {(2, 1): WEAK_THRESHOLD}) == {}, "ровно на планке — не склеиваем"
    assert assign_shards(talk, {(2, 1): WEAK_THRESHOLD + 0.01}) == {2: 1}


def test_thirty_seconds_is_the_line_between_person_and_shard():
    sim = {(2, 1): 0.8}
    assert assign_shards({1: 300.0, 2: MIN_SPEAKER_S - 0.1}, sim) == {2: 1}
    assert assign_shards({1: 300.0, 2: MIN_SPEAKER_S}, sim) == {}, \
        "полминуты речи — уже участник, склеивать нельзя"

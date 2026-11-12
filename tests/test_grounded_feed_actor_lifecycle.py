"""Synthetic source-producer replacement after an acknowledged cutoff."""

import pytest

from grounded_feed.actor_core import ActorCore, ActorRefused


def test_source_worker_can_be_replaced_only_after_confirmed_cutoff():
    class Worker:
        def __init__(self):
            self.calls = []

        def cancel_and_join(self, timeout_s):
            self.calls.append(timeout_s)
            return True

    actor = ActorCore()
    first, replacement = Worker(), Worker()
    actor.attach_source_worker(first)
    with pytest.raises(ActorRefused, match="source_worker_already_live"):
        actor.attach_source_worker(replacement)

    assert actor.stop_workers(0.25) is True
    assert first.calls == [0.25]
    actor.attach_source_worker(replacement)
    assert actor.cutoff_acknowledged is False
    assert actor.stop_workers(0.5) is True
    assert replacement.calls == [0.5]
    assert first.calls == [0.25]
    assert actor.cutoff_acknowledged is True

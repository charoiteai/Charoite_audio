"""«Сказать один раз» — реестр строк на экземпляре, для пакета без приложения.

Отказ, который повторяется на каждом вызове, — не новость, а шум: сервер отвечает
тем же кодом и телом, и строка о нём вытесняет из журнала настоящую причину. Дверь
векторов пакета (`charoite_graph.embed_door`) печатает такие строки через реестр,
который ей передали: приложение даёт свой реестр процесса (`src/once.py`), а
пользователь пакета получает по экземпляру на каждую собранную дверь — глобала в
пакете нет, чужие «уже сказано» не стираются (№323 PR 1, вход 2, критика 2).

Контракт — тот же, что у `src/once.py`, и одна контрактная проверка
(`tests/test_notices.py`) гоняет обе реализации; свести их в одну — №524.

Ключ — пара `(пространство, смысл)`. В смысл входит то, что делает повтор
повтором: код ответа, тело, форма, — а не готовая строка целиком.

Только stdlib.
"""
from __future__ import annotations

import sys
import threading

#: Ключ: `(пространство, смысл)`. Смысл — хешируемое описание повтора.
Key = tuple[str, object]


class Notices:
    """Реестр «уже сказали»: `first/say/forget/reset` над своим набором ключей."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._said: set[Key] = set()

    def first(self, key: Key) -> bool:
        """True — ключа ещё не было (и он запомнен); False — уже сказали.

        Решение и пометка — под одним замком: два потока на одном ключе получают
        True ровно один раз, и строка не двоится.
        """
        with self._lock:
            if key in self._said:
                return False
            self._said.add(key)
            return True

    def say(self, key: Key, text: str, stream=None) -> bool:
        """Первая на ключ строка `text` в `stream` (по умолчанию stderr); True — напечатали.

        Замок — только на решение «первый ли раз», печать — после него. Строка
        уходит одним вызовом `write` вместе с переводом строки: две строки разных
        потоков не склеиваются. Напечатать не удалось — ключ забывается, и
        следующая попытка скажет снова: строка о неполадке не роняет работу и не
        замолкает навсегда.
        """
        if not self.first(key):
            return False
        try:
            out = sys.stderr if stream is None else stream
            out.write(text + "\n")
            out.flush()
        except Exception:      # noqa: BLE001 — строка о неполадке не роняет работу
            self.forget(key)
            return False
        return True

    def forget(self, key: Key) -> None:
        """Эпизод кончился: ключ можно сказать снова."""
        with self._lock:
            self._said.discard(key)

    def reset(self, namespace: str | None = None) -> None:
        """Забыть всё (`None`) или только ключи одного пространства."""
        with self._lock:
            if namespace is None:
                self._said.clear()
            else:
                for key in [k for k in self._said if k[0] == namespace]:
                    self._said.discard(key)

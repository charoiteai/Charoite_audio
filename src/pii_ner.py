"""Распознавание имён для маски (№793): Slovnet из пакета natasha, локально и без сети.

Веса едут внутри пакета natasha (`natasha/data`), загрузчика нет. Нет пакета или
модель не поднялась — `pii_mask.MaskRefused` с рецептом: базовая маска без NER
пропускает гостей, которых нет в графе (замер 26.10: 88 из 140 людей против 138),
поэтому тихо понижать уровень нельзя (n793-in3 DS-I7).
"""
from __future__ import annotations

import threading

import pii_mask

INSTALL_RECIPE = ("поставьте пакет natasha в окружение Чароита "
                  "(pip install natasha==1.6.0) или выключите маску явно: sufler.cloud_mask: false")

#: Типы Slovnet → вид маски. Места (LOC) не маскируем: город — не персональные данные,
#: а ревизия без географии теряет смысл.
_KINDS = {"PER": pii_mask.PERSON, "ORG": pii_mask.SYSTEM}

_lock = threading.Lock()
_model = None


def _load():
    global _model
    with _lock:
        if _model is None:
            try:
                from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter
            except Exception as e:  # noqa: BLE001 — любой сбой импорта = нет движка
                raise pii_mask.MaskRefused(f"нет распознавания имён ({type(e).__name__}): "
                                           f"{INSTALL_RECIPE}") from None
            try:
                emb = NewsEmbedding()
                _model = (Doc, Segmenter(), NewsNERTagger(emb), emb)
            except Exception as e:  # noqa: BLE001
                raise pii_mask.MaskRefused(f"модель имён не поднялась ({type(e).__name__}): "
                                           f"{INSTALL_RECIPE}") from None
        return _model


def spans(text: str) -> list[tuple[int, int, str]]:
    """Спаны людей и организаций: `(start, end, PERSON|SYSTEM)` по координатам `text`."""
    doc_cls, segmenter, tagger, _emb = _load()
    doc = doc_cls(text)
    doc.segment(segmenter)
    doc.tag_ner(tagger)
    return [(s.start, s.stop, _KINDS[s.type]) for s in doc.spans if s.type in _KINDS]


def is_common(word: str) -> bool:
    """Есть ли слово (нижний регистр) в словаре новостного корпуса Navec — того же,
    на котором обучен Slovnet: 250 тыс. словоформ. Для маски это «общее слово»:
    имя системы маскируется только отличительной частью (`pii_mask._distinctive`)."""
    return word in _load()[3]

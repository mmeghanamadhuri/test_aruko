"""Plain-text helpers to turn object-detection labels into a spoken sentence.

Mirrors ``sirena_ui.workers.object_announcer`` without PyQt5 so nina-link can
call gTTS + AudioPlayer headlessly.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, Sequence

# Same tables as object_announcer.py (keep in sync for consistent wording).
_IRREGULAR_PLURALS: Dict[str, str] = {
    "person": "people",
    "mouse": "mice",
    "knife": "knives",
    "child": "children",
    "tooth": "teeth",
    "foot": "feet",
    "sheep": "sheep",
    "fish": "fish",
    "deer": "deer",
}

_NUMBER_WORDS: Dict[int, str] = {
    1: "a",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def _pluralise(label: str, count: int) -> str:
    if count <= 1:
        return label
    parts = label.split()
    head = parts[-1]
    pl = _IRREGULAR_PLURALS.get(head)
    if pl is None:
        if head.endswith(("s", "x", "z", "ch", "sh")):
            pl = head + "es"
        elif head.endswith("y") and len(head) > 1 and head[-2] not in "aeiou":
            pl = head[:-1] + "ies"
        else:
            pl = head + "s"
    parts[-1] = pl
    return " ".join(parts)


def _article(label: str) -> str:
    return "an" if label[:1].lower() in "aeiou" else "a"


def _format_phrase(label: str, count: int) -> str:
    if count == 1:
        return f"{_article(label)} {label}"
    word = _NUMBER_WORDS.get(count, str(count))
    return f"{word} {_pluralise(label, count)}"


def _join_phrases(phrases: Sequence[str]) -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return ", ".join(phrases[:-1]) + f", and {phrases[-1]}"


def build_sentence(labels: Iterable[str]) -> str:
    counts = Counter(s for s in (str(x).strip() for x in labels) if s)
    if not counts:
        return "I don't see anything yet."
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    phrases: List[str] = [_format_phrase(label, n) for label, n in ordered]
    return "I see " + _join_phrases(phrases) + "."

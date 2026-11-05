"""Deterministic, history-aware practice suggestions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any


def repeated_word_suggestion(
    current_comparison: dict[str, Any],
    previous_comparisons: Iterable[dict[str, Any]],
) -> str | None:
    current_targets = {
        str(edit["expected"]).casefold()
        for edit in current_comparison.get("edits", [])
        if edit.get("expected")
    }
    if not current_targets:
        return None

    previous_counts: Counter[str] = Counter()
    for comparison in previous_comparisons:
        previous_counts.update(
            str(edit["expected"]).casefold()
            for edit in comparison.get("edits", [])
            if edit.get("expected")
        )
    repeated = sorted(
        (word for word in current_targets if previous_counts[word] > 0),
        key=lambda word: (-previous_counts[word], word),
    )
    if not repeated:
        return None
    words = ", ".join(f"'{word}'" for word in repeated[:3])
    return (
        f"Recent transcripts also differed on {words}. This may reflect ASR uncertainty, "
        "not a confirmed pronunciation error. Repeat the word slowly in a short phrase."
    )
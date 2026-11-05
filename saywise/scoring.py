"""Deterministic transcript comparison and score summaries."""

from __future__ import annotations

import re
from typing import Any


TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[''][a-z0-9]+)?", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_PATTERN.findall(text)]


def compare_words(expected_text: str, recognized_text: str) -> dict[str, Any]:
    """Return a minimum-edit alignment; this is lexical evidence, not pronunciation."""
    expected = tokenize(expected_text)
    recognized = tokenize(recognized_text)
    rows = len(expected) + 1
    columns = len(recognized) + 1
    costs = [[0] * columns for _ in range(rows)]
    back: list[list[str | None]] = [[None] * columns for _ in range(rows)]

    for row in range(1, rows):
        costs[row][0] = row
        back[row][0] = "deletion"
    for column in range(1, columns):
        costs[0][column] = column
        back[0][column] = "insertion"

    for row in range(1, rows):
        for column in range(1, columns):
            same = expected[row - 1] == recognized[column - 1]
            candidates = [
                (costs[row - 1][column - 1] + (not same), "match" if same else "substitution"),
                (costs[row - 1][column] + 1, "deletion"),
                (costs[row][column - 1] + 1, "insertion"),
            ]
            costs[row][column], back[row][column] = min(
                candidates, key=lambda item: (item[0], item[1] != "match")
            )

    alignment: list[dict[str, str | None]] = []
    row = len(expected)
    column = len(recognized)
    while row or column:
        operation = back[row][column]
        if operation in ("match", "substitution"):
            alignment.append({
                "type": operation,
                "expected": expected[row - 1],
                "recognized": recognized[column - 1],
            })
            row -= 1
            column -= 1
        elif operation == "deletion":
            alignment.append({"type": operation, "expected": expected[row - 1], "recognized": None})
            row -= 1
        else:
            alignment.append({"type": "insertion", "expected": None, "recognized": recognized[column - 1]})
            column -= 1
    alignment.reverse()
    edits = [item for item in alignment if item["type"] != "match"]

    substitutions = sum(edit["type"] == "substitution" for edit in edits)
    deletions = sum(edit["type"] == "deletion" for edit in edits)
    insertions = sum(edit["type"] == "insertion" for edit in edits)
    errors = substitutions + deletions + insertions
    reference_count = len(expected)
    return {
        "expected_words": expected,
        "recognized_words": recognized,
        "alignment": alignment,
        "edits": edits,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "word_error_rate": errors / reference_count if reference_count else None,
        "reference_word_count": reference_count,
    }


def fluency_summary(
    duration_seconds: float,
    speech_seconds: float,
    pause_durations: list[float],
    recognized_word_count: int,
) -> dict[str, float | int | None]:
    """Summarize measurable timing features without inventing a fluency grade."""
    speaking_rate = (
        recognized_word_count * 60 / speech_seconds if speech_seconds > 0 else None
    )
    return {
        "duration_seconds": round(max(duration_seconds, 0), 2),
        "speech_seconds": round(max(speech_seconds, 0), 2),
        "speaking_rate_wpm": round(speaking_rate, 1) if speaking_rate is not None else None,
        "pause_count": len(pause_durations),
        "longest_pause_seconds": round(max(pause_durations, default=0), 2),
    }
"""Reference metrics for offline research evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def edit_distance(reference: Sequence[Any], hypothesis: Sequence[Any]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, reference_item in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_item in enumerate(hypothesis, start=1):
            current.append(min(
                current[column - 1] + 1,
                previous[column] + 1,
                previous[column - 1] + (reference_item != hypothesis_item),
            ))
        previous = current
    return previous[-1]


def phoneme_error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float | None:
    if not reference:
        return None
    return edit_distance(reference, hypothesis) / len(reference)


def detection_metrics(
    references: Sequence[bool], predictions: Sequence[bool]
) -> dict[str, int | float | None]:
    if len(references) != len(predictions):
        raise ValueError("Reference and prediction arrays must have equal length.")
    true_positive = sum(reference and prediction for reference, prediction in zip(references, predictions))
    false_positive = sum(not reference and prediction for reference, prediction in zip(references, predictions))
    false_negative = sum(reference and not prediction for reference, prediction in zip(references, predictions))
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else None
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }
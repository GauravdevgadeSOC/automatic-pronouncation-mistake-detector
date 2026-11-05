import unittest

from saywise.scoring import compare_words, fluency_summary
from saywise.evaluation import detection_metrics, phoneme_error_rate
from saywise.feedback import repeated_word_suggestion


class CompareWordsTests(unittest.TestCase):
    def test_reports_substitutions_deletions_and_insertions(self):
        result = compare_words("we practice English", "we learn English today")

        self.assertEqual(result["substitutions"], 1)
        self.assertEqual(result["deletions"], 0)
        self.assertEqual(result["insertions"], 1)
        self.assertEqual(result["word_error_rate"], 2 / 3)

    def test_empty_recognition_is_all_deletions(self):
        result = compare_words("say this clearly", "")

        self.assertEqual(result["deletions"], 3)
        self.assertEqual(result["word_error_rate"], 1.0)

    def test_empty_reference_does_not_divide_by_zero(self):
        result = compare_words("", "extra words")

        self.assertEqual(result["insertions"], 2)
        self.assertIsNone(result["word_error_rate"])

    def test_alignment_preserves_insertion_position(self):
        result = compare_words("we practice English", "we now practice English")

        self.assertEqual(
            [item["type"] for item in result["alignment"]],
            ["match", "insertion", "match", "match"],
        )


class FluencySummaryTests(unittest.TestCase):
    def test_reports_features_without_a_composite_grade(self):
        result = fluency_summary(8, 6, [0.4, 1.2], 12)

        self.assertEqual(result["speaking_rate_wpm"], 120.0)
        self.assertEqual(result["pause_count"], 2)
        self.assertNotIn("score", result)


class EvaluationMetricTests(unittest.TestCase):
    def test_phone_error_rate(self):
        self.assertEqual(phoneme_error_rate(["s", "i", "t"], ["s", "ɪ", "t"]), 1 / 3)

    def test_detection_precision_recall_and_f1(self):
        result = detection_metrics([True, True, False, False], [True, False, True, False])

        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["f1"], 0.5)


class FeedbackTests(unittest.TestCase):
    def test_suggests_only_words_repeated_in_transcript_mismatches(self):
        current = {"edits": [{"type": "substitution", "expected": "thought", "recognized": "taught"}]}
        previous = [{"edits": [{"type": "deletion", "expected": "thought"}]}]

        suggestion = repeated_word_suggestion(current, previous)

        self.assertIn("'thought'", suggestion)
        self.assertIn("not a confirmed pronunciation error", suggestion)

    def test_does_not_infer_a_target_from_insertions(self):
        current = {"edits": [{"type": "insertion", "expected": None, "recognized": "extra"}]}

        self.assertIsNone(repeated_word_suggestion(current, []))


if __name__ == "__main__":
    unittest.main()
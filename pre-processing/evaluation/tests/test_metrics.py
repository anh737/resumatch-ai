"""Hand-computed unit tests for evaluation.retrieval_metrics.

Every expected value is derived by hand (log2 arithmetic spelled out in
the assertions) so a regression in the metric conventions — 1-based
positions, log2(i+1) discount, IDCG normalization — fails loudly.
Uses only stdlib + pytest so the suite runs on partial installs.
"""

import math

import pytest

from evaluation.retrieval_metrics import (
    aggregate,
    dcg_at_k,
    evaluate_ranking,
    hit_at_k,
    idcg_at_k,
    ndcg_at_k,
    precision_at_k,
    reciprocal_rank,
)


# ---------------------------------------------------------------- reciprocal_rank


class TestReciprocalRank:
    def test_relevant_at_rank_1(self):
        assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0

    def test_relevant_at_rank_2(self):
        assert reciprocal_rank(["a", "b", "c"], {"b"}) == pytest.approx(0.5)

    def test_relevant_absent(self):
        assert reciprocal_rank(["a", "b", "c"], {"z"}) == 0.0

    def test_empty_ranking(self):
        assert reciprocal_rank([], {"a"}) == 0.0

    def test_first_relevant_wins_when_several_relevant(self):
        # relevant at ranks 2 and 3 -> rr = 1/2
        assert reciprocal_rank(["a", "b", "c"], {"b", "c"}) == pytest.approx(0.5)


# ------------------------------------------------------------------- hit_at_k


class TestHitAtK:
    def test_hit_inside_k(self):
        assert hit_at_k(["a", "b", "c"], {"b"}, 2) == 1.0

    def test_miss_outside_k(self):
        assert hit_at_k(["a", "b", "c"], {"c"}, 2) == 0.0

    def test_hit_exactly_at_k(self):
        assert hit_at_k(["a", "b", "c"], {"c"}, 3) == 1.0

    def test_never_retrieved(self):
        assert hit_at_k(["a", "b", "c"], {"z"}, 10) == 0.0

    def test_k_zero(self):
        assert hit_at_k(["a"], {"a"}, 0) == 0.0


# ------------------------------------------------------------- precision_at_k


class TestPrecisionAtK:
    def test_one_of_two(self):
        assert precision_at_k(["a", "b", "c"], {"a"}, 2) == pytest.approx(0.5)

    def test_two_of_three(self):
        assert precision_at_k(["a", "b", "c"], {"a", "c"}, 3) == pytest.approx(2 / 3)

    def test_none_relevant(self):
        assert precision_at_k(["a", "b"], {"z"}, 2) == 0.0

    def test_k_beyond_list_uses_k_as_denominator(self):
        # 1 relevant among 3 retrieved, but k=5 -> 1/5 (missing slots are misses)
        assert precision_at_k(["a", "b", "c"], {"a"}, 5) == pytest.approx(0.2)

    def test_k_zero(self):
        assert precision_at_k(["a"], {"a"}, 0) == 0.0


# ------------------------------------------------------------ dcg / idcg / ndcg


class TestDcg:
    def test_single_gain_at_top_is_undiscounted(self):
        # position 1 divides by log2(2) == 1
        assert dcg_at_k([1.0], 1) == pytest.approx(1.0)

    def test_graded_gains_at_4(self):
        # ranked gains [3, 2, 0, 1]:
        # dcg@4 = 3/log2(2) + 2/log2(3) + 0/log2(4) + 1/log2(5)
        expected = 3 / math.log2(2) + 2 / math.log2(3) + 0.0 + 1 / math.log2(5)
        assert dcg_at_k([3, 2, 0, 1], 4) == pytest.approx(expected)

    def test_truncation_at_k(self):
        # only the first two positions count for k=2
        expected = 3 / math.log2(2) + 2 / math.log2(3)
        assert dcg_at_k([3, 2, 0, 1], 2) == pytest.approx(expected)

    def test_k_larger_than_list(self):
        # list shorter than k contributes only its available positions
        expected = 1 / math.log2(2) + 1 / math.log2(3)
        assert dcg_at_k([1, 1], 10) == pytest.approx(expected)

    def test_empty_gains(self):
        assert dcg_at_k([], 5) == 0.0

    def test_k_zero(self):
        assert dcg_at_k([3, 2], 0) == 0.0


class TestIdcg:
    def test_sorts_descending_before_discounting(self):
        # all_gains [3, 2, 1, 0] is already sorted:
        # idcg@4 = 3/log2(2) + 2/log2(3) + 1/log2(4) + 0
        expected = 3 / math.log2(2) + 2 / math.log2(3) + 1 / math.log2(4)
        assert idcg_at_k([3, 2, 1, 0], 4) == pytest.approx(expected)
        # unsorted input must yield the same ideal value
        assert idcg_at_k([0, 1, 3, 2], 4) == pytest.approx(expected)

    def test_single_truth_idcg_is_one_for_all_k(self):
        # single ground truth (gain 1.0): idcg@k == 1 for every k >= 1
        for k in (1, 3, 5, 10):
            assert idcg_at_k([1.0], k) == pytest.approx(1.0)


class TestNdcg:
    def test_graded_example(self):
        ranked = [3, 2, 0, 1]
        ideal = [3, 2, 1, 0]
        dcg = 3 / math.log2(2) + 2 / math.log2(3) + 1 / math.log2(5)
        idcg = 3 / math.log2(2) + 2 / math.log2(3) + 1 / math.log2(4)
        assert ndcg_at_k(ranked, ideal, 4) == pytest.approx(dcg / idcg)

    def test_perfect_ranking_is_one(self):
        assert ndcg_at_k([3, 2, 1], [3, 2, 1], 3) == pytest.approx(1.0)

    def test_idcg_zero_returns_zero(self):
        assert ndcg_at_k([0.0, 0.0], [0.0, 0.0], 3) == 0.0
        assert ndcg_at_k([], [], 3) == 0.0

    def test_k_larger_than_lists(self):
        # both lists exhausted before k; same sums -> ndcg == 1
        assert ndcg_at_k([2, 1], [2, 1], 100) == pytest.approx(1.0)

    def test_empty_ranked_list_with_positive_ideal(self):
        assert ndcg_at_k([], [1.0], 5) == 0.0


# ----------------------------------------------------------- evaluate_ranking


class TestEvaluateRanking:
    def test_single_truth_at_rank_3(self):
        ranked = ["a", "b", "c", "d", "e"]
        relevance = {"c": 1.0}
        out = evaluate_ranking(ranked, relevance, k_values=(1, 3, 5))

        assert out["rank"] == 3
        assert out["mrr"] == pytest.approx(1 / 3)

        assert out["accuracy@1"] == 0.0
        assert out["accuracy@3"] == 1.0
        assert out["accuracy@5"] == 1.0

        assert out["precision@1"] == 0.0
        assert out["precision@3"] == pytest.approx(1 / 3)
        assert out["precision@5"] == pytest.approx(1 / 5)

        # single ground truth: idcg@k == 1, ndcg@k == 1/log2(rank+1) for k >= rank
        assert out["idcg@1"] == pytest.approx(1.0)
        assert out["idcg@3"] == pytest.approx(1.0)
        assert out["dcg@1"] == 0.0
        assert out["dcg@3"] == pytest.approx(1 / math.log2(4))
        assert out["ndcg@1"] == 0.0
        assert out["ndcg@3"] == pytest.approx(1 / math.log2(4))  # == 0.5
        assert out["ndcg@5"] == pytest.approx(0.5)

    def test_relevant_doc_never_retrieved(self):
        out = evaluate_ranking(["a", "b"], {"z": 1.0}, k_values=(1, 3))
        assert out["rank"] is None
        assert out["mrr"] == 0.0
        assert out["accuracy@3"] == 0.0
        assert out["ndcg@3"] == 0.0
        assert out["idcg@3"] == pytest.approx(1.0)

    def test_zero_gain_ids_are_not_relevant(self):
        # "b" has gain 0.0 -> it must not count as relevant for rank/mrr
        out = evaluate_ranking(["b", "c"], {"b": 0.0, "c": 2.0}, k_values=(1,))
        assert out["rank"] == 2
        assert out["mrr"] == pytest.approx(0.5)
        assert out["accuracy@1"] == 0.0

    def test_duplicate_ids_are_collapsed_to_first_occurrence(self):
        # a repeated doc must not earn its gain twice: ndcg stays <= 1,
        # precision counts one hit, and rank/mrr use the first occurrence
        out = evaluate_ranking(["c", "c", "a"], {"c": 1.0}, k_values=(3,))
        assert out["rank"] == 1
        assert out["mrr"] == pytest.approx(1.0)
        assert out["ndcg@3"] == pytest.approx(1.0)
        assert out["precision@3"] == pytest.approx(1 / 3)
        assert out["dcg@3"] == pytest.approx(1.0)

    def test_empty_inputs_yield_floats(self):
        # declared -> float contract holds even for empty gain lists
        assert isinstance(dcg_at_k([], 3), float)
        assert isinstance(idcg_at_k([], 3), float)
        out = evaluate_ranking([], {}, k_values=(1,))
        assert isinstance(out["dcg@1"], float)
        assert isinstance(out["idcg@1"], float)

    def test_graded_relevance_gains(self):
        # gains for the ranked list come from the map (missing id -> 0.0);
        # all_gains for idcg is every value in the map
        ranked = ["x", "y", "unknown"]
        relevance = {"x": 2.0, "y": 1.0, "held_out": 3.0}
        out = evaluate_ranking(ranked, relevance, k_values=(3,))
        expected_dcg = 2 / math.log2(2) + 1 / math.log2(3)
        expected_idcg = 3 / math.log2(2) + 2 / math.log2(3) + 1 / math.log2(4)
        assert out["dcg@3"] == pytest.approx(expected_dcg)
        assert out["idcg@3"] == pytest.approx(expected_idcg)
        assert out["ndcg@3"] == pytest.approx(expected_dcg / expected_idcg)


# ---------------------------------------------------------------- aggregate


class TestAggregate:
    def test_simple_means(self):
        rows = [{"mrr": 1.0, "accuracy@1": 1.0}, {"mrr": 0.5, "accuracy@1": 0.0}]
        out = aggregate(rows)
        assert out["mrr"] == pytest.approx(0.75)
        assert out["accuracy@1"] == pytest.approx(0.5)

    def test_none_values_are_ignored(self):
        # rank None (not retrieved) drops out of the mean instead of poisoning it
        rows = [{"rank": 2, "mrr": 0.5}, {"rank": None, "mrr": 0.0}, {"rank": 4, "mrr": 0.25}]
        out = aggregate(rows)
        assert out["rank"] == pytest.approx(3.0)  # mean of 2 and 4 only
        assert out["mrr"] == pytest.approx(0.25)  # mean over all three rows

    def test_non_numeric_keys_are_skipped(self):
        rows = [{"query": "python dev", "mrr": 1.0}, {"query": "java dev", "mrr": 0.0}]
        out = aggregate(rows)
        assert out == {"mrr": pytest.approx(0.5)}
        assert "query" not in out

    def test_empty_rows(self):
        assert aggregate([]) == {}

    def test_key_missing_from_some_rows(self):
        rows = [{"a": 1.0}, {"a": 3.0, "b": 10.0}]
        out = aggregate(rows)
        assert out["a"] == pytest.approx(2.0)
        assert out["b"] == pytest.approx(10.0)

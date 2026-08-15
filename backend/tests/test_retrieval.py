"""Tests for candidate retrieval.

Retrieval only orders reference material, so approximate scoring is fine. What
must hold is that it never *decides* anything: these tests pin the ordering
properties the two resolvers rely on, and nothing here returns an answer.
"""

from app.services.retrieval import relevance, tokens, top_matches


class TestTokens:
    def test_words_are_split_on_non_alphanumerics(self):
        assert tokens("whole-wheat bread") == {"whole", "wheat", "bread"}

    def test_digits_are_kept(self):
        assert tokens("2% milk") == {"2", "milk"}

    def test_empty_string_has_no_tokens(self):
        assert tokens("") == set()


class TestRelevance:
    def test_contained_phrase_outranks_a_shared_token(self):
        name_tokens = {"whole", "wheat", "bread"}
        assert relevance("wheat bread", "whole wheat bread", name_tokens) > relevance(
            "bread", "whole wheat bread", name_tokens
        )

    def test_longer_contained_phrase_outranks_a_shorter_one(self):
        name_tokens = {"whole", "wheat", "bread"}
        assert relevance("wheat bread", "whole wheat bread", name_tokens) > relevance(
            "wheat", "whole wheat bread", name_tokens
        )

    def test_unrelated_key_scores_zero(self):
        assert relevance("ketchup", "baby spinach", {"baby", "spinach"}) == 0

    def test_shared_token_scores_above_zero(self):
        assert relevance("spinach", "baby spinach", {"baby", "spinach"}) > 0

    def test_more_shared_tokens_score_higher(self):
        name_tokens = {"whole", "wheat", "bread"}
        assert relevance("wheat flour bread", "whole wheat bread", name_tokens) > (
            relevance("bread rolls", "whole wheat bread", name_tokens)
        )


class TestTopMatches:
    def test_irrelevant_entries_are_excluded(self):
        known = {"bread": 7, "milk": 5}
        assert top_matches("whole wheat bread", known, 8) == {"bread": 7}

    def test_results_are_ordered_most_relevant_first(self):
        known = {"bread": 7, "wheat bread": 3, "milk": 5}
        assert list(top_matches("whole wheat bread", known, 8)) == [
            "wheat bread",
            "bread",
        ]

    def test_limit_is_respected(self):
        known = {f"bread {index}": index for index in range(20)}
        assert len(top_matches("bread", known, 8)) == 8

    def test_no_known_entries_yields_nothing(self):
        assert top_matches("dragonfruit", {}, 8) == {}

    def test_values_are_carried_through_unchanged(self):
        """Generic over the value type: one resolver maps to ints, one to strings."""
        assert top_matches("baby spinach", {"spinach": "produce"}, 8) == {
            "spinach": "produce"
        }

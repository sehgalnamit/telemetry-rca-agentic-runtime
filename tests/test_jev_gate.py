"""Tests for the TypeSafe Jev contract models, the documented confidence
formulas, and the deterministic LocalJevStandIn."""

from __future__ import annotations

import pytest

from evaluator.jev_gate import (
    ChoiceAnswer,
    ChoiceQuestion,
    LocalJevStandIn,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    compute_choice_confidence,
    compute_score,
    compute_score_confidence,
    get_default_jev_client,
    parse_jev_answer,
)


def test_choice_confidence_fully_concentrated_is_one():
    # n=2, p_max=1.0 -> (2*1 - 1)/(2-1) = 1.0
    assert compute_choice_confidence({"a": 1.0, "b": 0.0}) == 1.0


def test_choice_confidence_uniform_is_zero():
    # n=2, p_max=0.5 -> (2*0.5 - 1)/(2-1) = 0.0
    assert compute_choice_confidence({"a": 0.5, "b": 0.5}) == 0.0


def test_choice_confidence_matches_documented_formula_directly():
    probs = {"a": 0.4, "b": 0.3, "c": 0.3}
    n = 3
    p_max = 0.4
    expected = (n * p_max - 1) / (n - 1)
    assert compute_choice_confidence(probs) == expected


def test_choice_confidence_single_option_is_fully_confident():
    assert compute_choice_confidence({"only": 1.0}) == 1.0


def test_score_formula_matches_weighted_sum():
    probs = [0.1, 0.2, 0.3, 0.3, 0.1]
    expected = sum(k * p for k, p in enumerate(probs))
    assert compute_score(probs) == expected


def test_score_confidence_fully_concentrated_is_one():
    assert compute_score_confidence([0, 0, 1, 0, 0]) == 1.0
    assert compute_score_confidence([1, 0, 0, 0, 0]) == 1.0


def test_score_confidence_uniform_is_zero():
    assert compute_score_confidence([0.2] * 5) == 0.0


def test_score_confidence_single_level_is_fully_confident():
    assert compute_score_confidence([1.0]) == 1.0


def test_score_confidence_intermediate_between_bounds():
    conf = compute_score_confidence([0.05, 0.15, 0.6, 0.15, 0.05])
    assert 0.0 < conf < 1.0


def test_parse_jev_answer_dispatches_by_type():
    noul = parse_jev_answer({"type": "noul", "noul": 0.8})
    assert isinstance(noul, NoulAnswer)
    assert noul.derived_confidence == pytest.approx(0.6)  # |0.8-0.5|*2

    choice = parse_jev_answer(
        {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.8}
    )
    assert isinstance(choice, ChoiceAnswer)

    score = parse_jev_answer(
        {"type": "score", "score": 2.5, "probabilities": {"0": 0.1, "1": 0.9}, "confidence": 0.8}
    )
    assert isinstance(score, ScoreAnswer)


def test_parse_jev_answer_rejects_unknown_type():
    with pytest.raises(ValueError):
        parse_jev_answer({"type": "mystery"})


# ---------------------------------------------------------------------------
# LocalJevStandIn
# ---------------------------------------------------------------------------


def test_local_stand_in_is_deterministic():
    state = {"candidates": {"a": 0.9, "b": 0.1}, "top_candidate": "a"}
    questions = {
        "c": ChoiceQuestion(instructions="x", criteria={"a": "a", "b": "b"}),
        "s": ScoreQuestion(instructions="x", criteria=["l0", "l1", "l2", "l3", "l4"]),
        "n": NoulQuestion(instructions="x"),
    }
    stand_in_a = LocalJevStandIn()
    stand_in_b = LocalJevStandIn()

    ans_a = stand_in_a.system_one(state, questions)
    ans_b = stand_in_b.system_one(state, questions)

    assert ans_a["c"].model_dump() == ans_b["c"].model_dump()
    assert ans_a["s"].model_dump() == ans_b["s"].model_dump()
    assert ans_a["n"].model_dump() == ans_b["n"].model_dump()


def test_local_stand_in_choice_picks_highest_confidence_candidate():
    state = {"candidates": {"a": 0.9, "b": 0.1, "c": 0.3}, "top_candidate": "a"}
    questions = {"c": ChoiceQuestion(instructions="x", criteria={"a": "a", "b": "b", "c": "c"})}
    answer = LocalJevStandIn().system_one(state, questions)["c"]
    assert answer.choice == "a"
    assert answer.confidence > 0.5


def test_local_stand_in_noul_tracks_top_confidence_directly():
    state = {"candidates": {"a": 0.73}, "top_candidate": "a"}
    answer = LocalJevStandIn().system_one(state, {"n": NoulQuestion(instructions="x")})["n"]
    assert answer.noul == 0.73


def test_local_stand_in_score_confidence_increases_with_decisiveness():
    """A confidence near 0.5 (maximally ambiguous rule-engine signal)
    should yield a lower Score confidence than one near 0 or 1 (a clearly
    decisive signal, whether well- or un-supported) -- monotonic in
    decisiveness, not an artifact of discretization onto rubric levels."""
    rubric = ["l0", "l1", "l2", "l3", "l4"]
    stand_in = LocalJevStandIn()

    def score_conf(confidence: float) -> float:
        state = {"candidates": {"x": confidence}, "top_candidate": "x"}
        return stand_in.system_one(state, {"s": ScoreQuestion(instructions="x", criteria=rubric)})[
            "s"
        ].confidence

    ambiguous = score_conf(0.5)
    moderate = score_conf(0.6)
    decisive_high = score_conf(0.95)
    decisive_low = score_conf(0.05)

    assert ambiguous < moderate < decisive_high
    assert ambiguous < decisive_low
    assert decisive_high > 0.7
    assert ambiguous < 0.2


def test_local_stand_in_unknown_candidates_default_to_zero_support():
    state = {"candidates": {"a": 0.9}, "top_candidate": "a"}
    # "b" and "c" are not in state["candidates"] at all.
    questions = {"c": ChoiceQuestion(instructions="x", criteria={"a": "a", "b": "b", "c": "c"})}
    answer = LocalJevStandIn().system_one(state, questions)["c"]
    assert answer.choice == "a"
    assert answer.probabilities["b"] < answer.probabilities["a"]
    assert answer.probabilities["c"] < answer.probabilities["a"]


def test_get_default_jev_client_returns_local_stand_in_without_api_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    client = get_default_jev_client()
    assert isinstance(client, LocalJevStandIn)


def test_typesafe_client_requires_api_key(monkeypatch):
    from evaluator.jev_gate import TypeSafeJevClient

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        TypeSafeJevClient()

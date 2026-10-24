"""TypeSafe Jev "System One model" client contract + a deterministic,
offline stand-in built from this repo's own rule-engine signals.

Jev is a real, commercially-available product from TypeSafe
(https://typesafe.ai/, https://docs.typesafe.ai/introduction) designed for
fast, cheap, *calibrated* structured decisions: you ask it one or more
typed questions about some ``state`` and it answers each with a typed
result plus a confidence/probability distribution, rather than free-text.

This module implements exactly the documented contract (verified against
docs.typesafe.ai at the time of writing) so that swapping the real hosted
model in for :class:`LocalJevStandIn` is a one-line change (set
``TYPESAFE_API_KEY`` and use :func:`get_default_jev_client`). No network
call in this module is ever exercised by this repo's test suite --
:class:`TypeSafeJevClient` is implemented against the documented REST API
but is intentionally left unexercised in CI (no API key is available in
this build environment).

Three typed question/answer primitives (per docs.typesafe.ai/introduction):

- **Noul** -- a yes/no proposition. Answer: ``{"type": "noul", "noul": p}``
  where ``p`` is a single probability in ``[0, 1]``. There is no separate
  confidence field in the API response for Noul; distance from 0.5 is the
  documented confidence *signal*. :meth:`NoulAnswer.derived_confidence` is
  our own convenience helper for that signal -- not an API field.
- **Choice** -- pick one of an unordered, named set of options. Answer:
  ``{"type": "choice", "choice": "<id>", "probabilities": {...},
  "confidence": c}``. Per docs.typesafe.ai/confidence, for ``n`` options
  with max probability ``p_max``:
  ``confidence = (n * p_max - 1) / (n - 1)``.
- **Score** -- pick a point on an *ordered* rubric of ``n`` levels. Answer:
  ``{"type": "score", "score": s, "probabilities": {...}, "confidence": c}``.
  Per docs.typesafe.ai/confidence: ``score = sum(k * p_k for k in
  0..n-1)``; confidence is derived from the mean absolute deviation (MAD)
  of the probability mass around the distribution's mode index, relative
  to a uniform baseline: ``confidence = max(0, 1 - MAD(p)/MAD(uniform))``
  (1.0 = fully concentrated on one level, 0.0 = uniform across levels).
  The exact reference implementation of ``MAD(uniform)`` is not published
  beyond that description; :func:`compute_score_confidence` implements a
  self-consistent version (MAD computed around the *same* mode index for
  both the actual and uniform distributions) that exactly reproduces both
  documented boundary cases -- see its docstring.

TypeSafe's own official architectural guidance for using the confidence
output is "Confidence-Gated Routing"
(https://docs.typesafe.ai/patterns/confidence-routing): treat confidence as
a second decision axis independent of the answer itself -- high confidence
-> act automatically, medium -> proceed with caution/confirm, low -> do not
act, fall back to a human or a different system. This repo's
``evaluator/cascade.py`` implements exactly that pattern for RCA
hypotheses, with the existing LLM-jury (``evaluator/jury.py``) as the
"different system" fallback.

DeepEval's own Jev integration, "Jev-as-a-judge"
(https://deepeval.com/blog/introducing-jev-as-a-judge), combines several
typed questions into one weighted score,
``JevEval = sum(w_i * v_i) / sum(w_i)``; we cite it here only as the
inspiration for *why* asking several typed questions and combining them is
a reasonable pattern -- this repo does not reimplement JevEval itself.
"""

from __future__ import annotations

import abc
import math
import os
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Questions (requests)
# ---------------------------------------------------------------------------


class NoulQuestion(BaseModel):
    """A yes/no proposition to evaluate."""

    type: Literal["noul"] = "noul"
    instructions: str


class ChoiceQuestion(BaseModel):
    """Pick the best-supported option from an unordered, named set."""

    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str]


class ScoreQuestion(BaseModel):
    """Pick a point on an ordered rubric of levels (index 0 = lowest)."""

    type: Literal["score"] = "score"
    instructions: str
    criteria: list[str]


JevQuestion = Union[NoulQuestion, ChoiceQuestion, ScoreQuestion]


# ---------------------------------------------------------------------------
# Answers (responses)
# ---------------------------------------------------------------------------


class NoulAnswer(BaseModel):
    type: Literal["noul"] = "noul"
    noul: float = Field(ge=0.0, le=1.0)

    @property
    def derived_confidence(self) -> float:
        """Our own convenience signal (not an API field): distance from 0.5,
        rescaled to ``[0, 1]`` (``1.0`` = maximally decisive either way)."""
        return abs(self.noul - 0.5) * 2.0


class ChoiceAnswer(BaseModel):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)


class ScoreAnswer(BaseModel):
    type: Literal["score"] = "score"
    score: float
    probabilities: dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)


JevAnswer = Union[NoulAnswer, ChoiceAnswer, ScoreAnswer]

_ANSWER_TYPES: dict[str, type[BaseModel]] = {
    "noul": NoulAnswer,
    "choice": ChoiceAnswer,
    "score": ScoreAnswer,
}


def parse_jev_answer(raw: dict[str, Any]) -> JevAnswer:
    """Parse a raw ``{"type": ..., ...}`` dict (e.g. from the REST API)
    into the matching typed answer model."""
    answer_type = raw.get("type")
    model = _ANSWER_TYPES.get(answer_type)
    if model is None:
        raise ValueError(f"unknown Jev answer type: {answer_type!r}")
    return model.model_validate(raw)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Confidence / score formulas (docs.typesafe.ai/confidence)
# ---------------------------------------------------------------------------


def compute_choice_confidence(probabilities: dict[str, float]) -> float:
    """``confidence = (n * p_max - 1) / (n - 1)`` for ``n`` options."""
    n = len(probabilities)
    if n <= 1:
        return 1.0
    p_max = max(probabilities.values())
    return max(0.0, (n * p_max - 1) / (n - 1))


def compute_score(level_probabilities: list[float]) -> float:
    """``score = sum(k * p_k for k in 0..n-1)``."""
    return sum(k * p for k, p in enumerate(level_probabilities))


def _mode_index(level_probabilities: list[float]) -> int:
    return max(range(len(level_probabilities)), key=lambda i: level_probabilities[i])


def _mad_around(dist: list[float], center: int) -> float:
    return sum(p * abs(i - center) for i, p in enumerate(dist))


def compute_score_confidence(level_probabilities: list[float]) -> float:
    """``confidence = max(0, 1 - MAD(p) / MAD(uniform))``.

    MAD is computed around the distribution's own mode index (the index
    with the highest probability), for *both* the actual distribution and
    the uniform baseline, so the two are directly comparable:

    - Fully concentrated (``p = [0, ..., 1, ..., 0]``): ``MAD(p) = 0``
      (every mass sits exactly at the mode) -> ``confidence = 1.0``.
    - Fully uniform (``p = [1/n, ..., 1/n]``): the mode is (arbitrarily,
      by tie-break) the first index, and ``MAD(p)`` computed around that
      index is *identical* to ``MAD(uniform)`` computed around the same
      index by construction -> ratio ``= 1`` -> ``confidence = 0.0``.

    This reproduces both documented boundary cases exactly; TypeSafe's own
    reference implementation for intermediate distributions is not
    published beyond the textual description above, so this is our
    best-effort, boundary-verified implementation of it.
    """
    n = len(level_probabilities)
    if n <= 1:
        return 1.0
    mode = _mode_index(level_probabilities)
    mad_actual = _mad_around(level_probabilities, mode)
    uniform = [1.0 / n] * n
    mad_uniform = _mad_around(uniform, mode)
    if mad_uniform == 0:
        return 1.0
    return max(0.0, 1.0 - mad_actual / mad_uniform)


# ---------------------------------------------------------------------------
# Client contract
# ---------------------------------------------------------------------------


class JevClient(abc.ABC):
    """Contract shared by the real hosted TypeSafe Jev adapter and the
    deterministic offline stand-in: ask a batch of typed questions about
    some ``state`` and get back one typed answer per question id.

    ``state`` must be small, structured, metadata-level information (e.g.
    candidate labels + confidence scores) -- consistent with this repo's
    zero-copy principle, raw tenant telemetry rows must never be placed in
    ``state``.
    """

    @abc.abstractmethod
    def system_one(
        self, state: Any, questions: dict[str, JevQuestion]
    ) -> dict[str, JevAnswer]: ...


class TypeSafeJevClient(JevClient):
    """Real adapter for the hosted TypeSafe Jev System One API.

    REST contract (https://docs.typesafe.ai/introduction):
      ``POST https://api.typesafe.ai/v1/systemone``
      headers: ``Authorization: Bearer <TYPESAFE_API_KEY>``, ``Content-Type: application/json``
      body: ``{"state": ..., "model": "jev-latest", "questions": {...}}``

    Only usable if ``TYPESAFE_API_KEY`` is set (constructor raises
    otherwise). Requires ``httpx`` (see ``requirements-jev.txt`` --
    deliberately kept out of the core ``requirements.txt`` so offline
    CI/tests never need it). This class's network path is **not exercised
    by this repo's test suite**: no API key is available in this build
    environment, so only :class:`LocalJevStandIn` is tested.
    """

    API_URL = "https://api.typesafe.ai/v1/systemone"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "jev-latest",
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "TYPESAFE_API_KEY is not set; TypeSafeJevClient cannot be used. "
                "Use LocalJevStandIn (or get_default_jev_client()) for offline/CI use."
            )
        self.model = model
        self.timeout = timeout

    def system_one(self, state: Any, questions: dict[str, JevQuestion]) -> dict[str, JevAnswer]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without optional dep
            raise RuntimeError(
                "httpx is required for TypeSafeJevClient. "
                "Install it with `pip install -r requirements-jev.txt`."
            ) from exc

        payload = {
            "state": state,
            "model": self.model,
            "questions": {qid: q.model_dump() for qid, q in questions.items()},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = httpx.post(self.API_URL, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        raw = response.json()
        raw_answers = raw.get("answers", raw)
        return {qid: parse_jev_answer(ans) for qid, ans in raw_answers.items()}


class LocalJevStandIn(JevClient):
    """Deterministic, fully offline stand-in implementing the *identical*
    contract and the *exact* documented confidence formulas, built entirely
    from this repo's own deterministic rule-engine signals -- no network
    call, no randomness, no trained model.

    This is explicitly **not** the real trained Jev model. It exists so
    this repo's confidence-gated cascade (``evaluator/cascade.py``) is
    fully testable offline, and so switching to the real hosted model later
    is purely a matter of constructing :class:`TypeSafeJevClient` instead
    (same ``JevClient`` contract, same question/answer shapes).

    ``state`` is expected to be a dict with the convention this repo's
    ``evaluator.cascade.ConfidenceGatedCascade`` builds:
    ``{"candidates": {label: rule_confidence, ...}, "top_candidate": label}``.
    Both values are already-computed, metadata-level rule-engine
    confidences -- never raw telemetry.
    """

    def __init__(self, temperature: float = 0.15):
        self.temperature = temperature

    def system_one(self, state: Any, questions: dict[str, JevQuestion]) -> dict[str, JevAnswer]:
        candidates: dict[str, float] = dict(state.get("candidates", {}))
        top_candidate = state.get("top_candidate")
        top_confidence = candidates.get(top_candidate, 0.0) if top_candidate is not None else 0.0

        answers: dict[str, JevAnswer] = {}
        for qid, question in questions.items():
            if isinstance(question, NoulQuestion):
                answers[qid] = NoulAnswer(noul=max(0.0, min(1.0, top_confidence)))
            elif isinstance(question, ChoiceQuestion):
                answers[qid] = self._answer_choice(question, candidates)
            elif isinstance(question, ScoreQuestion):
                answers[qid] = self._answer_score(question, top_confidence)
            else:  # pragma: no cover - defensive
                raise ValueError(f"unsupported question type: {question!r}")
        return answers

    def _softmax(self, scores: dict[str, float]) -> dict[str, float]:
        return self._softmax_with_temperature(scores, temperature=self.temperature)

    @staticmethod
    def _softmax_with_temperature(scores: dict[str, float], temperature: float) -> dict[str, float]:
        if not scores:
            return {}
        scaled = {k: v / temperature for k, v in scores.items()}
        max_scaled = max(scaled.values())
        exps = {k: math.exp(v - max_scaled) for k, v in scaled.items()}
        total = sum(exps.values()) or 1.0
        return {k: v / total for k, v in exps.items()}

    def _answer_choice(self, question: ChoiceQuestion, candidates: dict[str, float]) -> ChoiceAnswer:
        # Restrict to the options the question actually offers, defaulting
        # unknown options to zero support.
        scores = {opt: candidates.get(opt, 0.0) for opt in question.criteria}
        probabilities = self._softmax(scores) if scores else {}
        choice = max(probabilities, key=lambda k: probabilities[k]) if probabilities else ""
        confidence = compute_choice_confidence(probabilities) if probabilities else 0.0
        return ChoiceAnswer(choice=choice, probabilities=probabilities, confidence=confidence)

    def _answer_score(self, question: ScoreQuestion, top_confidence: float) -> ScoreAnswer:
        n = len(question.criteria)
        if n == 0:
            return ScoreAnswer(score=0.0, probabilities={}, confidence=0.0)
        # Map top_confidence onto a continuous rubric location, then peak a
        # Gaussian-shaped distribution around it whose *spread* (not just a
        # flat temperature) is directly tied to how decisive the underlying
        # signal is: close to the maximally-ambiguous 0.5 (rule didn't
        # clearly fire either way) -> wide spread, low resulting score
        # confidence; close to 0 or 1 (a clearly unsupported or clearly
        # supported signal) -> narrow spread, high resulting score
        # confidence. This keeps the Score question's confidence formula
        # output monotonic in the underlying rule-engine signal's
        # decisiveness, rather than an artifact of how close
        # top_confidence happens to land to one of the n discrete rubric
        # levels.
        location = top_confidence * (n - 1)
        decisiveness = abs(top_confidence - 0.5) * 2.0
        max_stddev = (n - 1) / 2.0
        stddev = max(0.15, (1.0 - decisiveness) * max_stddev)
        scores = {str(k): -((k - location) ** 2) / (2.0 * stddev**2) for k in range(n)}
        probabilities = self._softmax_with_temperature(scores, temperature=1.0)
        ordered = [probabilities[str(k)] for k in range(n)]
        score = compute_score(ordered)
        confidence = compute_score_confidence(ordered)
        return ScoreAnswer(score=score, probabilities=probabilities, confidence=confidence)


def get_default_jev_client() -> JevClient:
    """Returns :class:`TypeSafeJevClient` if ``TYPESAFE_API_KEY`` is set in
    the environment, else :class:`LocalJevStandIn`. This is the one line
    that changes when moving from offline/CI use to the real hosted model.
    """
    if os.environ.get("TYPESAFE_API_KEY"):
        return TypeSafeJevClient()
    return LocalJevStandIn()

"""Deterministic jury "judges" used by default (CI-safe, no external calls),
plus a documented, pluggable interface for swapping in a real small/local
LLM-as-judge model.

Design rationale (kept honest, no invented benchmark numbers): a panel of
*independent* judges that each look at the same evidence from a different
angle reduces the single-point-of-failure bias/variance of asking one
model (or one rule) "do you believe this?" once. Three cheap, fast,
deterministic heuristics below intentionally disagree in different
situations -- e.g. the conservative judge is the only one that cares about
*optional* signals that didn't match -- so that real disagreement in the
evidence shows up as visible dissent rather than being smoothed into a
single confident-sounding number.
"""

from __future__ import annotations

import abc

from evaluator.models import JudgeVerdict
from rules.features import EvidenceBundle
from rules.models import RuleVerdict


class Judge(abc.ABC):
    """Base interface every jury member implements.

    A production deployment can plug in a real judge by subclassing this
    and calling out to a small/local model; this repo ships only
    deterministic stub judges so tests and CI run fully offline. See
    ``LLMJudge`` below for the documented (unimplemented) extension point.
    """

    judge_id: str

    @abc.abstractmethod
    def evaluate(self, rule_verdict: RuleVerdict, bundle: EvidenceBundle) -> JudgeVerdict: ...


class RuleAlignmentJudge(Judge):
    """Mirrors the deterministic rule engine's own verdict: sanity-checks
    that the rule which fired actually fired, at its stated confidence."""

    judge_id = "rule_alignment"

    def evaluate(self, rule_verdict: RuleVerdict, bundle: EvidenceBundle) -> JudgeVerdict:
        return JudgeVerdict(
            judge_id=self.judge_id,
            supports=rule_verdict.matched,
            confidence=rule_verdict.confidence,
            rationale=(
                f"rule '{rule_verdict.rule_id}' matched={rule_verdict.matched} "
                f"with signals {rule_verdict.matched_signals}"
            ),
        )


class EvidenceCoverageJudge(Judge):
    """Looks only at the *fraction* of the rule's own signals that matched,
    independent of the rule's hard-coded base confidence."""

    judge_id = "evidence_coverage"

    def __init__(self, min_ratio: float = 0.6):
        self.min_ratio = min_ratio

    def evaluate(self, rule_verdict: RuleVerdict, bundle: EvidenceBundle) -> JudgeVerdict:
        total = len(rule_verdict.matched_signals) + len(rule_verdict.unmatched_signals)
        ratio = (len(rule_verdict.matched_signals) / total) if total else 0.0
        supports = ratio >= self.min_ratio
        return JudgeVerdict(
            judge_id=self.judge_id,
            supports=supports,
            confidence=ratio,
            rationale=(
                f"{len(rule_verdict.matched_signals)}/{total} signals matched "
                f"(ratio={ratio:.2f}, threshold={self.min_ratio})"
            ),
        )


class ConservativeSkepticJudge(Judge):
    """Deliberately stricter bar: only supports if *every* signal matched
    (including optional ones) and confidence clears a higher bar. This is
    the judge most likely to dissent on borderline matches, by design."""

    judge_id = "conservative_skeptic"

    def __init__(self, min_confidence: float = 0.8):
        self.min_confidence = min_confidence

    def evaluate(self, rule_verdict: RuleVerdict, bundle: EvidenceBundle) -> JudgeVerdict:
        all_matched = not rule_verdict.unmatched_signals
        supports = (
            rule_verdict.matched and all_matched and rule_verdict.confidence >= self.min_confidence
        )
        confidence = rule_verdict.confidence if supports else rule_verdict.confidence * 0.5
        rationale = (
            f"strict bar: all_signals_matched={all_matched}, "
            f"confidence={rule_verdict.confidence:.2f} (>= {self.min_confidence} required); "
            f"unmatched={rule_verdict.unmatched_signals}"
        )
        return JudgeVerdict(
            judge_id=self.judge_id, supports=supports, confidence=confidence, rationale=rationale
        )


class LLMJudge(Judge):
    """Documented, unimplemented extension point for a real small/local LLM
    acting as one jury member.

    Intentionally not implemented in this repo: wiring a real model would
    require network access or a bundled local model runtime, breaking the
    "fully offline, no external API keys" requirement for CI/tests. To add
    one, subclass this, implement ``evaluate`` to prompt the model with the
    rule verdict + the evidence feature summary (never raw tenant rows --
    only aggregated features/log line text already in scope), parse a
    structured verdict, and add an instance to ``JuryPanel(judges=[...])``.
    """

    judge_id = "llm_judge_stub"

    def evaluate(self, rule_verdict: RuleVerdict, bundle: EvidenceBundle) -> JudgeVerdict:
        raise NotImplementedError(
            "LLMJudge is a documented extension point, not implemented in this repo. "
            "Use the deterministic judges (RuleAlignmentJudge, EvidenceCoverageJudge, "
            "ConservativeSkepticJudge) for offline/CI use, or subclass LLMJudge."
        )


DEFAULT_JUDGES: list[Judge] = [
    RuleAlignmentJudge(),
    EvidenceCoverageJudge(),
    ConservativeSkepticJudge(),
]

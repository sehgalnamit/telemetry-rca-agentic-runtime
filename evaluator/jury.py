"""The jury panel: convenes N independent judges and aggregates their votes
into a single, dissent-transparent :class:`~evaluator.models.JuryVerdict`.
"""

from __future__ import annotations

from evaluator.judges import DEFAULT_JUDGES, Judge
from evaluator.models import JudgeVerdict, JuryVerdict
from rules.features import EvidenceBundle
from rules.models import RuleVerdict


class JuryPanel:
    def __init__(self, judges: list[Judge] | None = None):
        self.judges = judges if judges is not None else list(DEFAULT_JUDGES)

    def convene(self, rule_verdict: RuleVerdict, bundle: EvidenceBundle) -> JuryVerdict:
        votes: list[JudgeVerdict] = [j.evaluate(rule_verdict, bundle) for j in self.judges]

        support_count = sum(1 for v in votes if v.supports)
        total = len(votes) or 1
        consensus_supports = support_count * 2 > total  # strict majority
        agreement_ratio = (
            max(support_count, total - support_count) / total if votes else 0.0
        )
        mean_confidence = sum(v.confidence for v in votes) / total if votes else 0.0
        dissent_notes = [
            f"{v.judge_id}: {v.rationale}" for v in votes if v.supports != consensus_supports
        ]

        return JuryVerdict(
            votes=votes,
            consensus_supports=consensus_supports,
            mean_confidence=mean_confidence,
            agreement_ratio=agreement_ratio,
            dissent_notes=dissent_notes,
        )

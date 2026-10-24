"""LLM-jury evaluator gate: a panel of independent judges that votes on
whether the rule engine's top-ranked root-cause hypothesis is well-supported
by the gathered evidence, plus the logic that combines the rule-engine
confidence with the jury consensus into a final, auditable decision.
"""

from evaluator.decision import combine_decision
from evaluator.judges import (
    DEFAULT_JUDGES,
    ConservativeSkepticJudge,
    EvidenceCoverageJudge,
    Judge,
    LLMJudge,
    RuleAlignmentJudge,
)
from evaluator.jury import JuryPanel
from evaluator.models import FinalDecision, JudgeVerdict, JuryVerdict

__all__ = [
    "combine_decision",
    "DEFAULT_JUDGES",
    "ConservativeSkepticJudge",
    "EvidenceCoverageJudge",
    "Judge",
    "LLMJudge",
    "RuleAlignmentJudge",
    "JuryPanel",
    "FinalDecision",
    "JudgeVerdict",
    "JuryVerdict",
]

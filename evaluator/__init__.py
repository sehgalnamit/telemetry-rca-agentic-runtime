"""LLM-jury evaluator gate: a panel of independent judges that votes on
whether the rule engine's top-ranked root-cause hypothesis is well-supported
by the gathered evidence, plus the logic that combines the rule-engine
confidence with the jury consensus into a final, auditable decision.

Also includes the TypeSafe Jev "Confidence-Gated Routing" cascade
(``evaluator.jev_gate``, ``evaluator.cascade``): Jev gates hypotheses fast
and cheap when confident and in agreement with the rule engine, escalating
to the LLM-jury "appeals court" only when confidence is low/ambiguous or
Jev disagrees.
"""

from evaluator.cascade import CascadePath, CascadeResult, ConfidenceGatedCascade
from evaluator.decision import combine_decision
from evaluator.jev_gate import (
    ChoiceAnswer,
    ChoiceQuestion,
    JevAnswer,
    JevClient,
    JevQuestion,
    LocalJevStandIn,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    TypeSafeJevClient,
    compute_choice_confidence,
    compute_score,
    compute_score_confidence,
    get_default_jev_client,
)
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
    "CascadePath",
    "CascadeResult",
    "ConfidenceGatedCascade",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "JevAnswer",
    "JevClient",
    "JevQuestion",
    "LocalJevStandIn",
    "NoulAnswer",
    "NoulQuestion",
    "ScoreAnswer",
    "ScoreQuestion",
    "TypeSafeJevClient",
    "compute_choice_confidence",
    "compute_score",
    "compute_score_confidence",
    "get_default_jev_client",
]

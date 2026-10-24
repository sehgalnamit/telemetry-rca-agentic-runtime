"""Deterministic rule execution engine.

Loads a YAML-defined set of :class:`~rules.models.Rule` signatures and
evaluates each against an :class:`~rules.features.EvidenceBundle`, producing
fully explainable, reproducible :class:`~rules.models.RuleVerdict` objects
(no randomness, no model calls) -- the fast, auditable baseline signal that
is later combined with the LLM-jury consensus (see ``evaluator/``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from rules.features import EvidenceBundle, ServiceFeatures
from rules.models import Rule, RuleVerdict, SignalCondition

DEFAULT_RULES_PATH = Path(__file__).parent / "definitions" / "default_rules.yaml"


def load_rules(path: Optional[Path] = None) -> list[Rule]:
    path = path or DEFAULT_RULES_PATH
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [Rule.model_validate(r) for r in raw["rules"]]


class RuleEngine:
    def __init__(self, rules: Optional[list[Rule]] = None):
        self.rules = rules if rules is not None else load_rules()

    def evaluate(self, bundle: EvidenceBundle) -> list[RuleVerdict]:
        """Evaluate every loaded rule against the given evidence bundle.

        Returns one :class:`RuleVerdict` per rule (matched or not), sorted
        by confidence descending, so the caller can take the top result.
        """
        verdicts = [self._evaluate_rule(rule, bundle) for rule in self.rules]
        return sorted(verdicts, key=lambda v: v.confidence, reverse=True)

    def _evaluate_rule(self, rule: Rule, bundle: EvidenceBundle) -> RuleVerdict:
        matched: list[str] = []
        unmatched: list[str] = []
        veto = False

        for idx, signal in enumerate(rule.signals):
            label = f"{signal.type}:{signal.target}:{idx}"
            ok = self._evaluate_signal(signal, bundle)
            if ok:
                matched.append(label)
            else:
                unmatched.append(label)
                if signal.required:
                    veto = True

        total = len(rule.signals) or 1
        match_ratio = len(matched) / total
        fired = (not veto) and match_ratio >= rule.min_match_ratio
        confidence = rule.confidence * match_ratio if fired else 0.0

        rationale = (
            f"{len(matched)}/{total} signals matched for rule '{rule.id}' "
            f"(min_match_ratio={rule.min_match_ratio}); "
            f"matched={matched}; unmatched={unmatched}"
        )

        return RuleVerdict(
            rule_id=rule.id,
            root_cause_type=rule.root_cause_type,
            candidate_service=bundle.primary_label,
            matched=fired,
            confidence=confidence,
            matched_signals=matched,
            unmatched_signals=unmatched,
            rationale=rationale,
        )

    def _evaluate_signal(self, signal: SignalCondition, bundle: EvidenceBundle) -> bool:
        if signal.type == "peer_shared_node_degraded":
            return self._peer_shared_node_degraded(bundle, signal.gte or 1.5)

        targets = self._resolve_targets(signal.target, bundle)
        if not targets:
            return False

        checks = {
            "latency_ratio_gte": lambda f: f.latency_ratio >= (signal.gte or 0.0),
            "error_rate_incident_gte": lambda f: f.error_rate_incident >= (signal.gte or 0.0),
            "error_rate_delta_gte": lambda f: f.error_rate_delta >= (signal.gte or 0.0),
            "log_pattern": lambda f: f.matches_log_pattern(signal.pattern or "", signal.severity),
            "metric_trend_ratio_gte": lambda f: f.metric_trend_ratio(signal.metric_name or "")
            >= (signal.gte or 0.0),
            "metric_increasing": lambda f: f.metric_increasing(
                signal.metric_name or "", signal.factor_gte or 1.0
            ),
            "attribute_changed": lambda f: f.attribute_changed(signal.attribute or ""),
            "metric_incident_max_gte": lambda f: f.metric_incident_max.get(signal.metric_name or "", 0.0)
            >= (signal.gte or 0.0),
        }
        check = checks.get(signal.type)
        if check is None:
            raise ValueError(f"unsupported signal type: {signal.type}")

        # "primary" targets require all (exactly one) to match; "other"
        # targets match if ANY candidate service satisfies the condition.
        if signal.target == "primary":
            return all(check(f) for f in targets)
        return any(check(f) for f in targets)

    def _resolve_targets(self, target: str, bundle: EvidenceBundle) -> list[ServiceFeatures]:
        if target == "primary":
            f = bundle.primary_features()
            return [f] if f is not None else []
        return bundle.other_features()

    def _peer_shared_node_degraded(self, bundle: EvidenceBundle, gte: float) -> bool:
        primary = bundle.primary_features()
        if primary is None or primary.node is None:
            return False
        for svc, feat in bundle.services.items():
            if svc == bundle.primary_service:
                continue
            if feat.node != primary.node:
                continue
            if bundle.primary_service and bundle.are_connected(bundle.primary_service, svc):
                continue
            if feat.latency_ratio >= gte:
                return True
        return False

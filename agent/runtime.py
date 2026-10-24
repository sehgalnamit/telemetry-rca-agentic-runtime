"""The RCA agent runtime: the agentic loop tying together hypothesis
generation, zero-copy federated evidence gathering, the deterministic rule
engine, the LLM-jury evaluator gate, and the hash-chained audit log.

Loop (per alert):
  (a) triage    -- normalize the incoming alert, derive baseline/incident
                   evidence windows, record it to the audit log.
  (b) hypothesize -- propose a bounded set of candidate root causes using
                   topology metadata (``agent.hypothesis``).
  (c) gather evidence -- issue scoped, read-only federated queries via
                   ``federation.query_layer`` (never copying tenant rows)
                   and turn them into :class:`~rules.features.EvidenceBundle`
                   objects.
  (d) rank hypotheses -- run the deterministic rule engine over each
                   bundle and rank by resulting confidence.
  (e) validation gate -- either the jury alone (``investigate()``) or the
                   TypeSafe Jev confidence-gated cascade, which only falls
                   back to the jury "appeals court" when Jev's confidence
                   is low/ambiguous or it disagrees with the rule engine
                   (``investigate_with_cascade()``).
  (f) decide      -- combine rule + validation-gate output into a final action.
  (g) audit       -- every step above is appended to the hash-chained log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from agent.hypothesis import generate_hypotheses
from agent.models import Alert, Hypothesis
from audit.log_store import AuditLogStore
from audit.models import AuditEntryType
from evaluator.cascade import CascadeResult, ConfidenceGatedCascade
from evaluator.decision import combine_decision
from evaluator.jury import JuryPanel
from evaluator.models import FinalDecision, JuryVerdict
from federation.access_control import AccessScope
from federation.query_layer import FederationQueryEngine
from otel_mock.models import TenantTopology
from rules.engine import RuleEngine
from rules.features import EvidenceBundle, build_evidence_bundle
from rules.models import RuleVerdict


@dataclass
class HypothesisAssessment:
    hypothesis: Hypothesis
    rule_verdict: RuleVerdict
    bundle: EvidenceBundle


@dataclass
class PipelineResult:
    alert: Alert
    assessments: list[HypothesisAssessment]
    top_assessment: HypothesisAssessment
    jury_verdict: JuryVerdict
    final_decision: FinalDecision
    audit_log: AuditLogStore


@dataclass
class CascadePipelineResult:
    alert: Alert
    assessments: list[HypothesisAssessment]
    top_assessment: HypothesisAssessment
    cascade_result: CascadeResult
    audit_log: AuditLogStore


class RCAAgentRuntime:
    def __init__(
        self,
        query_engine: FederationQueryEngine,
        topology_catalog: dict[str, TenantTopology],
        rule_engine: RuleEngine | None = None,
        jury: JuryPanel | None = None,
        audit_log: AuditLogStore | None = None,
        incident_half_window: timedelta = timedelta(minutes=2),
        baseline_duration: timedelta = timedelta(minutes=1, seconds=30),
        baseline_gap: timedelta = timedelta(seconds=30),
    ):
        self.query_engine = query_engine
        self.topology_catalog = topology_catalog
        self.rule_engine = rule_engine or RuleEngine()
        self.jury = jury or JuryPanel()
        self.audit_log = audit_log or AuditLogStore()
        self.incident_half_window = incident_half_window
        self.baseline_duration = baseline_duration
        self.baseline_gap = baseline_gap

    def _triage_and_rank(self, alert: Alert, scope: AccessScope) -> list[HypothesisAssessment]:
        """Steps (a)-(d): triage, hypothesize, gather evidence, rank.
        Appends ALERT_RECEIVED, HYPOTHESIS, EVIDENCE_QUERY, and
        RULE_VERDICT audit entries. Shared by ``investigate()`` (jury-only)
        and ``investigate_with_cascade()`` (Jev confidence-gated cascade)."""
        topology = self.topology_catalog[alert.tenant_id]

        # (a) triage
        self.audit_log.append(
            AuditEntryType.ALERT_RECEIVED,
            tenant_id=alert.tenant_id,
            payload=alert.model_dump(mode="json"),
        )
        incident_start = alert.timestamp - self.incident_half_window
        incident_end = alert.timestamp + self.incident_half_window
        baseline_end = incident_start - self.baseline_gap
        baseline_start = baseline_end - self.baseline_duration

        # (b) hypothesize
        hypotheses = generate_hypotheses(topology, alert)
        for h in hypotheses:
            self.audit_log.append(
                AuditEntryType.HYPOTHESIS, tenant_id=alert.tenant_id, payload=h.model_dump(mode="json")
            )

        # (c) gather evidence once per tenant/window (never per-row copied;
        # services are sliced in-memory from these federated results).
        b_logs, b_logs_desc = self.query_engine.query_logs(
            scope, tenant_ids=[alert.tenant_id], start=baseline_start, end=baseline_end
        )
        b_traces, b_traces_desc = self.query_engine.query_traces(
            scope, tenant_ids=[alert.tenant_id], start=baseline_start, end=baseline_end
        )
        b_metrics, b_metrics_desc = self.query_engine.query_metrics(
            scope, tenant_ids=[alert.tenant_id], start=baseline_start, end=baseline_end
        )
        i_logs, i_logs_desc = self.query_engine.query_logs(
            scope, tenant_ids=[alert.tenant_id], start=incident_start, end=incident_end
        )
        i_traces, i_traces_desc = self.query_engine.query_traces(
            scope, tenant_ids=[alert.tenant_id], start=incident_start, end=incident_end
        )
        i_metrics, i_metrics_desc = self.query_engine.query_metrics(
            scope, tenant_ids=[alert.tenant_id], start=incident_start, end=incident_end
        )
        for desc in (b_logs_desc, b_traces_desc, b_metrics_desc, i_logs_desc, i_traces_desc, i_metrics_desc):
            self.audit_log.append(
                AuditEntryType.EVIDENCE_QUERY, tenant_id=alert.tenant_id, payload=desc.model_dump()
            )

        dependency_edges = {
            (a, b) for a, deps in topology.dependencies.items() for b in deps
        }

        # (d) rank hypotheses by evidence strength
        assessments: list[HypothesisAssessment] = []
        for h in hypotheses:
            bundle = build_evidence_bundle(
                tenant_id=alert.tenant_id,
                primary_service=h.candidate_service,
                primary_label=h.candidate_label,
                services=h.services_to_investigate,
                baseline_start=baseline_start,
                baseline_end=baseline_end,
                incident_start=incident_start,
                incident_end=incident_end,
                baseline_logs=b_logs,
                baseline_traces=b_traces,
                baseline_metrics=b_metrics,
                incident_logs=i_logs,
                incident_traces=i_traces,
                incident_metrics=i_metrics,
                dependency_edges=dependency_edges,
            )
            verdicts = self.rule_engine.evaluate(bundle)
            matching = [v for v in verdicts if v.root_cause_type == h.root_cause_type]
            verdict = matching[0] if matching else verdicts[0]
            self.audit_log.append(
                AuditEntryType.RULE_VERDICT,
                tenant_id=alert.tenant_id,
                payload={"hypothesis_id": h.hypothesis_id, **verdict.model_dump()},
            )
            assessments.append(HypothesisAssessment(hypothesis=h, rule_verdict=verdict, bundle=bundle))

        assessments.sort(key=lambda a: a.rule_verdict.confidence, reverse=True)
        return assessments

    def investigate(self, alert: Alert, scope: AccessScope) -> PipelineResult:
        assessments = self._triage_and_rank(alert, scope)
        top = assessments[0]

        # (e) jury gate on the top-ranked hypothesis
        jury_verdict = self.jury.convene(top.rule_verdict, top.bundle)
        self.audit_log.append(
            AuditEntryType.JURY_VERDICT,
            tenant_id=alert.tenant_id,
            payload={"hypothesis_id": top.hypothesis.hypothesis_id, **jury_verdict.model_dump()},
        )

        # (f) combine into a final decision
        final_decision = combine_decision(
            top.rule_verdict,
            jury_verdict,
            root_cause_type=top.hypothesis.root_cause_type,
            candidate_label=top.hypothesis.candidate_label,
        )
        self.audit_log.append(
            AuditEntryType.FINAL_DECISION,
            tenant_id=alert.tenant_id,
            payload={"hypothesis_id": top.hypothesis.hypothesis_id, **final_decision.model_dump()},
        )

        return PipelineResult(
            alert=alert,
            assessments=assessments,
            top_assessment=top,
            jury_verdict=jury_verdict,
            final_decision=final_decision,
            audit_log=self.audit_log,
        )

    def investigate_with_cascade(
        self, alert: Alert, scope: AccessScope, cascade: ConfidenceGatedCascade
    ) -> CascadePipelineResult:
        """Same steps (a)-(d) as :meth:`investigate`, but validates the top
        hypothesis via TypeSafe Jev's confidence-gated routing instead of
        always convening the jury -- see ``evaluator.cascade`` for the
        full cascade logic. Which path was taken (Jev-only vs
        Jev+jury-appeal) is recorded in the ``JEV_VERDICT`` audit entry's
        payload, alongside a ``JURY_VERDICT`` entry only when the jury was
        actually invoked."""
        assessments = self._triage_and_rank(alert, scope)
        top = assessments[0]

        cascade_result = cascade.evaluate(assessments)

        self.audit_log.append(
            AuditEntryType.JEV_VERDICT,
            tenant_id=alert.tenant_id,
            payload={
                "hypothesis_id": top.hypothesis.hypothesis_id,
                "path": cascade_result.path.value,
                "jev_agrees_with_rule_engine": cascade_result.jev_agrees_with_rule_engine,
                "jev_high_confidence": cascade_result.jev_high_confidence,
                "choice_answer": cascade_result.jev_choice_answer.model_dump(),
                "score_answer": cascade_result.jev_score_answer.model_dump(),
                "rationale": cascade_result.rationale,
            },
        )
        if cascade_result.jury_verdict is not None:
            self.audit_log.append(
                AuditEntryType.JURY_VERDICT,
                tenant_id=alert.tenant_id,
                payload={
                    "hypothesis_id": top.hypothesis.hypothesis_id,
                    **cascade_result.jury_verdict.model_dump(),
                },
            )
        self.audit_log.append(
            AuditEntryType.FINAL_DECISION,
            tenant_id=alert.tenant_id,
            payload={
                "hypothesis_id": top.hypothesis.hypothesis_id,
                "path": cascade_result.path.value,
                **cascade_result.final_decision.model_dump(),
            },
        )

        return CascadePipelineResult(
            alert=alert,
            assessments=assessments,
            top_assessment=top,
            cascade_result=cascade_result,
            audit_log=self.audit_log,
        )

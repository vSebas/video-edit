from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


class ReviewOutcomeError(ValueError):
    pass


def load_benchmark_findings(path: Path, project_id: str) -> dict:
    if not path.is_file():
        return {
            "schema_version": "provider-benchmark-findings.v1",
            "project_id": project_id,
            "comparison": {},
            "runs": {},
            "findings": [],
        }
    with path.open(encoding="utf-8") as handle:
        result = json.load(handle)
    if result.get("project_id") != project_id:
        return {
            "schema_version": "provider-benchmark-findings.v1",
            "project_id": project_id,
            "comparison": {},
            "runs": {},
            "findings": [],
        }
    return result


def build_review_outcome(
    project_id: str,
    runs: list[dict],
    benchmark: dict,
    generated_at: str,
) -> dict:
    if not runs:
        raise ReviewOutcomeError("At least one reviewed provider run is required")

    for run in runs:
        pending = run.get("summary", {}).get("pending_review_count")
        if run.get("review_status") != "reviewed" or pending != 0:
            raise ReviewOutcomeError(
                f"Provider run is not fully reviewed: {run.get('run_key') or run.get('run_id')}"
            )

    findings_by_evidence: dict[tuple[str, str], list[dict]] = {}
    for finding in benchmark.get("findings", []):
        key = (finding["run_key"], finding["evidence_id"])
        findings_by_evidence.setdefault(key, []).append(finding)

    candidate_sets = []
    all_conflicts = []
    revision_sources = []
    for run in sorted(runs, key=lambda item: item["run_key"]):
        run_key = run["run_key"]
        accepted = []
        rejected = []
        run_conflicts = []
        flagged_approved_count = 0
        edited_count = 0
        for observation in run["observations"]:
            if observation.get("normalization_status") != "accepted":
                continue
            review_status = observation.get("review_status", "pending")
            if review_status == "reviewed":
                reviewed_caption = observation.get("reviewed_caption") or observation["caption"]
                if reviewed_caption != observation["caption"]:
                    edited_count += 1
                evidence_findings = findings_by_evidence.get(
                    (run_key, observation["evidence_id"]), []
                )
                evidence_findings = [
                    finding
                    for finding in evidence_findings
                    if not finding.get("trigger_terms")
                    or any(
                        term.lower() in reviewed_caption.lower()
                        for term in finding["trigger_terms"]
                    )
                ]
                conflict_ids = []
                for finding in evidence_findings:
                    conflict = {
                        "finding_id": finding["finding_id"],
                        "run_key": run_key,
                        "evidence_id": observation["evidence_id"],
                        "provider_id": run["provider"]["id"],
                        "asset_id": observation["asset_id"],
                        "filename": observation["filename"],
                        "start_seconds": observation["start_seconds"],
                        "end_seconds": observation["end_seconds"],
                        "severity": finding["severity"],
                        "status": "unresolved",
                        "summary": finding["summary"],
                        "verified_observation": finding["verified_observation"],
                        "verification_source": finding["verification_source"],
                    }
                    conflict_ids.append(conflict["finding_id"])
                    run_conflicts.append(conflict)
                    all_conflicts.append(conflict)
                risks = observation.get("risk_flags", [])
                if risks:
                    flagged_approved_count += 1
                accepted.append(
                    {
                        "evidence_id": observation["evidence_id"],
                        "asset_id": observation["asset_id"],
                        "filename": observation["filename"],
                        "start_seconds": observation["start_seconds"],
                        "end_seconds": observation["end_seconds"],
                        "observation": reviewed_caption,
                        "original_observation": observation["caption"],
                        "review_note": observation.get("review_note"),
                        "reviewed_at": observation.get("reviewed_at"),
                        "adjustments": observation.get("adjustments", []),
                        "risk_flags": risks,
                        "conflict_ids": conflict_ids,
                    }
                )
            elif review_status == "rejected":
                rejected.append(
                    {
                        "evidence_id": observation["evidence_id"],
                        "asset_id": observation["asset_id"],
                        "filename": observation["filename"],
                        "start_seconds": observation["start_seconds"],
                        "end_seconds": observation["end_seconds"],
                        "original_observation": observation["caption"],
                        "review_note": observation.get("review_note"),
                        "reviewed_at": observation.get("reviewed_at"),
                    }
                )

        summary = run["summary"]
        benchmark_run = benchmark.get("runs", {}).get(run_key, {})
        candidate_sets.append(
            {
                "run_key": run_key,
                "run_id": run["run_id"],
                "provider": run["provider"],
                "review": {
                    "status": "reviewed",
                    "approved_count": len(accepted),
                    "rejected_count": len(rejected),
                    "edited_count": edited_count,
                    "approval_rate": round(
                        len(accepted) / max(len(accepted) + len(rejected), 1), 4
                    ),
                },
                "quality_signals": {
                    "clamped_count": summary.get("clamped_count", 0),
                    "risk_flagged_count": summary.get("risk_flagged_count", 0),
                    "flagged_approved_count": flagged_approved_count,
                    "material_conflict_count": len(run_conflicts),
                },
                "benchmark": {
                    "split_count": benchmark_run.get("split_count"),
                    "min_shot_duration_ms": benchmark_run.get("min_shot_duration_ms"),
                    "max_shot_duration_ms": benchmark_run.get("max_shot_duration_ms"),
                    "visual_seconds": benchmark_run.get("visual_seconds"),
                    "end_to_end_seconds": benchmark_run.get("end_to_end_seconds"),
                },
                "eligible_for_planning": len(run_conflicts) == 0 and bool(accepted),
                "accepted_evidence": accepted,
                "rejected_evidence": rejected,
                "material_conflicts": run_conflicts,
            }
        )
        revision_sources.append(
            {
                "run_key": run_key,
                "accepted": accepted,
                "rejected": rejected,
                "benchmark": benchmark_run,
            }
        )

    comparison_config = benchmark.get("comparison", {})
    directly_comparable = bool(
        comparison_config.get("directly_comparable", len(candidate_sets) < 2)
    )
    if all_conflicts:
        recommendation_status = "conflicts_require_resolution"
        recommendation_reason = (
            "Approved evidence conflicts with independently verified observations."
        )
    elif len(candidate_sets) == 1:
        recommendation_status = "provider_selection_required"
        recommendation_reason = (
            "Only one reviewed provider candidate is present; finalization does not select it automatically."
        )
    elif not directly_comparable:
        recommendation_status = "deterministic_rerun_required"
        recommendation_reason = comparison_config.get(
            "reason",
            "The provider runs did not use the same deterministic shot boundaries.",
        )
    else:
        recommendation_status = "provider_selection_required"
        recommendation_reason = (
            "The evidence is finalized, but no provider has been selected as the planning source."
        )

    revision_payload = {
        "project_id": project_id,
        "sources": revision_sources,
        "findings": benchmark.get("findings", []),
        "comparison": comparison_config,
    }
    revision_id = hashlib.sha256(
        json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    planning_eligible = (
        recommendation_status == "provider_selection_required"
        and any(item["eligible_for_planning"] for item in candidate_sets)
    )
    return {
        "schema_version": "reviewed-evidence-set.v1",
        "revision_id": revision_id,
        "generated_at": generated_at,
        "project_id": project_id,
        "status": recommendation_status,
        "planning_eligible": planning_eligible,
        "recommendation": {
            "status": recommendation_status,
            "winner_run_key": None,
            "reason": recommendation_reason,
            "next_action": (
                "Re-review the listed conflicts, then rerun both providers on identical owned ranges."
                if all_conflicts
                else "Select an eligible provider or run a deterministic comparison."
            ),
        },
        "comparison": {
            "directly_comparable": directly_comparable,
            "reason": comparison_config.get("reason"),
            "candidate_count": len(candidate_sets),
        },
        "candidate_sets": candidate_sets,
        "material_conflicts": all_conflicts,
        "warnings": [
            "Human approval is preserved, but an unresolved independent conflict prevents automatic promotion."
        ]
        if all_conflicts
        else [],
    }


def validate_review_outcome(value: dict, schema_path: Path) -> None:
    with schema_path.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path)
    )
    if errors:
        detail = "; ".join(error.message for error in errors[:5])
        raise ReviewOutcomeError(f"Finalized review outcome is invalid: {detail}")

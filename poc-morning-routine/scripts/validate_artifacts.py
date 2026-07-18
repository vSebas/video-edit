#!/usr/bin/env python3
import json
from pathlib import Path

import jsonschema


POC_ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_schema(schema_path: Path, artifact_path: Path) -> None:
    schema = load_json(schema_path)
    artifact = load_json(artifact_path)
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    validator.validate(artifact)
    print(f"VALID\t{artifact_path.relative_to(POC_ROOT)}")


def validate_ranges() -> None:
    inventory = load_json(POC_ROOT / "artifacts/media-inventory.json")
    durations = {
        asset["asset_id"]: asset["duration_seconds"]
        for asset in inventory["assets"]
    }

    concepts = load_json(POC_ROOT / "artifacts/creative-concepts.json")
    for concept in concepts["concepts"]:
        beat_total = sum(
            beat["target_duration_seconds"] for beat in concept["structure"]
        )
        if abs(beat_total - concept["target_duration_seconds"]) > 1e-9:
            raise ValueError(
                f"Beat duration mismatch for {concept['concept_id']}: {beat_total}"
            )
        for beat in concept["structure"]:
            for evidence in beat["evidence"]:
                duration = durations[evidence["asset_id"]]
                start = evidence["start_seconds"]
                end = evidence["end_seconds"]
                if not 0 <= start < end <= duration:
                    raise ValueError(
                        f"Out-of-range concept evidence in {concept['concept_id']}: "
                        f"{evidence}"
                    )
        print(
            f"RANGES_OK\t{concept['concept_id']}\t"
            f"{concept['target_duration_seconds']}s"
        )

    for analysis_path in sorted(
        (POC_ROOT / "artifacts/assets").glob("*/analysis.json")
    ):
        analysis = load_json(analysis_path)
        duration = analysis["technical"]["duration_seconds"]
        for observation in analysis["semantic_observations"]:
            start = observation["start_seconds"]
            end = observation["end_seconds"]
            if not 0 <= start < end <= duration:
                raise ValueError(
                    f"Out-of-range semantic observation in {analysis_path}: "
                    f"{observation}"
                )
        print(
            f"OBSERVATIONS_OK\t{analysis['asset_id']}\t"
            f"{len(analysis['semantic_observations'])}"
        )


def main() -> None:
    validate_schema(
        POC_ROOT / "schemas/creative-concepts.schema.json",
        POC_ROOT / "artifacts/creative-concepts.json",
    )
    for analysis_path in sorted(
        (POC_ROOT / "artifacts/assets").glob("*/analysis.json")
    ):
        validate_schema(
            POC_ROOT / "schemas/clip-analysis.schema.json",
            analysis_path,
        )
    validate_schema(
        POC_ROOT / "schemas/edit-plan.schema.json",
        POC_ROOT / "artifacts/edit-plan.json",
    )
    validate_ranges()


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
poc_root=$(cd -- "$script_dir/.." && pwd)
observations_file="$poc_root/semantic/verified-observations.json"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

for analysis_file in "$poc_root"/artifacts/assets/*/analysis.json; do
  asset_id=$(jq -r '.asset_id' "$analysis_file")
  observation_count=$(jq --arg asset_id "$asset_id" '.assets[$asset_id] | length' "$observations_file")

  if [[ "$observation_count" == "0" || "$observation_count" == "null" ]]; then
    echo "No reviewed observations for $asset_id" >&2
    exit 1
  fi

  temporary_file=$(mktemp)
  jq \
    --arg asset_id "$asset_id" \
    --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --slurpfile reviewed "$observations_file" \
    '.generated_at = $generated_at
     | .semantic_observations = $reviewed[0].assets[$asset_id]
     | .warnings = ([.warnings[] | select(. != "Semantic video understanding and ASR have not yet been run.")]
       + ["ASR has not been run; no spoken-content claims are used in the concepts.",
          "Semantic observations were checked with targeted still frames; precise motion boundaries should be confirmed during timeline validation."]
       | unique)' \
    "$analysis_file" > "$temporary_file"
  mv "$temporary_file" "$analysis_file"
done

echo "Applied reviewed semantic observations to seven clip-analysis artifacts."

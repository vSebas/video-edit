#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
video_editing_dir=$(cd "$project_dir/.." && pwd)
source_root=${1:-"$video_editing_dir/Crayotter/crayotter-data/user_temp"}
source_root=$(realpath "$source_root")
artifact_root="$project_dir/artifacts"
assets_root="$artifact_root/assets"
inventory_rows="$artifact_root/media-inventory.assets.jsonl"
report_rows="$artifact_root/index-report.rows.md"

scene_threshold=${SCENE_THRESHOLD:-0.45}
minimum_scene_gap=${MINIMUM_SCENE_GAP_SECONDS:-1.0}
uniform_sample_count=${UNIFORM_SAMPLE_COUNT:-8}
silence_threshold_db=${SILENCE_THRESHOLD_DB:--35}
silence_minimum_duration=${SILENCE_MINIMUM_DURATION_SECONDS:-0.8}

openclaw_bin=$(command -v openclaw || true)
if [[ -n "$openclaw_bin" ]]; then
  frame_script=$(realpath "$(dirname "$openclaw_bin")/../lib/node_modules/openclaw/skills/video-frames/scripts/frame.sh")
else
  npm_global_root=$(npm root -g)
  frame_script="$npm_global_root/openclaw/skills/video-frames/scripts/frame.sh"
fi

source_names=(
  IMG_0991.mp4
  IMG_0993.mp4
  IMG_0994.mp4
  IMG_0995.mp4
  IMG_0996.mp4
  IMG_0997.mp4
  IMG_0999.mp4
)

for required_command in ffmpeg ffprobe jq sha256sum montage realpath npm; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$required_command" >&2
    exit 1
  fi
done

if [[ ! -f "$frame_script" ]]; then
  printf 'Video frame helper not found: %s\n' "$frame_script" >&2
  exit 1
fi

mkdir -p "$assets_root"
: > "$inventory_rows"
: > "$report_rows"
generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

for source_name in "${source_names[@]}"; do
  source_path="$source_root/$source_name"
  if [[ ! -f "$source_path" ]]; then
    printf 'Missing benchmark source: %s\n' "$source_path" >&2
    exit 1
  fi

  asset_id=$(basename "$source_name" .mp4 | tr '[:upper:]' '[:lower:]')
  asset_dir="$assets_root/$asset_id"
  keyframe_dir="$asset_dir/keyframes"
  mkdir -p "$keyframe_dir"

  probe_path="$asset_dir/probe.json"
  ffprobe -v error -show_format -show_streams -of json "$source_path" > "$probe_path"

  duration_seconds=$(jq -r '.format.duration | tonumber' "$probe_path")
  size_bytes=$(stat -c '%s' "$source_path")
  sha256=$(sha256sum "$source_path" | awk '{print $1}')
  has_audio=$(jq '[.streams[] | select(.codec_type == "audio")] | length > 0' "$probe_path")

  scene_metadata="$asset_dir/scene-metadata.txt"
  scene_candidates="$asset_dir/scene-candidates.csv"
  scene_boundaries="$asset_dir/scene-boundaries.csv"
  scene_index="$asset_dir/scenes.json"

  : > "$scene_metadata"
  ffmpeg -hide_banner -loglevel error -i "$source_path" -an \
    -vf "select='gt(scene,$scene_threshold)',metadata=print:file='$scene_metadata'" \
    -fps_mode vfr -f null -

  awk '
    /pts_time:/ {
      line = $0
      sub(/^.*pts_time:/, "", line)
      split(line, parts, /[[:space:]]+/)
      timestamp = parts[1]
    }
    /lavfi.scene_score=/ {
      line = $0
      sub(/^.*=/, "", line)
      print timestamp "," line
    }
  ' "$scene_metadata" > "$scene_candidates"

  awk -F, -v minimum_gap="$minimum_scene_gap" '
    NF >= 2 && (accepted == 0 || ($1 - previous) >= minimum_gap) {
      print $1 "," $2
      previous = $1
      accepted += 1
      if (accepted >= 32) exit
    }
  ' "$scene_candidates" > "$scene_boundaries"

  jq -Rn \
    --argjson duration "$duration_seconds" \
    --argjson threshold "$scene_threshold" '
      [inputs
        | select(length > 0)
        | split(",")
        | {time_seconds: (.[0] | tonumber), score: (.[1] | tonumber)}
      ] as $detected
      | ([{time_seconds: 0, score: null}]
          + ($detected | map(select(.time_seconds > 0 and .time_seconds < $duration)))
          + [{time_seconds: $duration, score: null}]) as $points
      | {
          schema_version: "scene-index.v1",
          detector: "ffmpeg_scene_score",
          threshold: $threshold,
          scenes: [
            range(0; ($points | length) - 1) as $index
            | {
                scene_id: ("scene_" + (($index + 1) | tostring)),
                start_seconds: $points[$index].time_seconds,
                end_seconds: $points[$index + 1].time_seconds,
                duration_seconds: ($points[$index + 1].time_seconds - $points[$index].time_seconds),
                boundary_score: $points[$index].score
              }
          ]
        }
    ' "$scene_boundaries" > "$scene_index"

  keyframe_points="$asset_dir/keyframe-points.json"
  jq \
    --argjson duration "$duration_seconds" \
    --argjson sample_count "$uniform_sample_count" '
      ([range(0; $sample_count)
        | {
            timestamp_seconds: (($duration * (. + 0.5)) / $sample_count),
            kind: "uniform"
          }
      ] + [
        .scenes[]
        | {
            timestamp_seconds: ((.start_seconds + .end_seconds) / 2),
            kind: "scene_midpoint"
          }
      ])
      | sort_by(.timestamp_seconds)
      | group_by((.timestamp_seconds * 10 | floor))
      | map({
          timestamp_seconds: (map(.timestamp_seconds) | add / length),
          selection_reason: (
            if (any(.[]; .kind == "uniform") and any(.[]; .kind == "scene_midpoint"))
            then "uniform_and_scene_midpoint"
            elif any(.[]; .kind == "scene_midpoint")
            then "scene_midpoint"
            else "uniform"
            end
          )
        })
    ' "$scene_index" > "$keyframe_points"

  keyframe_rows="$asset_dir/keyframes.jsonl"
  keyframes_json="$asset_dir/keyframes.json"
  : > "$keyframe_rows"
  frame_number=0

  while IFS=$'\t' read -r timestamp selection_reason; do
    frame_number=$((frame_number + 1))
    timestamp_label=$(printf '%010.3f' "$timestamp" | tr '.' '_')
    frame_filename=$(printf '%03d_%s.jpg' "$frame_number" "$timestamp_label")
    frame_path="$keyframe_dir/$frame_filename"
    bash "$frame_script" "$source_path" --time "$timestamp" --out "$frame_path" >/dev/null
    relative_frame_path=$(realpath --relative-to="$project_dir" "$frame_path")
    jq -nc \
      --argjson timestamp "$timestamp" \
      --arg path "$relative_frame_path" \
      --arg reason "$selection_reason" \
      '{timestamp_seconds: $timestamp, path: $path, selection_reason: $reason}' \
      >> "$keyframe_rows"
  done < <(jq -r '.[] | [.timestamp_seconds, .selection_reason] | @tsv' "$keyframe_points")

  jq -s '.' "$keyframe_rows" > "$keyframes_json"

  mapfile -t current_keyframes < <(jq -r --arg project "$project_dir" '.[] | $project + "/" + .path' "$keyframes_json")
  contact_sheet="$asset_dir/contact-sheet.jpg"
  montage "${current_keyframes[@]}" \
    -thumbnail 320x320 \
    -background '#111111' \
    -gravity center \
    -extent 320x320 \
    -tile 4x \
    -geometry +6+6 \
    "$contact_sheet"

  audio_log="$asset_dir/audio-analysis.log"
  silence_rows="$asset_dir/silence-intervals.tsv"
  silences_json="$asset_dir/silence-intervals.json"
  : > "$audio_log"
  : > "$silence_rows"

  if [[ "$has_audio" == "true" ]]; then
    ffmpeg -hide_banner -nostats -i "$source_path" \
      -af "silencedetect=noise=${silence_threshold_db}dB:d=${silence_minimum_duration},volumedetect" \
      -f null - > /dev/null 2> "$audio_log"

    mean_volume=$(sed -n 's/^.*mean_volume: \([^ ]*\) dB.*$/\1/p' "$audio_log" | tail -n 1)
    max_volume=$(sed -n 's/^.*max_volume: \([^ ]*\) dB.*$/\1/p' "$audio_log" | tail -n 1)

    awk -v total="$duration_seconds" '
      /silence_start:/ {
        line = $0
        sub(/^.*silence_start: /, "", line)
        start = line + 0
        open = 1
      }
      /silence_end:/ {
        end_line = $0
        sub(/^.*silence_end: /, "", end_line)
        sub(/ \|.*$/, "", end_line)
        duration_line = $0
        sub(/^.*silence_duration: /, "", duration_line)
        print start "\t" (end_line + 0) "\t" (duration_line + 0)
        open = 0
      }
      END {
        if (open == 1) print start "\t" total "\t" (total - start)
      }
    ' "$audio_log" > "$silence_rows"
  else
    mean_volume=""
    max_volume=""
  fi

  jq -Rn '[
    inputs
    | select(length > 0)
    | split("\t")
    | {
        start_seconds: (.[0] | tonumber),
        end_seconds: (.[1] | tonumber),
        duration_seconds: (.[2] | tonumber)
      }
  ]' "$silence_rows" > "$silences_json"

  if [[ "$mean_volume" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
    mean_volume_json=$mean_volume
  else
    mean_volume_json=null
  fi
  if [[ "$max_volume" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
    max_volume_json=$max_volume
  else
    max_volume_json=null
  fi

  analysis_path="$asset_dir/analysis.json"
  jq -n \
    --arg generated_at "$generated_at" \
    --arg asset_id "$asset_id" \
    --slurpfile probe "$probe_path" \
    --slurpfile scene_index "$scene_index" \
    --slurpfile keyframes "$keyframes_json" \
    --slurpfile silences "$silences_json" \
    --argjson duration "$duration_seconds" \
    --argjson has_audio "$has_audio" \
    --argjson mean_volume "$mean_volume_json" \
    --argjson max_volume "$max_volume_json" \
    --argjson silence_threshold "$silence_threshold_db" '
      (first($probe[0].streams[] | select(.codec_type == "video"))) as $video
      | {
          schema_version: "clip-analysis.v1",
          generated_at: $generated_at,
          asset_id: $asset_id,
          technical: {
            duration_seconds: $duration,
            width: $video.width,
            height: $video.height,
            average_frame_rate: $video.avg_frame_rate,
            has_audio: $has_audio
          },
          scenes: $scene_index[0].scenes,
          keyframes: $keyframes[0],
          audio_analysis: {
            status: (if $has_audio then "completed" else "no_audio" end),
            mean_volume_db: $mean_volume,
            max_volume_db: $max_volume,
            silence_threshold_db: $silence_threshold,
            silence_intervals: $silences[0]
          },
          transcript: {
            status: "unavailable",
            backend: null,
            reason: "No local ASR backend is installed for this benchmark run.",
            language: null,
            segments: []
          },
          semantic_observations: [],
          warnings: (
            ["Semantic video understanding and ASR have not yet been run."]
            + (if $video.width > $video.height
               then ["Landscape source requires rotation and/or vertical reframe review."]
               else [] end)
            + (if $mean_volume != null and $mean_volume < -40
               then ["Source audio is very quiet and may need replacement, gain, or intentional muting."]
               else [] end)
            + (if $max_volume != null and $max_volume > -1
               then ["Source audio peaks within 1 dB of full scale; inspect for clipping before gain changes."]
               else [] end)
          )
        }
    ' > "$analysis_path"

  relative_source_path=$(realpath --relative-to="$project_dir" "$source_path")
  relative_analysis_path=$(realpath --relative-to="$project_dir" "$analysis_path")
  relative_scene_index=$(realpath --relative-to="$project_dir" "$scene_index")
  relative_keyframe_dir=$(realpath --relative-to="$project_dir" "$keyframe_dir")
  relative_contact_sheet=$(realpath --relative-to="$project_dir" "$contact_sheet")
  relative_probe_path=$(realpath --relative-to="$project_dir" "$probe_path")

  jq -n \
    --arg asset_id "$asset_id" \
    --arg filename "$source_name" \
    --arg source_path "$relative_source_path" \
    --arg sha256 "$sha256" \
    --argjson size_bytes "$size_bytes" \
    --argjson duration "$duration_seconds" \
    --arg analysis_path "$relative_analysis_path" \
    --arg scene_index_path "$relative_scene_index" \
    --arg keyframe_directory "$relative_keyframe_dir" \
    --arg contact_sheet_path "$relative_contact_sheet" \
    --arg probe_path "$relative_probe_path" \
    --slurpfile probe "$probe_path" '
      (first($probe[0].streams[] | select(.codec_type == "video"))) as $video
      | (first($probe[0].streams[] | select(.codec_type == "audio"))) as $audio
      | {
          asset_id: $asset_id,
          filename: $filename,
          source_path: $source_path,
          sha256: $sha256,
          size_bytes: $size_bytes,
          duration_seconds: $duration,
          format_names: (($probe[0].format.format_name // "") | split(",") | map(select(length > 0))),
          bit_rate: (($probe[0].format.bit_rate // null) | if . == null then null else tonumber end),
          video: {
            codec: $video.codec_name,
            width: $video.width,
            height: $video.height,
            pixel_format: ($video.pix_fmt // null),
            average_frame_rate: $video.avg_frame_rate,
            rotation_degrees: (($video.side_data_list // [] | map(select(has("rotation"))) | first.rotation) // 0)
          },
          audio: (
            if $audio == null then null
            else {
              codec: $audio.codec_name,
              sample_rate: ($audio.sample_rate | tonumber),
              channels: $audio.channels,
              channel_layout: ($audio.channel_layout // null)
            }
            end
          ),
          derived: {
            analysis_path: $analysis_path,
            scene_index_path: $scene_index_path,
            keyframe_directory: $keyframe_directory,
            contact_sheet_path: $contact_sheet_path,
            probe_path: $probe_path
          }
        }
    ' >> "$inventory_rows"

  scene_count=$(jq '.scenes | length' "$scene_index")
  keyframe_count=$(jq 'length' "$keyframes_json")
  dimensions=$(jq -r '[first(.streams[] | select(.codec_type == "video")) | .width, .height] | map(tostring) | join("×")' "$probe_path")
  frame_rate=$(jq -r 'first(.streams[] | select(.codec_type == "video")) | .avg_frame_rate' "$probe_path")
  printf '| `%s` | %.3f | %s | %s | %s | %s | %s |\n' \
    "$source_name" "$duration_seconds" "$dimensions" "$frame_rate" "$scene_count" "$keyframe_count" "$mean_volume_json" \
    >> "$report_rows"
done

mapfile -t asset_contact_sheets < <(find "$assets_root" -mindepth 2 -maxdepth 2 -name 'contact-sheet.jpg' -type f | sort)
overview_contact_sheet="$artifact_root/contact-sheet-overview.jpg"
montage "${asset_contact_sheets[@]}" \
  -thumbnail 640x640 \
  -background '#111111' \
  -gravity center \
  -extent 640x640 \
  -tile 2x \
  -geometry +10+10 \
  "$overview_contact_sheet"

inventory_path="$artifact_root/media-inventory.json"
jq -s '.' "$inventory_rows" > "$artifact_root/media-inventory.assets.json"
jq -n \
  --arg generated_at "$generated_at" \
  --arg source_root "$source_root" \
  --slurpfile assets "$artifact_root/media-inventory.assets.json" '
    {
      schema_version: "media-inventory.v1",
      generated_at: $generated_at,
      source_root: $source_root,
      assets: $assets[0]
    }
  ' > "$inventory_path"

total_duration=$(jq '[.assets[].duration_seconds] | add' "$inventory_path")
report_path="$artifact_root/INDEX_REPORT.md"
{
  printf '# Media Index Report\n\n'
  printf 'Generated: `%s`\n\n' "$generated_at"
  printf 'Source root: `%s`\n\n' "$source_root"
  printf 'Assets: **%s**  \n' "${#source_names[@]}"
  printf 'Combined source duration: **%.3f seconds**\n\n' "$total_duration"
  printf '| Asset | Duration (s) | Dimensions | Avg FPS | Scenes | Keyframes | Mean volume (dB) |\n'
  printf '|---|---:|---:|---:|---:|---:|---:|\n'
  cat "$report_rows"
  printf '\n## Index Scope\n\n'
  printf -- '- SHA-256 source identity and file metadata\n'
  printf -- '- FFmpeg scene-score boundaries (`threshold=%s`, minimum gap `%ss`)\n' "$scene_threshold" "$minimum_scene_gap"
  printf -- '- Uniform and scene-midpoint inspection frames with per-asset contact sheets\n'
  printf -- '- Combined visual overview: `contact-sheet-overview.jpg`\n'
  printf -- '- Audio mean/max volume and silence intervals below `%s dB` for at least `%ss`\n' "$silence_threshold_db" "$silence_minimum_duration"
  printf -- '- ASR and semantic observations are explicitly pending\n'
} > "$report_path"

jq empty "$inventory_path"
find "$assets_root" -name '*.json' -type f -print0 | xargs -0 -n1 jq empty

printf 'Created media inventory: %s\n' "$inventory_path"
printf 'Created index report: %s\n' "$report_path"

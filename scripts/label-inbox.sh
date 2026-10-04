#!/usr/bin/env bash
# Label every unlabelled email in the inbox, in batches.
#
# Each pass asks Gmail for inbox emails that carry none of the labels in the
# criteria file, so the script can be stopped and restarted at any time without
# re-classifying emails that are already labelled.
#
# Usage: scripts/label-inbox.sh [--dry-run] [--batch N] [--criteria-file PATH]
#   --dry-run    classify one batch without applying labels, then stop
#   --batch N    emails per pass, 1-1000 (default 500)
#   --criteria-file PATH
#                default ~/.config/jev-gmail-labeler/criteria.json
# Extra environment: JEV_LABELER_BIN overrides the command (default jev-gmail-labeler).
set -euo pipefail

batch=500
dry_run=0
criteria="$HOME/.config/jev-gmail-labeler/criteria.json"
bin="${JEV_LABELER_BIN:-jev-gmail-labeler}"

while (($#)); do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --batch) batch="${2:?--batch needs a value}"; shift ;;
    --criteria-file) criteria="${2:?--criteria-file needs a value}"; shift ;;
    -h | --help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if ! [[ "$batch" =~ ^[0-9]+$ ]] || ((batch < 1 || batch > 1000)); then
  echo "--batch must be between 1 and 1000" >&2
  exit 2
fi
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
[[ -r "$criteria" ]] || { echo "cannot read $criteria" >&2; exit 1; }

# Build `-label:"a" -label:"b" ...` from every label the criteria file can apply.
# A category without a "label" key uses its id; "label": null applies nothing.
exclusions=$(jq -r '
  (.label_prefix // "") as $p
  | [ (.categories[] | if has("label") then .label else .id end),
      .uncertain_label, .no_match_label ]
  | map(select(. != null) | if $p != "" then "\($p)/\(.)" else . end)
  | unique
  | map("-label:\"" + gsub("\""; "") + "\"")
  | join(" ")
' "$criteria")
[[ -n "$exclusions" ]] || { echo "no labels found in $criteria" >&2; exit 1; }
query="in:inbox $exclusions"

args=(label --query "$query" --count "$batch" --criteria-file "$criteria")
((dry_run)) || args+=(--apply)

total=0
pass=0
while true; do
  pass=$((pass + 1))
  summary=$("$bin" "${args[@]}" 2>&1 >/dev/null | tee /dev/stderr | grep '^Processed ' || true)
  processed=$(sed -n 's/^Processed \([0-9]*\) emails.*/\1/p' <<<"$summary")
  labelled=$(sed -n 's/.* \([0-9]*\) labelled.*/\1/p' <<<"$summary")
  if [[ -z "$processed" ]]; then
    echo "pass $pass: no summary line from $bin; stopping" >&2
    exit 1
  fi
  total=$((total + ${labelled:-0}))
  echo "pass $pass: $summary (total labelled: $total)"

  ((dry_run)) && break
  if ((processed == 0)); then
    echo "Done: no unlabelled emails left."
    break
  fi
  if ((${labelled:-0} == 0)); then
    echo "Stopping: pass labelled nothing (errors, or emails Jev gave no label)." >&2
    echo "Set no_match_label and uncertain_label in the criteria file to avoid the latter." >&2
    exit 1
  fi
done

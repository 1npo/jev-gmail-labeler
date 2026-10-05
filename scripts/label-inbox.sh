#!/usr/bin/env bash
# Label every unlabelled email in the inbox, in batches.
#
# First, one review pass re-classifies inbox emails that carry the criteria
# file's uncertain_label or no_match_label (with --force, so a new label replaces
# the old one). Edit the criteria file after reviewing them and rerun to move them.
# Then each pass asks Gmail for inbox emails that carry none of the labels in the
# criteria file, so the script can be stopped and restarted at any time without
# re-classifying emails that are already labelled.
#
# The review pass runs once per invocation, over at most --batch emails, because
# emails that stay uncertain would otherwise be fetched again forever.
#
# Usage: scripts/label-inbox.sh [--dry-run] [--batch N] [--criteria-file PATH]
#   --dry-run    classify one batch of each kind without applying labels, then stop
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
    -h | --help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# Prefix-aware label names from the criteria file, as Gmail search terms.
# A category without a "label" key uses its id, with spaces/underscores as hyphens; "label": null applies nothing.
label_terms() { # $1: jq expression listing label names
  jq -r --arg op "$2" '
    (.label_prefix // "") as $p
    | [ '"$1"' ]
    | map(select(. != null) | if $p != "" then "\($p)/\(.)" else . end)
    | unique
    | map($op + "label:\"" + gsub("\""; "") + "\"")
    | join(if $op == "" then " OR " else " " end)
  ' "$criteria"
}

exclusions=$(label_terms '(.categories[] | if has("label") then .label else (.id | gsub("[ _]"; "-")) end), .uncertain_label, .no_match_label' '-')
[[ -n "$exclusions" ]] || { echo "no labels found in $criteria" >&2; exit 1; }
review=$(label_terms '.uncertain_label, .no_match_label' '')

# run_pass QUERY [EXTRA_ARG...]: sets $processed and $labelled.
run_pass() {
  local args=(label --query "$1" --count "$batch" --criteria-file "$criteria")
  shift
  ((dry_run)) || args+=(--apply)
  local summary
  summary=$("$bin" "${args[@]}" "$@" 2>&1 >/dev/null | tee /dev/stderr | grep '^Processed ' || true)
  processed=$(sed -n 's/^Processed \([0-9]*\) emails.*/\1/p' <<<"$summary")
  labelled=$(sed -n 's/.* \([0-9]*\) labelled.*/\1/p' <<<"$summary")
  if [[ -z "$processed" ]]; then
    echo "$label: no summary line from $bin; stopping" >&2
    exit 1
  fi
  total=$((total + ${labelled:-0}))
  echo "$label: $summary (total labelled: $total)"
}

total=0
processed=0
labelled=0

if [[ -n "$review" ]]; then
  label="review pass"
  run_pass "in:inbox ($review)" --force
fi

pass=0
while true; do
  pass=$((pass + 1))
  label="pass $pass"
  run_pass "in:inbox $exclusions"

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

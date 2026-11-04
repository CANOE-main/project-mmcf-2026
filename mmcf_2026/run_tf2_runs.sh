#!/usr/bin/env bash
# MMCF TF2 submission runs, one after another, all with the net-zero policies on, the
# global carbon price off and the same cap on 2045 (standing for 2050) emissions:
#   tf2-ref   reference run, CANOE's own cost ratios
#   tf2-run1  Run 1, conservative harmonized cost ratios
#   tf2-run2  Run 2, accelerated harmonized cost ratios
# The first run compiles the base database; the others copy it (--db), so nothing is
# overwritten. Each run writes canoe-pwd/<name>_8d.sqlite and canoe-pwd/logs/<name>.log.
#
# Usage (from anywhere):  mmcf_2026/run_tf2_runs.sh
# Leave running:          nohup mmcf_2026/run_tf2_runs.sh > /dev/null 2>&1 &
# Skip the compiler:      BASE_DB=canoe-pwd/canoe-net-zero.sqlite mmcf_2026/run_tf2_runs.sh
# Change the cap (Mt):    NET_ZERO_CAP_MT=550 mmcf_2026/run_tf2_runs.sh

set -uo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
SCRIPT=mmcf_2026/tf2_electrification.py
COMPILED_DB=canoe-pwd/canoe-net-zero.sqlite  # compiler.base.db_output_dir in the config
LOG_DIR=canoe-pwd/logs
# Cap on 2045 emissions: NET_ZERO_CAP_MT in tf2_electrification.py unless overridden here
cap_args=()
[[ -n "${NET_ZERO_CAP_MT:-}" ]] && cap_args=(--net-zero-cap-mt "$NET_ZERO_CAP_MT")
mkdir -p "$LOG_DIR"

# name  extra flags (every run also gets --nz-policies and the cap; no --carbon-price)
SCENARIOS=(
  "tf2-ref|"
  "tf2-run1|--cost-ratios run1"
  "tf2-run2|--cost-ratios run2"
)

echo "net-zero cap: ${NET_ZERO_CAP_MT:-NET_ZERO_CAP_MT of $SCRIPT} Mt CO2e"
base_db="${BASE_DB:-}"
failed=()
for entry in "${SCENARIOS[@]}"; do
  name="${entry%%|*}"
  flags="${entry#*|}"
  log="$LOG_DIR/$name.log"

  if [[ -z "$base_db" ]]; then
    db_args=()  # compile, then the script copies the output to <name>.sqlite
  else
    db_args=(--db "$base_db")
  fi

  echo "$(date '+%F %T') start $name ($flags) -> $log"
  # stdin from /dev/null: an interactive prompt (e.g. Temoa's) fails the run instead of
  # waiting forever for an answer nobody sees, since the output goes to the log
  # shellcheck disable=SC2086  # flags are split on purpose
  if "$PY" "$SCRIPT" "${db_args[@]}" --scenario-name "$name" --nz-policies \
      "${cap_args[@]}" $flags > "$log" 2>&1 < /dev/null; then
    echo "$(date '+%F %T') done  $name"
  else
    echo "$(date '+%F %T') FAIL  $name (see $log)"
    failed+=("$name")
    # Without a compiled base database the remaining runs can't go ahead
    [[ -z "$base_db" ]] && { echo "compiler run failed, stopping"; exit 1; }
  fi
  base_db="${base_db:-$COMPILED_DB}"
done

if (( ${#failed[@]} )); then
  echo "failed: ${failed[*]}"
  exit 1
fi
echo "all runs done"

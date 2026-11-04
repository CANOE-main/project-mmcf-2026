#!/usr/bin/env bash
# TF2 experiments from configuration/canoe-full-pipeline.toml, run one after another:
#   1) no carbon price, no policies   2) carbon price, no policies
#   3) no carbon price, policies      4) carbon price, policies
# The first run compiles the base database; the others copy it (--db), so nothing is
# overwritten. Each scenario writes canoe-pwd/<name>_8d.sqlite and canoe-pwd/logs/<name>.log.
#
# Usage (from anywhere):  mmcf_2026/run_tf2_experiments.sh
# Leave running:          nohup mmcf_2026/run_tf2_experiments.sh > /dev/null 2>&1 &
# Skip the compiler:      BASE_DB=canoe-pwd/canoe-net-zero.sqlite mmcf_2026/run_tf2_experiments.sh

set -uo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
SCRIPT=mmcf_2026/tf2_electrification.py
COMPILED_DB=canoe-pwd/canoe-net-zero.sqlite  # compiler.base.db_output_dir in the config
LOG_DIR=canoe-pwd/logs
mkdir -p "$LOG_DIR"

# name  extra flags
SCENARIOS=(
  "nz-noprice-nopol|"
  "nz-price-nopol|--carbon-price"
  "nz-noprice-pol|--nz-policies"
  "nz-price-pol|--carbon-price --nz-policies"
)

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
  if "$PY" "$SCRIPT" "${db_args[@]}" --scenario-name "$name" $flags > "$log" 2>&1 < /dev/null; then
    echo "$(date '+%F %T') done  $name"
  else
    echo "$(date '+%F %T') FAIL  $name (see $log)"
    failed+=("$name")
    # Without a compiled base database the remaining scenarios can't run
    [[ -z "$base_db" ]] && { echo "compiler run failed, stopping"; exit 1; }
  fi
  base_db="${base_db:-$COMPILED_DB}"
done

if (( ${#failed[@]} )); then
  echo "failed: ${failed[*]}"
  exit 1
fi
echo "all scenarios done"

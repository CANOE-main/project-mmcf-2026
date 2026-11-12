#!/usr/bin/env bash
# MMCF TF2 forum runs, end to end:
#   1. Probe the 2045 emissions floor: Run 1 cost ratios, policies on, the very high
#      global carbon price on (GLOBAL_CARBON_PRICE in tf2_electrification.py), no cap.
#      Run 1 is the probe because it is the costliest case for electrification (dearer
#      BEVs and heat pumps than CANOE's own costs), so its floor should be the highest of
#      the three runs and a cap feasible for it is feasible for the others.
#   2. Cap = probe 2045 co2e x CAP_SLACK (default 1.10), so the cap is not the only
#      feasible solution and the cost ratios can still move the result.
#   3. Run the reference run, Run 1 and Run 2 with that cap (scripts/run_tf2_runs.sh):
#      policies on, carbon price off.
#
# Usage:                  scripts/run_tf2_forum.sh
# Leave running:          nohup scripts/run_tf2_forum.sh > canoe-pwd/logs/forum.log 2>&1 &
# Reuse a compiled base:  BASE_DB=canoe-pwd/canoe-net-zero.sqlite scripts/run_tf2_forum.sh
# Other slack:            CAP_SLACK=1.05 scripts/run_tf2_forum.sh
# Skip the probe:         FLOOR_MT=506.4 scripts/run_tf2_forum.sh

set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
SCRIPT=mmcf_2026/tf2_electrification.py
COMPILED_DB=canoe-pwd/canoe-net-zero.sqlite  # compiler.base.db_output_dir in the config
LOG_DIR=canoe-pwd/logs
PROBE=tf2-probe-run1
CAP_SLACK="${CAP_SLACK:-1.10}"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%F %T') $*"; }

# --------------------------------------------------------------------------------------
# 1. Probe the floor
# --------------------------------------------------------------------------------------
if [[ -n "${FLOOR_MT:-}" ]]; then
  log "probe skipped, floor given: $FLOOR_MT Mt"
  floor="$FLOOR_MT"
else
  if [[ -n "${BASE_DB:-}" ]]; then
    db_args=(--db "$BASE_DB")
  else
    db_args=()  # compile, then the script copies the output to tf2/$PROBE.sqlite
  fi
  log "start probe $PROBE -> $LOG_DIR/$PROBE.log"
  # stdin from /dev/null: an interactive prompt fails the run instead of hanging
  if ! "$PY" "$SCRIPT" "${db_args[@]}" --scenario-name "$PROBE" \
      --nz-policies --carbon-price --cost-ratios run1 --no-net-zero-cap \
      > "$LOG_DIR/$PROBE.log" 2>&1 < /dev/null; then
    log "FAIL probe (see $LOG_DIR/$PROBE.log)"
    exit 1
  fi
  BASE_DB="${BASE_DB:-$COMPILED_DB}"  # the probe compiled it if it wasn't given

  # Temoa writes its results into the representative-periods copy, <name>_<n>d.sqlite
  probe_out=$(ls -t canoe-pwd/tf2/"$PROBE"_*d.sqlite 2>/dev/null | head -n 1)
  [[ -n "$probe_out" ]] || { log "no probe output database canoe-pwd/tf2/${PROBE}_*d.sqlite"; exit 1; }
  floor=$(sqlite3 "$probe_out" \
    "SELECT ROUND(SUM(emission) / 1000, 2) FROM output_emission
     WHERE emis_comm = 'co2e' AND period = (SELECT MAX(period) FROM output_emission)")
  [[ -n "$floor" ]] || { log "no co2e results in $probe_out"; exit 1; }
  log "probe done: last-period co2e floor $floor Mt ($probe_out)"
fi

# --------------------------------------------------------------------------------------
# 2. Cap
# --------------------------------------------------------------------------------------
cap=$(awk -v f="$floor" -v s="$CAP_SLACK" 'BEGIN { printf "%.2f", f * s }')
log "cap: $floor Mt x $CAP_SLACK = $cap Mt CO2e"

# --------------------------------------------------------------------------------------
# 3. Forum runs: reference, Run 1, Run 2
# --------------------------------------------------------------------------------------
if [[ -n "${BASE_DB:-}" ]]; then
  BASE_DB="$BASE_DB" NET_ZERO_CAP_MT="$cap" scripts/run_tf2_runs.sh
else
  NET_ZERO_CAP_MT="$cap" scripts/run_tf2_runs.sh  # FLOOR_MT given and no base: compiles
fi
log "forum runs done with cap $cap Mt (floor $floor Mt, slack $CAP_SLACK)"

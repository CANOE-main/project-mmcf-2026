#!/bin/bash
# Submit one job per TF1 run, with the memory and time set in tf1_rep_days.py (RUNS).
#
#   ./submit_all.sh                                # all runs
#   ./submit_all.sh tf1_midmonth12 tf1_peak12      # only these
#   DRY_RUN=1 ./submit_all.sh                      # print the sbatch commands
#   EXTRA="--solver-option ScaleFlag=2" ./submit_all.sh tf1_selected120
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
mkdir -p logs  # Slurm doesn't create the --output directory

python tf1_rep_days.py list "$@" | while read -r name mem time; do
  cmd=(sbatch --job-name="$name" --mem="$mem" --time="$time" run_one.sh "$name" ${EXTRA:-})
  if [[ "${DRY_RUN:-0}" == 1 ]]; then
    echo "${cmd[*]}"
  else
    echo -n "$name ($mem, $time): "
    "${cmd[@]}"
  fi
done

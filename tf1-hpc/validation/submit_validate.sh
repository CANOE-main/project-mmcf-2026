#!/bin/bash
# Submit one validation per solved reduced run in runs/ (validation/validate.py list).
#
#   validation/submit_validate.sh                     # every solved reduced run
#   validation/submit_validate.sh peak12 selected12   # only runs whose path contains these
#   DRY_RUN=1 validation/submit_validate.sh           # print the sbatch commands
#   EXTRA="--solver-option ScaleFlag=2" validation/submit_validate.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
mkdir -p logs

python validation/validate.py list | while read -r db mem time; do
  if [[ $# -gt 0 ]]; then
    match=0
    for want in "$@"; do [[ "$db" == *"$want"* ]] && match=1; done
    [[ $match == 1 ]] || continue
  fi
  name="val_$(basename "$(dirname "$db")")"
  cmd=(sbatch --job-name="$name" --mem="$mem" --time="$time" validation/run_validate.sh "$db" ${EXTRA:-})
  if [[ "${DRY_RUN:-0}" == 1 ]]; then
    echo "${cmd[*]}"
  else
    echo -n "$name ($mem, $time): "
    "${cmd[@]}"
  fi
done

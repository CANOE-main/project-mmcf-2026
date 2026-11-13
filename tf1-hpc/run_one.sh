#!/bin/bash
# One TF1 run (Slurm job). Memory and time come from submit_all.sh (sbatch flags override
# these lines). Everything after the run name goes to `tf1_rep_days.py run`.
#
#   sbatch --mem=64G --time=03:00:00 run_one.sh tf1_selected12
#   sbatch --mem=245G --time=24:00:00 run_one.sh tf1_selected120 --solver-option ScaleFlag=2
#
#SBATCH --job-name=tf1
#SBATCH --account=def-dposen
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=03:00:00
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
#SBATCH --mail-user=yamil.essus@utoronto.ca
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"

module load gurobi
source .venv/bin/activate

python tf1_rep_days.py run "${1:?usage: run_one.sh <run name> [options]}" "${@:2}" < /dev/null

#!/bin/bash
# One full-year validation (Slurm job), from the package root. Everything after the solved
# database goes to `validation/validate.py run`.
#
#   sbatch --mem=1200G --time=72:00:00 validation/run_validate.sh runs/tf1_peak12_123/tf1_peak12_12d.sqlite
#
#SBATCH --job-name=tf1-val
#SBATCH --account=def-dposen
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=1200G
#SBATCH --time=72:00:00
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
#SBATCH --mail-user=yamil.essus@utoronto.ca
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"

module load gurobi
source .venv/bin/activate

python validation/validate.py run "${1:?usage: run_validate.sh <solved db> [options]}" "${@:2}" < /dev/null

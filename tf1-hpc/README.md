# MMCF TF1: temporal representation (HPC package)

The TF3 reference run (CER + coal phase-out, perfect foresight, same Temoa settings as
`mmcf_2026/tf3_myopic.py`) under the 8 sets of representative days of the TF1 Experiment
Design (Oct 16 2026). One Slurm job per run. Everything is in `tf1_rep_days.py`; the
shell scripts only submit jobs.

| Run | Days | Rep days |
|---|---|---|
| `tf1_midmonth12` | 12 | 15th of each month; every day represented by its month's 15th |
| `tf1_peak12` | 12 | Day of each month with the highest hourly Canada demand (CODERS 2018, summed over provinces) |
| `tf1_selected{12,24,36,60,120}` | n | Our method (tsam hierarchical, PCA 6, weather 2018), only `n_periods` changes |
| `tf1_all365` | 365 | No reduction, `time_sequencing = "consecutive_days"` |

Mid-month and peak days are precomputed by `prepare` as tsam collections in
`clusterings/` (`assignments.csv`) and passed to the rep periods module as
`TsamCollection`. Their weights are the days in each month (28 to 31), so close to,
but not exactly, equal. `days_per_period` is the number of days the rep days represent:
363 for the selected runs (the weather series run from Jan 2 to Dec 30), 365 otherwise.

## 1. Prepare (locally, inside project-mmcf-2026)

```bash
.venv/bin/python tf1-hpc/tf1_rep_days.py prepare                    # compile, then policies
.venv/bin/python tf1-hpc/tf1_rep_days.py prepare --db canoe-pwd/canoe-net-zero.sqlite  # skip compiler
```

It writes:
- `db/tf1-ref.sqlite`: the compiled DB + transportation + coal phase-out + CER, full year
- `clusterings/{midmonth,peak}/`: precomputed rep days (`peak/daily_peak.csv` has the daily peaks)
- `assets/rep_periods_data/timeseries/`: weather series for the selected runs

## 2. Copy to Narval and set up

```bash
# From project-mmcf-2026. tf1-hpc/ must not hold a .venv, runs/ or logs/ (scp copies everything)
scp -r tf1-hpc narval:~/scratch/

# On a login node (compute nodes have no internet)
cd ~/scratch/tf1-hpc
module load gurobi python/3.12
uv sync
```

## 3. Run

```bash
DRY_RUN=1 ./submit_all.sh                  # print the sbatch commands
./submit_all.sh tf1_selected12             # smoke test
./submit_all.sh                            # all 8
EXTRA="--solver-option ScaleFlag=2" ./submit_all.sh tf1_selected120   # extra Gurobi options
squeue -u $USER
python tf1_rep_days.py collect             # -> results.csv
```

Then the full-year validation of each run (section 4).

Memory, time and per-run Gurobi options are in `RUNS` in `tf1_rep_days.py` (sized from the
canoe-benchmarking runs: 32d used 35 GB, 64d 64 GB, 128d 112 GB, on 32 CPUs). Note that the
benchmark's 128d and 256d runs hit numerical trouble with Temoa's default barrier settings;
`tf1_selected120` and `tf1_all365` may need `ScaleFlag=2` (see canoe-benchmarking/numerics).

## Outputs: `runs/<run>_<jobid>/`

| File | |
|---|---|
| `summary.json` | Rep days, `days_per_period`, threads, Gurobi options, status, reduction/Temoa time, peak RSS (Python process and children) |
| `gurobi.log` | Gurobi log: problem size, iterations, solve time |
| `rep_days.csv` | Day -> representative day |
| `temoa-outputs/` | Temoa log |
| `<run>[_<n>d].sqlite` | Solved database (skip with `--no-keep-db`) |

## 4. Full-year validation (after the runs finish)

`validation/validate.py` takes a solved reduced run, copies the unsolved full-year
`db/tf1-ref.sqlite` and solves all 365 days in sequence with the run's decisions fixed:

- **New capacity** of every tech and vintage the run built: `ge` the run's (`limit_new_capacity`),
  shaved by `CLOSE_TOL` (0.1%) for solver noise. Below `ZERO_TOL` (1e-3): no bound. The model
  can build more: that top-up (`topup.csv`) is what the run's plan was missing.
- **Retirements**: the capacity of retirable techs (only `E_COAL-EXS`) is fixed in every period
  to the run's (within `CLOSE_TOL`), so the validation retires exactly what the run retired.
- **Upper limits the run reached** (`le` rows of `limit_new_capacity` / `limit_capacity`
  within `CLOSE_TOL`) become equalities at the limit, so the bounds can't cross them.

```bash
DRY_RUN=1 validation/submit_validate.sh          # one job per runs/tf1_*/tf1_*_<n>d.sqlite
validation/submit_validate.sh                    # all (1200G, 72h each, as tf1_all365)
validation/submit_validate.sh peak12 selected12  # only these
python tf1_rep_days.py collect                   # validations appear as val_<run>
```

Outputs in `runs/val_<run>_<jobid>/`: as above, plus `min_new_capacity.csv` (the bounds)
and `topup.csv` (capacity built beyond them). The validation's objective minus
`tf1_all365`'s is the extra full-year cost of planning on the run's days (never negative).

## Collect

`collect` puts them in one table (`results.csv`) with the problem size and solve time from
`gurobi.log` and Slurm's state, elapsed, CPU time and MaxRSS from `sacct`: the TF1
computational statistics.

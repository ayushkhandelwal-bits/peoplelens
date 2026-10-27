# Modelling methodology and results

Results below are from the default full-profile build (seed 42, data to 30 Sep 2026). Re-run with
`python scripts/train_models.py`; the app's *Model monitoring* tab shows the values for the current build.

## 1. Workforce forecasting (`src/models/workforce_forecast.py`)

**Question:** what will headcount, exits and hiring look like over the next six months?

**Candidates.** Naive (last value), Drift (average historical change), Holt damped-trend exponential smoothing,
Holt-Winters (additive trend + 12-month season, only with ≥ 24 training months) and a structural **flow model**:

```
headcount(t) = headcount(t-1) − exits(t) + hires(t)
exits(t)     = headcount(t-1) × seasonal exit rate[calendar month]   (rescaled to the trailing-12-month level)
hires(t)     = max(known offer pipeline(t) × historical join rate, trailing 6-month average hires)
```

The known pipeline is the set of offers already accepted *at the forecast origin* with an agreed joining date after
it — information a planner really has at that time (verified by a test). Offers accepted but not yet joined at
the origin (no joining record yet) are placed at the accepted date plus the median accept-to-join gap observed
before the origin.

**Validation.** Rolling-origin backtests with a 6-month horizon at three origins (Sep 2025, Dec 2025, Mar 2026),
strictly chronological, never shuffled. Selection = lowest mean MAE.

| Model | MAE (heads) | RMSE |
|---|---:|---:|
| **Flow model (selected)** | **29.1** | 33.9 |
| Holt (damped) | 31.1 | 34.5 |
| Drift | 34.8 | 39.7 |
| Holt-Winters | 52.0 | 63.5 |
| Naive | 97.4 | 110.9 |

The flow model cuts error by 70% versus naive; Holt is a close second. Holt-Winters overfits the short (≤ 30-month)
training windows. For **monthly exits**, seasonal-naive (MAE 10.1 exits/month) slightly beats the flow model's
exit component (11.3) — reported honestly; the flow model is still preferred for headcount because it combines exits
with the hiring pipeline.

**Uncertainty.** 80% intervals = forecast ± 1.28 × backtest RMSE at each horizon (with a floor), i.e. based on
out-of-sample errors. With three origins the intervals are approximate.

**Department forecasts and hiring demand.** Each department uses the organisation's seasonal exit profile scaled by
its trailing attrition and its own pipeline (transfers ignored). Hires required = plan headcount at horizon end −
current headcount + expected exits; compared with the hires budgeted in the plan and with what the pipeline covers.

## 2. Attrition risk (`src/models/attrition.py`, `features.py`)

**Question:** which groups are more likely to leave voluntarily in the next six months?

* **Unit:** employee × prediction date (quarter ends). Population: active employees L1–L7.
* **Label:** voluntary exit in (date, date + 6 months].
* **Features (all dated ≤ prediction date):** tenure, level, contract, latest engagement / manager-support /
  career-growth scores and change vs previous survey, survey non-response, latest rating (and missing flag), pay vs
  peer median (same level and family), months since promotion/hire, manager change in last 6 months, manager's team
  size, development hours in last 12 months, department trailing-12-month attrition, business unit, location, source
  of hire.
* **Excluded:** exit date, exit type, exit reason, any later record (leakage); name and gender (policy).
* **Temporal split:** train on 7 quarter-ends (Mar 2024 – Sep 2025; 38,101 rows), test on employees active on
  **31 Mar 2026** (5,916 rows) and their outcomes to 30 Sep 2026. Training outcome windows end before the test date.
* **Threshold:** maximises F1 on the last training snapshot (model fitted on earlier snapshots), then frozen.

| Hold-out (31 Mar 2026) | Logistic regression (primary) | Gradient boosting |
|---|---:|---:|
| ROC-AUC | **0.637** | 0.625 |
| PR-AUC (base rate 7.6%) | **0.127** | 0.120 |
| Brier score | 0.069 | 0.069 |
| Precision / recall at threshold | 12.2% / 45.0% | 13.8% / 28.2% |
| Top-decile lift | 1.93× | 2.02× |
| Confusion (TN / FP / FN / TP) | 4,021 / 1,448 / 246 / 201 | 4,681 / 788 / 321 / 126 |

The boosted model was not materially better (rule: > 0.02 AUC), so the interpretable logistic regression is primary.
Calibration by decile tracks the diagonal (shown in the app).

**Explanations.** Largest standardised coefficients: latest engagement (−0.30), pay vs peer median (−0.19), job level
(−0.15), department trailing attrition (+0.12), latest rating (−0.11), tenure (−0.10), survey non-response (+0.09).
Permutation importance for the boosted model ranks engagement, level, pay and department attrition highest; location
contributes almost nothing — consistent with it having no simulated effect.

**Honest reading.** AUC 0.64 means the model orders risk better than chance but misses most individual leavers; at the
threshold it flags 1,649 employees to find 201 of 447 leavers. It is suitable for aggregated, team-level planning and
for prompting supportive conversations — not for decisions about individuals. Associations are not causes.

A separate **statsmodels logistic regression** (Talent & Lifecycle → Statistical diagnostics) gives odds ratios with
employee-clustered 95% CIs for interpretable binary factors (e.g. engagement +1 SD: OR 0.72; contract: OR 1.36;
manager change in 6 months: OR 1.24; no promotion in 3+ years: OR 0.99, not significant).

## 3. Time to fill (`src/models/hiring_forecast.py`)

* **Kaplan-Meier** curves by job family treat open and cancelled requisitions as censored. Median 53–81 days; P80
  105–149 days (Legal & Compliance slowest). The naive median of filled requisitions understates the KM median by
  1–5 days because long searches are still open.
* **Point prediction hold-out:** trained on 2,557 requisitions opened before 30 Jun 2025; tested on 725 opened
  30 Jun – 30 Dec 2025 (only 8 still open, so censoring is negligible).

| Model | MAE (days) |
|---|---:|
| Global median (baseline) | 40.6 |
| Segment median (family × level band) | 40.9 |
| Gradient boosting, median loss | 41.6 |

No model beats the baseline: in this dataset time to fill is dominated by candidate-level chance. PeopleLens therefore
plans with distributions (open a requisition P80 days plus notice period before the need date) instead of a point
model.

## 4. Scenarios (`src/models/scenarios.py`)

Deterministic, not trained: per department and month, exits = headcount × (baseline annual exit rate + Δpp)/12,
hires = max(known pipeline, baseline pace × (1 + change) + extra target) after any added delay, cost = headcount ×
cost per head × (1 + inflation), turnover cost = exits × replacement cost. Baselines: last 12 months' exit rate, last
6 months' hiring pace, latest cost per head. Tests check determinism and direction of effects.

## 5. Monitoring and refresh

Every artefact stores version, training time, data window, dataset sizes, metrics, assumptions and limitations
(`artifacts/*.json`). Training is deliberate (`scripts/train_models.py` or the in-app button when artefacts are
missing) and never runs on page load.

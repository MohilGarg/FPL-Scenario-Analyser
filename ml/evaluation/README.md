# Offline baseline evaluation — Phase 2b

`metrics.py` reports MAE, RMSE, mean signed prediction bias, error quantiles, threshold fractions,
retrospective official-position groups and observed zero/nonzero-minute groups. Selection uses
validation MAE with RMSE as its tie breaker. Paired MAE differences use 1,000 four-GW moving-block
resamples; these intervals are approximate, not a full account of all player/fixture dependence.

`report.py` reads saved results only and creates tables plus two standalone PNG charts. Run
`python -m ml.training.experiment report`. Complete prediction distributions and model diagnostics
remain in the ignored experiment directory; concise reviewed findings are committed in
`ml/reports/BASELINE_EXPERIMENTS.md`.

There is no production forecasting-engine comparison. Such a comparison still requires identical
historical information cutoffs, availability/schedules and a scoring-rule policy. This experiment
retains recorded unmultiplied historical FPL points and never silently rescores old seasons.

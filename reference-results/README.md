# Reference Results

These compact files summarize the canonical synthetic campaigns used in the manuscript. `policy_table.tex`, `ablation_table.tex`, `uncertainty_table.tex`, and `scaling_table.tex` are the manuscript-ready tables; `summary.json` records policy means; `random_tag_smoke.json` records the seeded hostname/DC software smoke test.

`canonical-run-manifest.json` is the manifest from the original full multiscale run. It records hashes for all canonical output files, including the large scenario and decision JSON files that are intentionally omitted from this Git folder. `manifest.json` records hashes of the compact files present here and the source hashes from the canonical run.

The omitted scenario/decision files can be regenerated with `run_multiscale_experiments.py`. Timings are machine-dependent, so byte-for-byte reproduction of timed output files is not expected. Compare scenario structures, decisions, costs, and residual reachability rather than wall-clock values.

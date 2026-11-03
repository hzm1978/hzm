# DML further study — 3 November 2026

Read `研究结论_20261103.md` for the Chinese assessment and `note/rank_sensitive_minimax.pdf` for full analytic arguments.

This is a separate research note. The already published DML v53 package is unchanged. The derivations concern rank promises, a common optimal frame measurement, finite nonattainment, and the four-dimensional pure-input outcome threshold. Literature novelty has **not** been established.

Files:

- `note/rank_sensitive_minimax.pdf` and `.tex`: complete internal derivations.
- `code/check_rank_extensions.py`: beta quadrature, Monte Carlo, direct density-matrix probabilities, and finite constructions.
- `results/rank_extension_checks.json`: saved executed results and versions.
- `results/literature_access_probe.json`: actual blocked access attempts.
- `literature_verification_plan.csv`: priority sources, search terms, and claims still to verify.

Reproduce with Python 3.12 and the versions in `code/requirements-tested.txt`:

```bash
python -m pip install -r code/requirements-tested.txt
python code/check_rank_extensions.py
python code/plot_rank_guarantees.py
```

Compile the note using `latexmk -pdf -interaction=nonstopmode -halt-on-error rank_sensitive_minimax.tex` from `note/`. A standard TeX Live installation with amsmath, amsthm, geometry, booktabs, and hyperref is sufficient.

Saved environment draft additions require user review/save/publication before the blocked primary-source work can resume. No network restriction was bypassed.

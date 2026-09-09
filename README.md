# asimov-exoplanet

An [Asimov](https://github.com/etive-io/asimov) pipeline plugin for searching
stellar photometry (e.g. Kepler/K2/TESS light curves) for transiting
exoplanets, registered via the `asimov.pipelines` entry point in the same
way as `asimov-bilby`, `asimov-lalinference`, `asimov-pesummary`, and
`asimov-gracedb`.

See [`DESIGN.md`](DESIGN.md) for the full design and phased roadmap.

## Status

This package has completed **Phases 1 through 3** of the roadmap in
`DESIGN.md`: real MAST/`lightkurve` ingestion, detrending, a Box Least
Squares transit search, odd/even and secondary-eclipse vetting checks, and
an interactive per-target HTML report are implemented and wired into
`BLSTransitSearch`, with a worked single-target blueprint at
`examples/kepler-10.yaml`. Catalog-scale campaigns (`ProjectAnalysis`) are
also supported -- an HTCondor DAG per campaign (one job per target plus a
dependent aggregation job) and an aggregate candidate report -- with a
worked multi-target blueprint at `examples/koi-catalog.yaml`. Slurm catalog
submission has a known limitation: see "Open questions" in `DESIGN.md`.

## Installation

```bash
pip install -e ".[test]"
```

The `photometry` extra (`astropy`, `lightkurve`, `astroquery`) is required
for `BLSTransitSearch` and is pulled in automatically by `[test]`; the
dependency-free `DummyTransitSearchPipeline` doesn't need it.

## Running the tests

```bash
pytest
```

## Trying it against a real target

```bash
asimov apply -f examples/kepler-10.yaml
asimov manage build
asimov manage submit
```

## Trying it against a small catalog campaign

```bash
asimov apply -f examples/koi-catalog.yaml
asimov manage build
asimov manage submit
```

## Package layout

```
asimov_exoplanet/
  pipeline.py            # BLSTransitSearch + DummyTransitSearchPipeline - the asimov.pipelines entry points
  filesource.py          # MAST/Kepler asimov.hooks.filesource entry point
  photometry.py          # detrend()/search() - plain functions, unit-testable without Asimov
  vetting.py             # odd/even + secondary-eclipse checks - plain functions, same pattern
  cli.py                 # asimov-exoplanet-bls / asimov-exoplanet-bls-catalog-report console scripts
  config_template.toml   # liquid-templated pipeline config
  report.py              # per-target (Phase 2) and per-catalog (Phase 3) interactive HTML reports
```

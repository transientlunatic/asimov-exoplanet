# asimov-exoplanet

An [Asimov](https://github.com/etive-io/asimov) pipeline plugin for searching
stellar photometry (e.g. Kepler/K2/TESS light curves) for transiting
exoplanets, registered via the `asimov.pipelines` entry point in the same
way as `asimov-bilby`, `asimov-lalinference`, `asimov-pesummary`, and
`asimov-gracedb`.

See [`DESIGN.md`](DESIGN.md) for the full design and phased roadmap.

## Status

This package has completed **Phase 1** of the roadmap in `DESIGN.md`: real
MAST/`lightkurve` ingestion, detrending, and a Box Least Squares transit
search are implemented and wired into `BLSTransitSearch`, with a worked
blueprint at `examples/kepler-10.yaml`. Vetting and per-target/catalog
reporting (Phase 2/3) are not yet implemented.

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

## Package layout

```
asimov_exoplanet/
  pipeline.py            # BLSTransitSearch + DummyTransitSearchPipeline - the asimov.pipelines entry points
  filesource.py          # MAST/Kepler asimov.hooks.filesource entry point
  photometry.py          # detrend()/search() - plain functions, unit-testable without Asimov
  cli.py                 # asimov-exoplanet-bls console script: the job build_dag() actually runs
  config_template.toml   # liquid-templated pipeline config
  report.py              # per-target + per-catalog reporting (Phase 2, stub)
```

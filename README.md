# asimov-exoplanet

An [Asimov](https://github.com/etive-io/asimov) pipeline plugin for searching
stellar photometry (e.g. Kepler/K2/TESS light curves) for transiting
exoplanets, registered via the `asimov.pipelines` entry point in the same
way as `asimov-bilby`, `asimov-lalinference`, `asimov-pesummary`, and
`asimov-gracedb`.

See [`DESIGN.md`](DESIGN.md) for the full design and phased roadmap.

## Status

This package is at the **Phase 0 (scaffold)** stage of the roadmap in
`DESIGN.md`: the package skeleton, entry points, and a dummy pipeline exist
so the Asimov plugin plumbing can be exercised end-to-end, but no real
photometry ingestion, detrending, or transit search is implemented yet.

## Installation

```bash
pip install -e ".[test]"
```

## Running the tests

```bash
pytest
```

## Package layout

```
asimov_exoplanet/
  pipeline.py            # BLSTransitSearch (Phase 1, stub) + DummyTransitSearchPipeline (Phase 0)
  filesource.py          # MAST/Kepler asimov.hooks.filesource entry point (stub)
  config_template.toml   # liquid-templated pipeline config
  report.py              # per-target + per-catalog reporting (stub)
```

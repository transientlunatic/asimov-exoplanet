# Design: `asimov-exoplanet`

An [Asimov](https://github.com/etive-io/asimov) pipeline plugin for searching
for transiting exoplanets in stellar photometry (e.g. Kepler/K2/TESS light
curves), registered via the `asimov.pipelines` entry point in the same way as
`asimov-bilby`, `asimov-lalinference`, `asimov-pesummary`, and
`asimov-gracedb`.

This document was originally scoped as a design proposal against Asimov core
(`etive-io/asimov#152`). Asimov core no longer accepts pipeline-specific code
or documentation in-tree, so it lives here instead, as the design doc for
this standalone plugin package.

## Motivation

Asimov already provides ledger-driven orchestration (blueprints, job
submission, monitoring, recovery) for gravitational-wave parameter
estimation pipelines. That same machinery — a declarative ledger of targets
and analyses, templated pipeline configuration, and scheduler-backed job
management — maps cleanly onto a very different domain: searching stellar
photometry for transiting exoplanets. This package is a proof that Asimov's
abstractions (`subject`, `SimpleAnalysis`, `ProjectAnalysis`, `Pipeline`)
generalise beyond gravitational-wave astronomy, and a genuinely useful tool
for running transit searches at catalog scale.

## Mapping onto Asimov's model

| Asimov concept | This plugin |
|---|---|
| `subject` (blueprint `kind: subject`) | A target star, keyed by catalog ID (KIC/EPIC/TIC) |
| `SimpleAnalysis` | One pipeline run on one target: download light curve → detrend → transit search → vet |
| `ProjectAnalysis` | A catalog-scale campaign: the same analysis run across many targets (a KOI/TOI list, an injection-recovery study) |

A single light-curve analysis takes seconds, so Asimov's job orchestration
(HTCondor/Slurm submission, monitoring, recovery) doesn't earn its keep at
the single-target scale — the payoff is at catalog scale, via
`ProjectAnalysis`, where hundreds or thousands of targets need the same
pipeline run, tracked, and retried on failure.

## Package layout (provisional)

```
asimov_exoplanet/
  __init__.py
  pipeline.py            # BLSTransitSearch(Pipeline) - the asimov.pipelines entry point
  filesource.py          # MAST/Kepler asimov.hooks.filesource entry point
  config_template.toml   # liquid-templated pipeline config
  report.py              # per-target + per-catalog reporting
```

## MVP pipeline stages (Phase 1)

Using [Box Least Squares](https://docs.astropy.org/en/stable/timeseries/bls.html)
(`astropy.timeseries.BoxLeastSquares`) as the transit-search algorithm — no
heavy extra dependency, and a solid, well-understood baseline before
anything more sensitive (like `transitleastsquares`) is considered.

1. **Ingest** — fetch the light curve for the subject's catalog ID via a
   `mast`/`kepler` `asimov.hooks.filesource` hook, mirroring how
   `asimov-gracedb` fetches GWOSC frames: a class taking the global Asimov
   `config` object in its constructor, and exposing
   `fetch(target_id, product) -> bytes`. Under the hood this uses
   `lightkurve`/`astroquery.mast`. The fetched FITS file is cached under the
   analysis run directory.
2. **Detrend** — remove stellar variability and instrumental trends, using
   `lightkurve`'s `flatten()` / spline detrending.
3. **Transit search** — run BLS over a period grid; record the best period,
   epoch, duration, depth, and a significance statistic (SDE or equivalent).
4. **Vet** — cheap, deterministic checks only for the MVP: odd/even transit
   depth consistency, a secondary-eclipse search at phase 0.5, and
   per-quarter/sector consistency where available. This is **not** full
   centroid/pixel-level vetting (which needs target pixel files, not just
   light curves) — that's future work (Phase 4).
5. **Report** — a small machine-readable `results.json` (period, depth,
   duration, SDE, vetting flags) plus a folded-light-curve plot, analogous to
   `collect_assets` for GW pipelines.

## Pipeline class

`BLSTransitSearch` subclasses `asimov.pipeline.Pipeline`, registered under
the name `photometry-bls`:

- `build_dag(dryrun=False)` — write an executable script (or, for catalog
  campaigns, one row of an HTCondor/Slurm DAG per subject) running
  ingest → detrend → BLS → vet → report, writing `results.json` into
  `self.production.rundir`.
- `submit_dag(dryrun=False)` — hand off to the configured scheduler
  (`asimov.scheduler.Slurm` or HTCondor), same as other pipelines.
- `detect_completion()` — `os.path.exists(os.path.join(self.production.rundir, "results.json"))`.
- `collect_assets()` — returns
  `{"results": .../results.json, "folded_lightcurve": .../folded_lightcurve.png}`.

Each stage is fast and deterministic, so a single target can reasonably run
as one job. The DAG/scheduler machinery starts mattering once a
`ProjectAnalysis` fans this out over a catalog.

## Blueprint examples

A single-target `SimpleAnalysis`:

```yaml
kind: subject
name: KIC-11446443
photometry:
  mission: Kepler
  catalog id: 11446443
---
kind: analysis
name: transit-search
pipeline: photometry-bls
comment: BLS transit search on Kepler-10
```

A catalog-scale `ProjectAnalysis` (Phase 3):

```yaml
kind: project_analysis
name: koi-catalog-rerun
pipeline: photometry-bls
subjects: koi-active-list.txt
comment: Re-run BLS across all active KOIs with an updated detrending window
```

## Configuration templating

Config templates use the [liquid](https://shopify.github.io/liquid/) templating
language, matching how other Asimov pipelines template their configuration
(e.g. `bilby.ini`):

```toml
[target]
catalog_id = {{ production.subject.meta['photometry']['catalog id'] }}
mission = "{{ production.subject.meta['photometry']['mission'] }}"

[detrend]
window_length = {{ production.meta['detrend']['window length'] | default: 0.5 }}

[bls]
period_min = {{ production.meta['bls']['period min'] | default: 0.5 }}
period_max = {{ production.meta['bls']['period max'] | default: 20.0 }}
duration_grid = {{ production.meta['bls']['duration grid'] | default: "[0.05, 0.10, 0.20]" }}
```

## Phased roadmap

- **Phase 0 — Scaffold** *(this PR)*: package skeleton, `asimov.pipelines` +
  `asimov.hooks.filesource` entry points in `pyproject.toml`, and a dummy
  pipeline (following the pattern of asimov core's
  `asimov/pipelines/testing` module — `SimpleTestPipeline` et al.) so the
  entry-point plumbing can be verified end-to-end before any real astronomy
  code exists.
- **Phase 1 — Single-target MVP**: real MAST/`lightkurve` ingestion,
  detrending, BLS, `collect_assets`/`detect_completion`, a worked blueprint,
  and unit tests against synthetic injected-transit light curves (no network
  access needed in CI).
- **Phase 2 — Vetting & reporting**: odd/even and secondary-eclipse checks,
  folded-light-curve plots, per-target report.
- **Phase 3 — Catalog-scale campaigns**: `ProjectAnalysis` support,
  HTCondor/Slurm DAG generation for batch submission, an aggregate
  report/dashboard (candidate table, completeness plots for
  injection-recovery studies).
- **Phase 4 — Stretch**: a pluggable transit-search backend
  (`transitleastsquares` as an alternative to BLS), multi-mission support
  (TESS, K2), pixel-level vetting using target pixel files.

## Open questions

These don't need to be resolved now, but are worth recording:

- **Network access from worker nodes.** Catalog campaigns fan out over
  HTCondor/Slurm worker nodes, which may not have network access to MAST.
  This is the same problem GW pipelines solve for GWOSC frames (pre-staging
  data on a submit node vs. allowing worker-node network access) — the
  solution there should transfer, but hasn't been validated for MAST access
  patterns specifically.
- **Storage of downloaded FITS files.** Light curve FITS files should be
  treated as run-directory assets, not committed into any git-backed ledger
  repository — these can be large and numerous at catalog scale.
- **Depth of vetting required before Phase 2 is "done".** The MVP's
  deterministic checks (odd/even depth, secondary eclipse, per-sector
  consistency) are not a substitute for full centroid/pixel-level vetting,
  which needs target pixel files rather than just light curves. Phase 2
  should probably ship with this limitation clearly documented rather than
  wait for pixel-level vetting to be ready.

## Testing strategy

Mirrors `asimov/pipelines/testing` in asimov core: unit tests build a
pipeline instance against synthetic light curves with a known injected
transit (fixed period/depth/duration), assert that BLS recovers it within
tolerance, and exercise `build_dag`/`detect_completion`/`collect_assets`
without network access or a real scheduler. End-to-end tests can later use a
small, fixed real target (e.g. Kepler-10, whose transits are
well-characterised) checked against published ground truth.

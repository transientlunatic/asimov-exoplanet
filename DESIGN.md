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
  photometry.py          # detrend()/search() - plain functions, unit-testable without Asimov
  cli.py                 # asimov-exoplanet-bls console script: the job build_dag() actually runs
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

A single-target `SimpleAnalysis` (see `examples/kepler-10.yaml` for a
worked, verified-working version of this against a real Kepler target):

```yaml
kind: event
name: KIC-11446443
photometry:
  mission: Kepler
  catalog id: 11446443
---
kind: analysis
name: transit-search
event: KIC-11446443
pipeline: photometry-bls
comment: BLS transit search on Kepler-10
```

> **Note:** the original design used `kind: subject` for the first document.
> As of asimov 0.7.0, `asimov apply` (`asimov.cli.application.apply_page`)
> only recognises `kind: event` -- `subject` is accepted as an alias for the
> same object by the newer schema in `asimov.blueprints`
> (`select_blueprint_kind`), but that schema isn't wired into `apply_page`
> yet. Use `event` until it is. Likewise, an analysis blueprint applied
> without an explicit CLI `-e`/`--event` flag needs its target named via an
> `event:` field, or `apply_page` will prompt for it interactively.

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
window_length = {% if production.meta['detrend'] and production.meta['detrend']['window length'] %}{{ production.meta['detrend']['window length'] }}{% else %}0.5{% endif %}

[bls]
period_min = {% if production.meta['bls'] and production.meta['bls']['period min'] %}{{ production.meta['bls']['period min'] }}{% else %}0.5{% endif %}
period_max = {% if production.meta['bls'] and production.meta['bls']['period max'] %}{{ production.meta['bls']['period max'] }}{% else %}20.0{% endif %}
duration_grid = {% if production.meta['bls'] and production.meta['bls']['duration grid'] %}{{ production.meta['bls']['duration grid'] }}{% else %}[0.05, 0.10, 0.20]{% endif %}
```

> **Gotcha:** the `| default:` filter only substitutes a fallback for an
> *undefined value* -- it doesn't protect against indexing into a completely
> missing parent key. `production.meta['detrend']['window length'] | default: 0.5`
> raises at render time if `detrend:` was never set at all (the common case
> for a bare blueprint with no overrides), rather than falling back to 0.5.
> Guard each optional section with an explicit `{% if %}` on the parent key
> first, as above.

## Bayesian signal characterization (Phase 5)

### Prior art

[BayesFlare](https://github.com/BayesFlare/bayesflare) (Pitkin, Williams,
Fletcher & Grant 2014, [MNRAS 445, 2268](https://arxiv.org/abs/1406.1712))
computed a Bayesian odds ratio between a flare-shaped template (Gaussian
rise + exponential decay) and Gaussian noise for Kepler light curves, via a
template grid scan with the signal amplitude marginalised analytically in
closed form (`log_marg_amp`, a cross-correlation/matched-filter kernel).
Its model library also included a `Transit` template (a flat-bottomed box
with Gaussian ingress/egress wings) alongside `Flare` — the same
odds-ratio machinery could in principle have compared flare, transit, and
noise hypotheses for the same data, though nothing in the historical
codebase wires that comparison up.

The statistical idea generalises past Kepler and past flares specifically;
what's dated is the implementation (Python 2.7/Cython/PyFITS, GPLv2,
unmaintained since ~2015, amplitude-only marginalisation on a fixed
template grid). `bilby`'s core library (`Likelihood`, `PriorDict`,
nested-sampling `run_sampler`) is a general-purpose replacement for
exactly this: define an arbitrary signal model, put priors on *all* its
parameters rather than grid-searching most of them and marginalising only
amplitude in closed form, and let nested sampling produce both full
posteriors and the log-evidence that gives the odds ratio directly. This
phase reuses the idea, not the code.

### Not `bilby_pipe`

`bilby_pipe` (which `asimov-bilby` drives for CBC PE) is not a generic
"run bilby against arbitrary data" tool — its `Input`/`DataGenerationInput`
classes, ini schema, and job-splitting logic are hard-wired to `bilby.gw`:
interferometer strain data, `GravitationalWaveTransient` likelihoods,
waveform approximants, calibration/ROQ options. There is no seam for a
Gaussian-noise photometric time series with a flare/transit template —
reusing it here would mean forking most of it, not extending it. The
generic part of the stack is `bilby.core`; the config-generation/job
role `bilby_pipe` plays for GW PE is instead played by this package's own
pipeline class, the same relationship `asimov-bilby` already has with
`bilby_pipe`.

### A joint model, not separate flare/transit hypotheses

A light curve can contain both a flare and a transit in the same stretch
of data — a flare underlying a transit biases BLS's box-depth estimate,
and neither `photometry.search()` nor a flare-only Bayesian pass catches
that. Rather than testing "flare vs. noise" and "transit vs. noise" as
separate, mutually exclusive hypotheses, the PE-stage likelihood should
model the flux as a **sum of components** — one periodic transit
component (informed by the search stage's ephemeris) plus zero or more
flare components (informed by the search stage's flagged epochs) — and
let `bilby` jointly fit amplitudes, timescales, and depth with the
interaction between them properly marginalised. This is an improvement
over both BayesFlare (flare-only) and the current BLS/`vet()` design (no
flare-contamination handling at all).

### Two-stage architecture: search proposes, PE fits

Full nested-sampling PE over an entire multi-year, multi-flare light curve
in one run is not tractable, and isn't how the equivalent GW workflow
operates either — matched-filter search pipelines flag candidate times
cheaply, and PE runs only against those. The same split applies here:

1. **Search stage** — extends the existing `BLSTransitSearch`/
   `photometry.search()`: a cheap pass over the full light curve that
   proposes a *candidate list* rather than a single best period — a
   transit ephemeris (if any) from BLS, and a list of flagged flare-like
   epochs from a fast matched-filter/threshold pass (the modernised
   analogue of BayesFlare's grid scan). Output: an extended
   `results.json` carrying `{transit_candidate, flare_epochs: [...]}`.
2. **PE stage** — a new pipeline (working name `photometry-bayes-pe`),
   `needs: [transit-search]` in its blueprint, reading the search stage's
   candidate list and constructing exactly one joint `bilby.core.Likelihood`
   (transit component + one term per flagged flare epoch) with priors
   seeded around the search stage's estimates. One `run_sampler` call per
   star produces the odds ratio(s) — transit-vs-noise, and per-flare-epoch
   flare-vs-nothing, read off the nested-sampling evidences — and full
   posteriors on every component at once, properly accounting for their
   overlap.

### Ledger granularity: per star, not per candidate

One asimov `Analysis` per identified flare/transit candidate doesn't match
how the ledger is meant to scale (the same reasoning that motivates
`ProjectAnalysis` above: the payoff is at catalog scale, over targets, not
over per-target sub-events). An active flare star can produce dozens to
hundreds of candidates over a Kepler/TESS baseline; at a catalog of
thousands of targets, one ledger entry per candidate would run into the
10⁵–10⁶ range, with real per-entry overhead (rundir, DAG, submission,
monitoring). Instead:

- One search `Analysis` per star (as now).
- One PE `Analysis` per star, `needs: [search]`, whose single job
  internally loops over the star's candidate list and fits the joint
  model once — batched internally, not one ledger entry per candidate.
- Only candidates clearing some significance/interest bar (e.g. a transit
  candidate worth individual multi-sector vetting) get promoted to their
  own tracked `event`/`Analysis` — mirroring how not every search trigger
  in a GW catalog becomes a published event; most stay line items in a
  results file, a few get individually followed up.
- `SubjectAnalysis` (asimov core's aggregator over sibling analyses that
  require more than one pipeline's results — the role `PESummary` plays
  combining several `bilby` runs for one GW event) is the right mechanism
  for later aggregating multiple *promoted* candidates' PE results into
  one per-star report. It is not the right mechanism for the search stage
  itself, which is a plain `SimpleAnalysis`/pipeline production, the same
  as `BLSTransitSearch` already is — `SubjectAnalysis` resolves and
  aggregates *existing* sibling analyses, it doesn't generate new ones.

## Phased roadmap

- **Phase 0 — Scaffold** *(done)*: package skeleton, `asimov.pipelines` +
  `asimov.hooks.filesource` entry points in `pyproject.toml`, and a dummy
  pipeline (following the pattern of asimov core's
  `asimov/pipelines/testing` module — `SimpleTestPipeline` et al.) so the
  entry-point plumbing can be verified end-to-end before any real astronomy
  code exists.
- **Phase 1 — Single-target MVP** *(done)*: real MAST/`lightkurve` ingestion
  (`filesource.py`), detrending and BLS (`photometry.py`), a real
  `build_dag`/`submit_dag` (rendering `config_template.toml` via
  `production.make_config` and running the `asimov-exoplanet-bls` console
  script), `collect_assets`/`detect_completion`, a worked blueprint
  (`examples/kepler-10.yaml`, verified end-to-end including a ledger
  save/reload cycle), and unit tests against synthetic injected-transit
  light curves (no network access needed in CI -- MAST/lightkurve calls are
  mocked at the `lightkurve.search_lightcurve`/`MASTFileSource.fetch`
  boundary). `vet()` remains a stub (Phase 2).
- **Phase 2 — Vetting & reporting**: odd/even and secondary-eclipse checks,
  folded-light-curve plots, per-target report.
- **Phase 3 — Catalog-scale campaigns**: `ProjectAnalysis` support,
  HTCondor/Slurm DAG generation for batch submission, an aggregate
  report/dashboard (candidate table, completeness plots for
  injection-recovery studies).
- **Phase 4 — Stretch**: a pluggable transit-search backend
  (`transitleastsquares` as an alternative to BLS), multi-mission support
  (TESS, K2), pixel-level vetting using target pixel files.
- **Phase 5 — Bayesian signal characterization**: see
  [Bayesian signal characterization (Phase 5)](#bayesian-signal-characterization-phase-5)
  above. Extend the search stage to emit a candidate list (transit
  ephemeris + flagged flare epochs) instead of a single best period; add a
  `photometry-bayes-pe` pipeline built on `bilby.core` that jointly fits a
  transit-plus-flares model per star and reports odds ratios and
  posteriors per component; use this as `vet()`'s primary signal, in place
  of (or alongside) Phase 2's deterministic odd/even and secondary-eclipse
  checks.

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
- **How the PE stage reads the search stage's output.** `needs:` gives
  dependency ordering, but nothing in this package yet reads one
  analysis's `collect_assets()`/results file to build another analysis's
  config — that plumbing (and whether an equivalent pattern already exists
  for other asimov pipelines, e.g. `PESummary` reading multiple `bilby`
  results) needs checking before Phase 5's `photometry-bayes-pe` blueprint
  can be written concretely.
- **Promotion threshold.** What odds ratio or other criterion promotes a
  candidate from "line item in a per-star results file" to "its own
  tracked `event`/`Analysis`" needs a concrete definition, not just the
  qualitative bar described above.
- **Fixed vs. unknown component count.** Phase 5 assumes the search stage
  hands the PE stage a fixed list of components to fit (a known transit
  ephemeris, N flagged flare epochs) rather than doing trans-dimensional
  inference over an unknown number of flares. Worth revisiting only if the
  search stage's epoch-flagging turns out to be unreliable enough that
  fixing the component count materially biases results.
- **Nested-sampling cost at catalog scale.** Unlike BLS (seconds per
  target), per-star `bilby` nested sampling is unbenchmarked here. Phase 5
  should not be scoped further (e.g. default sampler settings, walltime
  budgets for `ProjectAnalysis` fan-out) until that cost is measured
  against a real multi-flare target.

## Pitfalls for anyone adding another pipeline (e.g. Phase 4's `transitleastsquares` backend)

- **A pipeline's `name` class attribute must equal its registered
  `asimov.pipelines` entry-point key (case-insensitively).**
  `asimov.analysis.Analysis.to_dict()` serializes a production's pipeline as
  `self.pipeline.name.lower()` -- not the entry-point key it was originally
  constructed with -- and reconstructs it on reload via
  `known_pipelines[pipeline.lower()]`. If the two don't match, saving and
  reloading a ledger (i.e. separate `asimov apply` / `asimov manage build`
  CLI invocations, or just quitting and restarting `asimov`) silently breaks
  pipeline lookup for any analysis using it. `BLSTransitSearch.name` and
  `DummyTransitSearchPipeline.name` are set to their entry-point keys
  (`photometry-bls`, `photometry-bls-dummy`) for exactly this reason, rather
  than to the class name -- see `tests/test_entry_points.py`'s
  `test_pipeline_name_matches_its_own_entry_point_key` and
  `tests/test_pipeline.py`'s `test_pipeline_survives_ledger_save_and_reload`
  for the regression tests this discovery produced.

## Testing strategy

Two layers, matching the pattern other Asimov pipeline plugins (e.g.
`asimov-lalinference`) use:

- **Unit tests** (`pytest`, run on every push/PR): mirror
  `asimov/pipelines/testing` in asimov core. Build a pipeline instance
  against synthetic light curves with a known injected transit (fixed
  period/depth/duration), assert that BLS recovers it within tolerance, and
  exercise `build_dag`/`detect_completion`/`collect_assets` without network
  access or a real scheduler (MAST/lightkurve calls are mocked at the
  `lightkurve.search_lightcurve`/`MASTFileSource.fetch` boundary).
- **End-to-end test** (`.github/workflows/e2e.yml`, run on every push/PR): a
  real HTCondor container (`htcondor/mini`), the real `photometry-bls`
  pipeline, `asimov apply`/`asimov manage build submit`/`asimov monitor` as
  a user actually would, against a real MAST target (Kepler-10,
  `examples/kepler-10.yaml` — the same file documented as the worked
  example, so this doubles as proof the example works). Asserts BLS
  recovers Kepler-10 b's known ~0.8375-day period from the real downloaded
  light curve, not just that a `results.json` file exists. Uses the shared
  `etive-io/actions` composite actions (`setup-htcondor`,
  `create-submit-user`, `run-asimov-command`, `wait-for-files`) that
  `asimov-lalinference`'s own `e2e.yml` uses, plus a package-local
  `setup-exoplanet-env` action (conda env + pip install, no conda-only
  dependencies needed since `astropy`/`lightkurve`/`astroquery` are all pure
  PyPI wheels).

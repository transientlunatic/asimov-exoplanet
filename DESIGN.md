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
  vetting.py             # odd/even + secondary-eclipse checks - plain functions, same pattern
  cli.py                 # asimov-exoplanet-bls / asimov-exoplanet-bls-catalog-report console scripts
  config_template.toml   # liquid-templated pipeline config
  report.py              # per-target (Phase 2) and per-catalog (Phase 3) interactive HTML reports
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
   depth consistency (`vetting.check_odd_even`) and a secondary-eclipse
   search at phase 0.5 (`vetting.check_secondary_eclipse`). Per-quarter/sector
   consistency is **not** implemented -- it needs multiple quarters/sectors
   of data, and `ingest()` currently fetches only the first
   `lightkurve.search_lightcurve` result (see "Open questions"). This is
   also **not** full centroid/pixel-level vetting (which needs target pixel
   files, not just light curves) — that's future work (Phase 4).
5. **Report** — a small machine-readable `results.json` (period, depth,
   duration, SDE, vetting flags) plus an interactive folded-light-curve
   report (`report.build_target_report`): a self-contained HTML page with a
   D3 phase-folded scatter plot (odd/even cycles coloured separately, the
   BLS box model overlaid) and a secondary-eclipse zoom panel, analogous to
   `collect_assets` for GW pipelines.

## Pipeline class

`BLSTransitSearch` subclasses `asimov.pipeline.Pipeline`, registered under
the name `photometry-bls`:

- `build_dag(dryrun=False)` — for a single-target `SimpleAnalysis`, write an
  executable job script running ingest → detrend → BLS → vet → report,
  writing `results.json` into `self.production.rundir`. For a catalog-scale
  `ProjectAnalysis`, write one such job per subject into
  `<rundir>/<subject name>/`, plus a final aggregation job (depending on
  every subject job via an HTCondor DAGMan `PARENT`/`CHILD` line) that scans
  all the per-subject `results.json` files and builds the campaign-wide
  candidate report.
- `submit_dag(dryrun=False)` — hand off to the configured scheduler
  (`asimov.scheduler.Slurm` or HTCondor). Under HTCondor this is a single
  `condor_submit_dag` call in both cases; DAGMan itself enforces the
  subject/aggregation dependency for a catalog campaign. Slurm has no
  dependency-chaining support in this plugin (see "Open questions" below),
  so for a catalog campaign each subject job is submitted independently and
  the aggregation step is **not** automatically triggered.
- `detect_completion()` — for a `SimpleAnalysis`,
  `os.path.exists(os.path.join(self.production.rundir, "results.json"))`;
  for a `ProjectAnalysis`, checks for `catalog_report.html` instead.
- `collect_assets()` — for a `SimpleAnalysis`, returns
  `{"results": .../results.json, "folded_lightcurve": .../folded_lightcurve.html}`;
  for a `ProjectAnalysis`, returns
  `{"catalog_report": .../catalog_report.html, "candidates": .../candidates.json}`.

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

A catalog-scale `ProjectAnalysis` (Phase 3; see `examples/koi-catalog.yaml` for
a worked, verified-working version of this against real KIC targets):

```yaml
kind: projectanalysis
name: koi-catalog-rerun
pipeline: photometry-bls
subjects:
  - KIC-11446443
  - KIC-11913073
comment: Re-run BLS across these KOIs with an updated detrending window
```

> **Note:** the original design sketched `kind: project_analysis` and a bare
> filename (`subjects: koi-active-list.txt`) for a list of targets. Neither
> works: `apply_page` matches blueprint kinds case-insensitively but without
> normalising underscores, so it only recognises `kind: projectanalysis` (no
> underscore) -- `project_analysis` is silently a no-op (exit 0, ledger
> unchanged). And `ProjectAnalysis.__init__` assigns `subjects:` directly to
> `self._subjects` with no file-expansion logic, so it must be an actual YAML
> list of subject names already known to the ledger (i.e. each named by a
> prior `kind: event` blueprint), not a path to a file listing them.

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
- **Phase 2 — Vetting & reporting** *(done)*: odd/even and secondary-eclipse
  checks (`vetting.py`), wired into both `BLSTransitSearch.vet()` and the
  `asimov-exoplanet-bls` console script (`cli.py`), so `results.json`'s
  `vetting_flags` are now real; an interactive per-target HTML report
  (`report.py`, D3-based) written alongside `results.json` and exposed via
  `collect_assets()`. Verified against real Kepler-10 data in the e2e
  workflow (no vetting flags raised for a genuine planet, as expected) and
  against synthetic light curves with injected eclipsing-binary-like
  signals in unit tests (`tests/test_vetting.py`). Per-quarter/sector
  consistency remains out of scope (see the roadmap item above).
- **Phase 3 — Catalog-scale campaigns** *(done)*: `ProjectAnalysis` support
  (`BLSTransitSearch._build_catalog_dag`/`_submit_catalog_dag`), an HTCondor
  DAG with one job per subject plus a dependent aggregation job, an
  aggregate candidate report (`report.build_catalog_report`, D3-based: a
  sortable candidate table linking to each target's own per-target report,
  plus a period-vs-SDE overview plot), and a worked catalog blueprint
  (`examples/koi-catalog.yaml`). Two deliberate scope cuts versus the
  original sketch: (1) Slurm catalog submission does not chain the
  aggregation job -- see "Open questions" below; (2) "completeness plots for
  injection-recovery studies" are not implemented, since they need known
  injected truth values per target that this plugin doesn't track -- left
  for a later phase.
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
- **Depth of vetting: resolved for Phase 2, revisit later.** Phase 2 shipped
  with exactly two checks (odd/even depth, secondary eclipse) and explicitly
  does not attempt per-sector consistency (needs `ingest()` to fetch and
  stitch multiple quarters/sectors, which it doesn't) or centroid/pixel-level
  vetting (needs target pixel files, not just light curves). Both remain real
  gaps versus a production-grade vetting report (e.g. the Kepler Robovetter)
  -- worth reconsidering if this plugin is ever used for anything beyond a
  smoke-test-scale demonstration.
- **Slurm catalog-campaign dependency chaining.** `asimov.scheduler.Slurm`
  exposes a simple `.submit(script_path)` with no built-in support for
  dependent job chains (unlike HTCondor DAGMan's native `PARENT`/`CHILD`).
  For a catalog `ProjectAnalysis`, `_submit_catalog_dag` currently submits
  every subject's job independently under Slurm and logs a warning that the
  aggregation step needs a manual, later
  `asimov-exoplanet-bls-catalog-report <rundir>` invocation once every
  subject job has finished. Slurm job dependencies (`sbatch --dependency=afterok:<ids>`)
  could close this gap, but haven't been implemented -- catalog campaigns in
  this plugin have only been exercised against HTCondor so far.

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
  period/depth/duration), assert that BLS recovers it within tolerance,
  assert the vetting checks pass on a clean signal and flag synthetic
  eclipsing-binary-like signals (alternating odd/even depths, an injected
  secondary eclipse), assert the HTML report embeds the right data, and
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
  light curve (not just that a `results.json` file exists), that no
  vetting flags are raised for this genuine planet, and that the HTML
  report is well-formed and carries this run's actual data. Uses the
  shared `etive-io/actions` composite actions (`setup-htcondor`,
  `create-submit-user`, `run-asimov-command`, `wait-for-files`) that
  `asimov-lalinference`'s own `e2e.yml` uses, plus a package-local
  `setup-exoplanet-env` action (conda env + pip install, no conda-only
  dependencies needed since `astropy`/`lightkurve`/`astroquery` are all pure
  PyPI wheels).

  `e2e.yml` currently only exercises the single-target (`SimpleAnalysis`)
  path via `examples/kepler-10.yaml`. `examples/koi-catalog.yaml`'s
  `ProjectAnalysis` has been verified directly (real ledger apply + real
  `BLSTransitSearch.build_dag()`, producing correct per-subject
  directories, configs, and a DAG with the right `PARENT`/`CHILD`
  aggregation dependency -- see `BLSTransitSearchCatalogDagTests` in
  `tests/test_pipeline.py`), but not yet through the full
  `asimov manage build submit`/`asimov monitor` CLI path the way the
  single-target e2e test is. Asimov core's `manage.py` has separate,
  more involved handling for `ledger.project_analyses` (interest-based
  scheduling across repeated analyses) that this plugin's minimal
  `ProjectAnalysis` usage doesn't exercise -- worth a dedicated e2e job in
  a later pass, rather than folding into this phase's already-broad scope.

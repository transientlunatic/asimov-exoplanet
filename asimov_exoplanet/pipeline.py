"""
Asimov pipeline interfaces for transiting-exoplanet searches in stellar
photometry.

This module provides two pipelines, both registered under the
``asimov.pipelines`` entry point (see ``pyproject.toml``):

``DummyTransitSearchPipeline``
    A minimal, dependency-free pipeline that mirrors asimov core's
    ``asimov.pipelines.testing.SimpleTestPipeline``. It performs no real
    astronomy, just writes a stub ``results.json``, so that the
    entry-point plumbing (pipeline discovery, ``build_dag``/``submit_dag``,
    ``detect_completion``, ``collect_assets``) can be exercised end-to-end
    without any real photometry dependencies.

``BLSTransitSearch``
    The real pipeline (``photometry-bls``): ingest a light curve via the
    ``mast`` filesource hook, detrend it (``photometry.detrend``), run a
    Box Least Squares transit search (``photometry.search``), vet the
    candidate (``vetting.vet``), and write a per-target report
    (``report.build_target_report``). See ``DESIGN.md`` for the full
    design and phased roadmap.
"""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from asimov.pipeline import Pipeline
from asimov.scheduler import Slurm

try:
    from importlib.resources import files
except ImportError:  # pragma: no cover
    from importlib_resources import files


def _ensure_rundir(rundir):
    if not rundir:
        return False
    Path(rundir).mkdir(parents=True, exist_ok=True)
    return True


def _write_transit_search_job_script(rundir, config_path):
    """
    Write the job script that actually runs a single target's transit
    search (ingest -> detrend -> BLS -> vet -> report via the
    ``asimov-exoplanet-bls`` console script). Shared between a single-target
    ``SimpleAnalysis`` job and each per-subject job in a catalog-scale
    ``ProjectAnalysis`` DAG -- the only difference between them is which
    ``rundir``/``config_path`` they're pointed at.
    """
    job_script = os.path.join(rundir, "run_transit_search.sh")
    with open(job_script, "w") as f:
        f.write("#!/bin/bash\n")
        f.write("# BLS transit-search pipeline job\n")
        f.write("set -e\n")
        f.write(f"echo 'Working directory: {rundir}'\n")
        f.write(f"asimov-exoplanet-bls {shlex.quote(config_path)} {shlex.quote(rundir)}\n")
        f.write(f"echo 'Transit search complete - {os.path.join(rundir, 'results.json')} created'\n")
    os.chmod(job_script, 0o755)
    return job_script


def _write_condor_submit_file(rundir, job_script, sub_filename):
    """
    Write an HTCondor submit description file for one job.

    See the note in ``_write_submission_files`` on why ``executable``/
    ``initialdir`` must **not** be quoted here.
    """
    submit_file = os.path.join(rundir, sub_filename)
    with open(submit_file, "w") as f:
        f.write("universe = vanilla\n")
        f.write(f"executable = {job_script}\n")
        f.write(f"initialdir = {rundir}\n")
        f.write("output = job.out\n")
        f.write("error = job.err\n")
        f.write("log = job.log\n")
        f.write("getenv = True\n")
        f.write("queue 1\n")


def _write_sbatch_wrapper(rundir, job_script, job_label, sbatch_filename="sbatch_submit.sh"):
    """Write a Slurm sbatch wrapper around a job script."""
    sbatch_file = os.path.join(rundir, sbatch_filename)
    with open(sbatch_file, "w") as f:
        f.write("#!/bin/bash\n")
        f.write(f"#SBATCH --job-name={job_label}\n")
        f.write(f"#SBATCH --output={rundir}/slurm_%j.out\n")
        f.write(f"#SBATCH --error={rundir}/slurm_%j.err\n")
        f.write("#SBATCH --ntasks=1\n")
        f.write("#SBATCH --time=00:10:00\n")
        f.write(f"\nbash {shlex.quote(job_script)}\n")
    os.chmod(sbatch_file, 0o755)
    return sbatch_file


def _write_submission_files(rundir, job_script, sub_filename, dag_filename, job_label):
    """
    Write the HTCondor submit/DAG files and a Slurm sbatch wrapper for a
    single-job pipeline run. Shared by both pipelines in this module,
    since the scheduler-submission boilerplate is identical -- only the
    job script content differs between them.
    """
    # Unlike a shell command line, HTCondor's submit-file language takes
    # `executable`/`initialdir` as the literal remainder of the line -- it
    # does not strip surrounding quotes the way a shell would, so quoting
    # these to "protect" against spaces instead makes HTCondor look for a
    # path with literal quote characters in it. Confirmed by a real e2e
    # failure: the job never even reached the schedd's queue (condor_q
    # stayed empty for the full 600s wait) once these were quoted, and
    # DAGMan wrote a rescue file instead.
    _write_condor_submit_file(rundir, job_script, sub_filename)

    dag_file = os.path.join(rundir, dag_filename)
    with open(dag_file, "w") as f:
        f.write(f"JOB job {sub_filename}\n")

    _write_sbatch_wrapper(rundir, job_script, job_label)


def _submit_to_scheduler(pipeline, dag_filename, batch_name):
    """Hand a built DAG off to whichever scheduler is configured."""
    rundir = pipeline.production.rundir
    original_dir = os.getcwd()
    os.chdir(rundir)
    try:
        if isinstance(pipeline.scheduler, Slurm):
            job_id = pipeline.scheduler.submit("sbatch_submit.sh")
            pipeline.logger.info(f"Slurm job submitted: {job_id}")
            return job_id

        command = ["condor_submit_dag", "-batch-name", batch_name, dag_filename]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        match = re.search(r"submitted to cluster (\d+)", result.stdout)
        if match:
            return int(match.group(1))
        pipeline.logger.warning("Could not extract cluster ID from condor_submit_dag output")
        return None
    except subprocess.CalledProcessError as e:
        pipeline.logger.error(f"Failed to submit DAG: {e}\nstderr: {e.stderr}")
        raise
    finally:
        os.chdir(original_dir)


class DummyTransitSearchPipeline(Pipeline):
    """
    A minimal testing pipeline for exercising the asimov-exoplanet plugin
    plumbing without any real photometry dependencies.

    This is the exoplanet-plugin analogue of asimov core's
    ``SimpleTestPipeline``: it creates a dummy ``results.json`` file rather
    than performing an actual transit search, so that pipeline discovery,
    DAG building, submission, and completion detection can all be tested
    without needing MAST access or the ``photometry``/``lightkurve``
    optional dependencies installed.

    Examples
    --------
    .. code-block:: yaml

        kind: analysis
        name: dummy-transit-search
        pipeline: photometry-bls-dummy
        status: ready
    """

    #: Asimov re-derives the pipeline to use on reload from
    #: ``known_pipelines[pipeline.name.lower()]`` (see
    #: ``asimov.analysis.Analysis.to_dict``), so this must match the
    #: registered ``asimov.pipelines`` entry-point key exactly, not just be
    #: a human-readable class name.
    name = "photometry-bls-dummy"
    STATUS = {"wait", "stuck", "stopped", "running", "finished"}

    _JOB_LABEL = "photometry-bls-dummy"
    _SUB_FILENAME = "run_dummy_transit_search.sub"
    _DAG_FILENAME = "dummy.dag"

    def __init__(self, production, category=None):
        super().__init__(production, category)
        self.logger.info("Using the DummyTransitSearchPipeline for testing")

    def build_dag(self, user=None, dryrun=False):
        """Write a job script which produces a dummy ``results.json``."""
        if dryrun:
            self.logger.info("Dry run: would build dummy transit-search DAG")
            return

        if not _ensure_rundir(self.production.rundir):
            self.logger.warning("No run directory specified, cannot build DAG")
            return

        rundir = self.production.rundir
        results_file = os.path.join(rundir, "results.json")
        job_script = os.path.join(rundir, "run_dummy_transit_search.sh")

        with open(job_script, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("# Dummy transit-search pipeline job\n")
            f.write("set -e\n")
            f.write(f"echo 'Working directory: {rundir}'\n")
            f.write(
                "python3 -c \"import json; "
                "json.dump({'period': 1.0, 'epoch': 0.0, 'duration': 0.1, "
                "'depth': 0.001, 'sde': 10.0, 'vetting_flags': []}, "
                f"open('{results_file}', 'w'))\"\n"
            )
            f.write(f"echo 'Dummy transit search complete - {results_file} created'\n")
        os.chmod(job_script, 0o755)

        _write_submission_files(
            rundir, job_script, self._SUB_FILENAME, self._DAG_FILENAME,
            job_label=f"{self._JOB_LABEL}/{self.production.name}",
        )

        self.logger.info(f"Built dummy transit-search DAG in {rundir}")

    def submit_dag(self, dryrun=False):
        """Submit the job to the configured scheduler."""
        if not self.production.rundir:
            self.logger.warning("No run directory specified, cannot submit job")
            return None

        self.build_dag(dryrun=dryrun)
        self.before_submit(dryrun=dryrun)

        if dryrun:
            self.logger.info("Dry run: would submit dummy transit-search DAG")
            return 90210

        return _submit_to_scheduler(
            self, self._DAG_FILENAME, batch_name=f"{self._JOB_LABEL}/{self.production.name}"
        )

    def before_submit(self, dryrun=False):
        if not dryrun and _ensure_rundir(self.production.rundir):
            self.logger.info(f"Prepared run directory: {self.production.rundir}")

    def detect_completion(self):
        if not self.production.rundir:
            return False
        return os.path.exists(os.path.join(self.production.rundir, "results.json"))

    def after_completion(self):
        if self.production.rundir:
            _ensure_rundir(self.production.rundir)
            results_file = os.path.join(self.production.rundir, "results.json")
            if not os.path.exists(results_file):
                with open(results_file, "w") as f:
                    json.dump(
                        {
                            "period": 1.0,
                            "epoch": 0.0,
                            "duration": 0.1,
                            "depth": 0.001,
                            "sde": 10.0,
                            "vetting_flags": [],
                        },
                        f,
                    )
        super().after_completion()
        self.production.status = "complete"

    def collect_assets(self):
        assets = {}
        if self.production.rundir:
            results = os.path.join(self.production.rundir, "results.json")
            if os.path.exists(results):
                assets["results"] = results
        return assets


class BLSTransitSearch(Pipeline):
    """
    Box Least Squares transit-search pipeline (``photometry-bls``).

    Runs: ingest a light curve for the subject's catalog ID via the
    ``mast`` filesource hook -> detrend it -> Box Least Squares transit
    search -> vet the candidate (odd/even depth, secondary eclipse) ->
    write a machine-readable ``results.json`` and an interactive
    folded-light-curve report. See ``DESIGN.md`` for the full design and
    phased roadmap.

    Requires the ``photometry`` extra (``pip install asimov-exoplanet[photometry]``)
    for ``astropy``/``lightkurve``/``astroquery``.

    .. note::
       Vetting is deliberately limited to cheap, deterministic checks on
       the single light curve already fetched (odd/even depth, secondary
       eclipse) -- see ``asimov_exoplanet.vetting``. Per-sector consistency
       and centroid/pixel-level vetting are out of scope for this MVP.

    Examples
    --------
    .. code-block:: yaml

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

    (See ``examples/kepler-10.yaml`` for a verified-working version of this
    against a real target, and the note in ``DESIGN.md`` on why ``event``
    is used here rather than ``subject``.)
    """

    #: Must match the registered ``asimov.pipelines`` entry-point key
    #: exactly -- see the note on ``DummyTransitSearchPipeline.name`` above.
    name = "photometry-bls"
    STATUS = {"wait", "stuck", "stopped", "running", "finished"}

    _JOB_LABEL = "photometry-bls"
    _SUB_FILENAME = "run_transit_search.sub"
    _DAG_FILENAME = "transit_search.dag"
    _CONFIG_FILENAME = "photometry-bls.toml"
    _AGGREGATE_SUB_FILENAME = "run_catalog_report.sub"

    #: Used by ``asimov.analysis.Analysis.make_config`` when no
    #: ``[templating] directory`` is configured for the project.
    config_template = str(files("asimov_exoplanet").joinpath("config_template.toml"))

    def __init__(self, production, category=None):
        super().__init__(production, category)
        self.logger.info("Using the BLSTransitSearch pipeline")

    def ingest(self):
        """
        Fetch the light curve for this subject's catalog ID via the
        ``mast`` filesource hook, and cache the FITS file under the run
        directory.

        Returns
        -------
        lightkurve.LightCurve
        """
        from asimov import config as asimov_config

        from .filesource import MASTFileSource

        photometry_meta = self.production.subject.meta.get("photometry", {})
        catalog_id = photometry_meta.get("catalog id")
        if catalog_id is None:
            raise ValueError(
                f"Subject {self.production.subject.name!r} has no "
                "'photometry: catalog id' in its metadata."
            )
        mission = photometry_meta.get("mission", "Kepler")

        if not _ensure_rundir(self.production.rundir):
            raise ValueError(
                f"Analysis {self.production.name!r} has no run directory configured; "
                "cannot cache the ingested light curve."
            )
        client = MASTFileSource(asimov_config)
        fits_bytes = client.fetch(catalog_id, mission=mission)

        fits_path = os.path.join(self.production.rundir, "light_curve.fits")
        with open(fits_path, "wb") as f:
            f.write(fits_bytes)

        import lightkurve as lk

        return lk.read(fits_path)

    def detrend(self, light_curve):
        """Remove stellar variability/instrumental trends from the light curve."""
        from . import photometry

        window_length = self.production.meta.get("detrend", {}).get("window length", 0.5)
        return photometry.detrend(light_curve, window_length=window_length)

    def search(self, light_curve):
        """Run a Box Least Squares transit search over a period grid."""
        from . import photometry

        bls_meta = self.production.meta.get("bls", {})
        return photometry.search(
            light_curve,
            period_min=bls_meta.get("period min", 0.5),
            period_max=bls_meta.get("period max", 20.0),
            duration_grid=bls_meta.get("duration grid", [0.05, 0.10, 0.20]),
        )

    def vet(self, light_curve, search_result):
        """
        Run cheap deterministic vetting checks on a candidate transit signal.

        See ``asimov_exoplanet.vetting`` for what's actually checked
        (odd/even transit-depth consistency, a secondary-eclipse search)
        and what's deliberately out of scope for this MVP (per-sector
        consistency, centroid/pixel-level vetting).
        """
        from . import vetting

        return vetting.vet(light_curve, search_result)

    def build_dag(self, user=None, dryrun=False):
        """
        Build the executable pipeline for this analysis.

        For a single-target ``SimpleAnalysis``, renders this subject's
        config via ``self.config_template`` and writes a job script that
        invokes the ``asimov-exoplanet-bls`` console script (ingest ->
        detrend -> BLS), producing ``results.json`` in
        ``self.production.rundir``. For a catalog-scale ``ProjectAnalysis``,
        delegates to ``_build_catalog_dag``.
        """
        from asimov.analysis import ProjectAnalysis

        if isinstance(self.production, ProjectAnalysis):
            return self._build_catalog_dag(dryrun=dryrun)

        if dryrun:
            self.logger.info("Dry run: would build transit-search DAG")
            return

        if not _ensure_rundir(self.production.rundir):
            self.logger.warning("No run directory specified, cannot build DAG")
            return

        rundir = self.production.rundir
        config_path = os.path.join(rundir, self._CONFIG_FILENAME)
        self.production.make_config(config_path)

        job_script = _write_transit_search_job_script(rundir, config_path)

        _write_submission_files(
            rundir, job_script, self._SUB_FILENAME, self._DAG_FILENAME,
            job_label=f"{self._JOB_LABEL}/{self.production.name}",
        )

        self.logger.info(f"Built transit-search DAG in {rundir}")

    def _render_subject_config(self, subject, config_path):
        """
        Render one subject's config for a catalog-scale campaign.

        ``ProjectAnalysis`` has no single ``.subject`` (only ``.subjects``,
        plural), so ``production.make_config()`` -- which assumes exactly
        that -- can't be used here. This reimplements the same
        Liquid-rendering step it uses internally, against a small
        per-subject view object exposing ``.subject`` (this one target)
        and ``.meta`` (the project's own campaign-wide settings, e.g. a
        shared ``detrend``/``bls`` override applied uniformly across
        every target in the campaign).
        """
        from liquid.liquid import Liquid

        from asimov import config as asimov_config

        class _SubjectView:
            def __init__(self, subject, meta):
                self.subject = subject
                self.meta = meta

        view = _SubjectView(subject, self.production.meta)
        liq = Liquid(self.config_template)
        rendered = liq.render(production=view, analysis=view, pipeline=self, config=asimov_config)
        with open(config_path, "w") as f:
            f.write(rendered)

    def _build_catalog_dag(self, dryrun=False):
        """
        Build a single HTCondor DAG for a catalog-scale campaign: one
        independent job per subject (ingest -> detrend -> BLS -> vet ->
        report, exactly as for a single-target analysis, each in its own
        ``<rundir>/<subject name>/`` subdirectory), plus a final
        aggregation job -- depending on every subject job via a DAGMan
        PARENT/CHILD line -- that scans all the per-subject
        ``results.json`` files and writes a catalog-wide candidate report
        (``asimov-exoplanet-bls-catalog-report``, see ``cli.py``).

        Slurm support is deliberately more limited: see
        ``_submit_catalog_dag``.
        """
        if dryrun:
            self.logger.info("Dry run: would build catalog transit-search DAG")
            return

        if not _ensure_rundir(self.production.rundir):
            self.logger.warning("No run directory specified, cannot build DAG")
            return

        rundir = self.production.rundir
        subjects = self.production.subjects
        if not subjects:
            self.logger.warning("Project analysis has no subjects, cannot build DAG")
            return

        dag_lines = []
        job_labels = []
        for subject in subjects:
            subject_rundir = os.path.join(rundir, subject.name)
            _ensure_rundir(subject_rundir)

            config_path = os.path.join(subject_rundir, self._CONFIG_FILENAME)
            self._render_subject_config(subject, config_path)

            job_script = _write_transit_search_job_script(subject_rundir, config_path)
            _write_condor_submit_file(subject_rundir, job_script, self._SUB_FILENAME)
            _write_sbatch_wrapper(subject_rundir, job_script, job_label=f"{self._JOB_LABEL}/{subject.name}")

            job_label = f"target_{subject.name}"
            dag_lines.append(f"JOB {job_label} {os.path.join(subject.name, self._SUB_FILENAME)}\n")
            job_labels.append(job_label)

        aggregate_script = os.path.join(rundir, "run_catalog_report.sh")
        with open(aggregate_script, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("# Catalog aggregation job: builds the candidate report once every\n")
            f.write("# target's transit search has finished.\n")
            f.write("set -e\n")
            f.write(f"asimov-exoplanet-bls-catalog-report {shlex.quote(rundir)}\n")
        os.chmod(aggregate_script, 0o755)
        _write_condor_submit_file(rundir, aggregate_script, self._AGGREGATE_SUB_FILENAME)

        dag_lines.append(f"JOB aggregate {self._AGGREGATE_SUB_FILENAME}\n")
        dag_lines.append(f"PARENT {' '.join(job_labels)} CHILD aggregate\n")

        with open(os.path.join(rundir, self._DAG_FILENAME), "w") as f:
            f.writelines(dag_lines)

        self.logger.info(f"Built catalog transit-search DAG for {len(subjects)} subjects in {rundir}")

    def submit_dag(self, dryrun=False):
        """Hand off the built DAG to the configured scheduler."""
        from asimov.analysis import ProjectAnalysis

        if not self.production.rundir:
            self.logger.warning("No run directory specified, cannot submit job")
            return None

        self.build_dag(dryrun=dryrun)
        self.before_submit(dryrun=dryrun)

        if dryrun:
            self.logger.info("Dry run: would submit transit-search DAG")
            return 24601

        if isinstance(self.production, ProjectAnalysis):
            return self._submit_catalog_dag()

        return _submit_to_scheduler(
            self, self._DAG_FILENAME, batch_name=f"{self._JOB_LABEL}/{self.production.name}"
        )

    def _submit_catalog_dag(self):
        """
        Submit the catalog DAG built by ``_build_catalog_dag``.

        Under HTCondor this is a single ``condor_submit_dag`` call --
        DAGMan itself handles the per-subject/aggregation PARENT/CHILD
        dependency. Slurm has no equivalent dependency chaining wired up
        in this plugin, so each subject's job is submitted independently
        and the aggregation step is *not* automatically triggered; it
        would need a separate, later
        ``asimov-exoplanet-bls-catalog-report <rundir>`` invocation once
        every subject job has finished. See DESIGN.md.
        """
        if isinstance(self.scheduler, Slurm):
            rundir = self.production.rundir
            # Resolve subject names before changing directory: each access to
            # `.subjects` re-resolves the event via the ledger, which
            # re-opens its git checkout using a path relative to the
            # project root -- so doing this after os.chdir(rundir) below
            # would break checkout resolution for every subject.
            subject_names = [subject.name for subject in self.production.subjects]

            job_ids = []
            original_dir = os.getcwd()
            os.chdir(rundir)
            try:
                for name in subject_names:
                    sbatch_script = os.path.join(name, "sbatch_submit.sh")
                    job_id = self.scheduler.submit(sbatch_script)
                    job_ids.append(job_id)
                    self.logger.info(f"Slurm job submitted for {name}: {job_id}")
            finally:
                os.chdir(original_dir)
            self.logger.warning(
                "Slurm catalog submission does not chain the aggregation step -- "
                f"run 'asimov-exoplanet-bls-catalog-report {rundir}' manually "
                "once every subject job finishes."
            )
            return job_ids

        return _submit_to_scheduler(
            self, self._DAG_FILENAME, batch_name=f"{self._JOB_LABEL}/{self.production.name}"
        )

    def before_submit(self, dryrun=False):
        if not dryrun and _ensure_rundir(self.production.rundir):
            self.logger.info(f"Prepared run directory: {self.production.rundir}")

    def detect_completion(self):
        from asimov.analysis import ProjectAnalysis

        if not self.production.rundir:
            return False
        if isinstance(self.production, ProjectAnalysis):
            return os.path.exists(os.path.join(self.production.rundir, "catalog_report.html"))
        return os.path.exists(os.path.join(self.production.rundir, "results.json"))

    def collect_assets(self):
        from asimov.analysis import ProjectAnalysis

        assets = {}
        if not self.production.rundir:
            return assets

        if isinstance(self.production, ProjectAnalysis):
            catalog_report = os.path.join(self.production.rundir, "catalog_report.html")
            if os.path.exists(catalog_report):
                assets["catalog_report"] = catalog_report
            candidates = os.path.join(self.production.rundir, "candidates.json")
            if os.path.exists(candidates):
                assets["candidates"] = candidates
            return assets

        results = os.path.join(self.production.rundir, "results.json")
        if os.path.exists(results):
            assets["results"] = results
        report_path = os.path.join(self.production.rundir, "folded_lightcurve.html")
        if os.path.exists(report_path):
            assets["folded_lightcurve"] = report_path
        return assets

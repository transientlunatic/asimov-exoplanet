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
    without any of the Phase 1 ingestion/detrending/BLS code in place.

``BLSTransitSearch``
    The real pipeline (``photometry-bls``): ingest a light curve, detrend
    it, run a Box Least Squares transit search, vet the result, and write a
    report. See ``DESIGN.md`` for the full phased roadmap. The pipeline
    stages are not yet implemented (Phase 1); this class currently defines
    the intended interface and raises ``NotImplementedError`` in the stage
    methods.
"""

import json
import os
from pathlib import Path

from asimov.pipeline import Pipeline


class DummyTransitSearchPipeline(Pipeline):
    """
    A minimal testing pipeline for exercising the asimov-exoplanet plugin
    plumbing without any real photometry dependencies.

    This is the exoplanet-plugin analogue of asimov core's
    ``SimpleTestPipeline``: it creates a dummy ``results.json`` file rather
    than performing an actual transit search, so that pipeline discovery,
    DAG building, submission, and completion detection can all be tested
    before Phase 1 (real MAST ingestion, detrending, BLS) is implemented.

    Examples
    --------
    .. code-block:: yaml

        kind: analysis
        name: dummy-transit-search
        pipeline: photometry-bls-dummy
        status: ready
    """

    name = "DummyTransitSearchPipeline"
    STATUS = {"wait", "stuck", "stopped", "running", "finished"}

    def __init__(self, production, category=None):
        super().__init__(production, category)
        self.logger.info("Using the DummyTransitSearchPipeline for testing")

    def _ensure_rundir(self):
        if not self.production.rundir:
            return False
        Path(self.production.rundir).mkdir(parents=True, exist_ok=True)
        return True

    def build_dag(self, user=None, dryrun=False):
        """Write a job script which produces a dummy ``results.json``."""
        if dryrun:
            self.logger.info("Dry run: would build dummy transit-search DAG")
            return

        if not self._ensure_rundir():
            self.logger.warning("No run directory specified, cannot build DAG")
            return

        results_file = os.path.join(self.production.rundir, "results.json")
        job_script = os.path.join(self.production.rundir, "run_dummy_transit_search.sh")

        with open(job_script, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("# Dummy transit-search pipeline job\n")
            f.write("set -e\n")
            f.write(f"echo 'Working directory: {self.production.rundir}'\n")
            f.write(
                "python -c \"import json; "
                "json.dump({'period': 1.0, 'epoch': 0.0, 'duration': 0.1, "
                "'depth': 0.001, 'sde': 10.0, 'vetting_flags': []}, "
                f"open('{results_file}', 'w'))\"\n"
            )
            f.write(f"echo 'Dummy transit search complete - {results_file} created'\n")
        os.chmod(job_script, 0o755)

        submit_file = os.path.join(self.production.rundir, "run_dummy_transit_search.sub")
        with open(submit_file, "w") as f:
            f.write("universe = vanilla\n")
            f.write(f"executable = {job_script}\n")
            f.write(f"initialdir = {self.production.rundir}\n")
            f.write("output = dummy_job.out\n")
            f.write("error = dummy_job.err\n")
            f.write("log = dummy_job.log\n")
            f.write("getenv = True\n")
            f.write("queue 1\n")

        dag_file = os.path.join(self.production.rundir, "dummy.dag")
        with open(dag_file, "w") as f:
            f.write("JOB dummy_job run_dummy_transit_search.sub\n")

        sbatch_file = os.path.join(self.production.rundir, "sbatch_submit.sh")
        with open(sbatch_file, "w") as f:
            f.write("#!/bin/bash\n")
            f.write(f"#SBATCH --job-name=photometry-bls-dummy/{self.production.name}\n")
            f.write(f"#SBATCH --output={self.production.rundir}/slurm_%j.out\n")
            f.write(f"#SBATCH --error={self.production.rundir}/slurm_%j.err\n")
            f.write("#SBATCH --ntasks=1\n")
            f.write("#SBATCH --time=00:10:00\n")
            f.write(f"\nbash {job_script}\n")
        os.chmod(sbatch_file, 0o755)

        self.logger.info(f"Built dummy transit-search DAG in {self.production.rundir}")

    def submit_dag(self, dryrun=False):
        """Submit the job to the configured scheduler."""
        import re
        import subprocess

        from asimov.scheduler import Slurm

        if not self.production.rundir:
            self.logger.warning("No run directory specified, cannot submit job")
            return None

        self.build_dag(dryrun=dryrun)
        self.before_submit(dryrun=dryrun)

        if dryrun:
            self.logger.info("Dry run: would submit dummy transit-search DAG")
            return 90210

        original_dir = os.getcwd()
        os.chdir(self.production.rundir)
        try:
            if isinstance(self.scheduler, Slurm):
                job_id = self.scheduler.submit("sbatch_submit.sh")
                self.logger.info(f"Slurm job submitted: {job_id}")
                return job_id

            command = [
                "condor_submit_dag",
                "-batch-name",
                f"photometry-bls-dummy/{self.production.name}",
                "dummy.dag",
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            match = re.search(r"submitted to cluster (\d+)", result.stdout)
            if match:
                return int(match.group(1))
            self.logger.warning("Could not extract cluster ID from condor_submit_dag output")
            return None
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to submit DAG: {e}\nstderr: {e.stderr}")
            raise
        finally:
            os.chdir(original_dir)

    def before_submit(self, dryrun=False):
        if not dryrun and self._ensure_rundir():
            self.logger.info(f"Prepared run directory: {self.production.rundir}")

    def detect_completion(self):
        if not self.production.rundir:
            return False
        return os.path.exists(os.path.join(self.production.rundir, "results.json"))

    def after_completion(self):
        if self.production.rundir:
            self._ensure_rundir()
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

    Runs: ingest a light curve for the subject's catalog ID -> detrend ->
    Box Least Squares transit search -> cheap deterministic vetting ->
    write a machine-readable report. See ``DESIGN.md`` for the full design
    and phased roadmap.

    .. note::
       This is a Phase 0 scaffold. The pipeline stages below are not yet
       implemented -- see the phased roadmap in ``DESIGN.md``. Use
       ``DummyTransitSearchPipeline`` (entry point ``photometry-bls-dummy``)
       to exercise the surrounding Asimov plumbing in the meantime.

    Examples
    --------
    .. code-block:: yaml

        kind: analysis
        name: transit-search
        pipeline: photometry-bls
        comment: BLS transit search on Kepler-10
    """

    name = "BLSTransitSearch"
    STATUS = {"wait", "stuck", "stopped", "running", "finished"}

    def __init__(self, production, category=None):
        super().__init__(production, category)
        self.logger.info("Using the BLSTransitSearch pipeline")

    def _ensure_rundir(self):
        if not self.production.rundir:
            return False
        Path(self.production.rundir).mkdir(parents=True, exist_ok=True)
        return True

    def ingest(self):
        """Fetch the light curve for this subject via the ``mast`` filesource hook."""
        raise NotImplementedError("Phase 1: MAST/lightkurve ingestion is not yet implemented.")

    def detrend(self, light_curve):
        """Remove stellar variability/instrumental trends from the light curve."""
        raise NotImplementedError("Phase 1: detrending is not yet implemented.")

    def search(self, light_curve):
        """Run a Box Least Squares transit search over a period grid."""
        raise NotImplementedError("Phase 1: BLS transit search is not yet implemented.")

    def vet(self, light_curve, search_result):
        """Run cheap deterministic vetting checks on a candidate transit signal."""
        raise NotImplementedError("Phase 2: vetting checks are not yet implemented.")

    def build_dag(self, user=None, dryrun=False):
        """
        Build the executable pipeline for this subject.

        For Phase 1 (single-target ``SimpleAnalysis``) this writes a script
        running ingest -> detrend -> BLS -> vet -> report and producing
        ``results.json`` in ``self.production.rundir``. For Phase 3
        (catalog-scale ``ProjectAnalysis``) this will instead write one row
        of an HTCondor/Slurm DAG per subject.
        """
        raise NotImplementedError(
            "Phase 1: build_dag is not yet implemented. "
            "Use the 'photometry-bls-dummy' pipeline to exercise the "
            "surrounding Asimov plumbing in the meantime."
        )

    def submit_dag(self, dryrun=False):
        """Hand off the built DAG to the configured scheduler."""
        raise NotImplementedError(
            "Phase 1: submit_dag is not yet implemented. "
            "Use the 'photometry-bls-dummy' pipeline to exercise the "
            "surrounding Asimov plumbing in the meantime."
        )

    def detect_completion(self):
        if not self.production.rundir:
            return False
        return os.path.exists(os.path.join(self.production.rundir, "results.json"))

    def collect_assets(self):
        assets = {}
        if self.production.rundir:
            results = os.path.join(self.production.rundir, "results.json")
            if os.path.exists(results):
                assets["results"] = results
            plot = os.path.join(self.production.rundir, "folded_lightcurve.png")
            if os.path.exists(plot):
                assets["folded_lightcurve"] = plot
        return assets

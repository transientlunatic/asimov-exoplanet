"""
Unit tests for the Phase 0 pipeline scaffold.

These exercise ``DummyTransitSearchPipeline`` (build_dag/submit_dag/
detect_completion/collect_assets) against a minimal duck-typed "production"
object, without requiring a full asimov ledger/project to be set up. They
also confirm ``BLSTransitSearch`` -- the not-yet-implemented Phase 1
pipeline -- exposes the intended interface and fails loudly rather than
silently.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from asimov.scheduler import Slurm

from asimov_exoplanet.pipeline import BLSTransitSearch, DummyTransitSearchPipeline


class FakeEvent:
    def __init__(self, name):
        self.name = name


class FakeProduction:
    """A minimal stand-in for asimov.analysis.SimpleAnalysis."""

    def __init__(self, name, rundir):
        self.name = name
        self.rundir = rundir
        self.event = FakeEvent(f"{name}-target")
        self.category = None


class DummyTransitSearchPipelineTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.rundir = os.path.join(self.test_dir, "run")
        self.production = FakeProduction("dummy-transit-search", self.rundir)
        self.pipeline = DummyTransitSearchPipeline(self.production)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_build_dag_creates_job_files(self):
        self.pipeline.build_dag()

        self.assertTrue(os.path.exists(self.rundir))
        self.assertTrue(
            os.path.exists(os.path.join(self.rundir, "run_dummy_transit_search.sh"))
        )
        self.assertTrue(
            os.path.exists(os.path.join(self.rundir, "run_dummy_transit_search.sub"))
        )
        self.assertTrue(os.path.exists(os.path.join(self.rundir, "sbatch_submit.sh")))

    def test_build_dag_dryrun_creates_nothing(self):
        self.pipeline.build_dag(dryrun=True)
        self.assertFalse(os.path.exists(self.rundir))

    def test_detect_completion_before_and_after(self):
        self.assertFalse(self.pipeline.detect_completion())

        os.makedirs(self.rundir, exist_ok=True)
        with open(os.path.join(self.rundir, "results.json"), "w") as f:
            json.dump({}, f)

        self.assertTrue(self.pipeline.detect_completion())

    def test_after_completion_writes_results_and_collect_assets(self):
        self.pipeline.after_completion()

        self.assertEqual(self.production.status, "complete")
        assets = self.pipeline.collect_assets()
        self.assertIn("results", assets)

        with open(assets["results"]) as f:
            results = json.load(f)
        self.assertIn("period", results)
        self.assertIn("sde", results)

    @patch("subprocess.run")
    def test_submit_dag_condor(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "1 job(s) submitted to cluster 90210."
        mock_run.return_value = mock_result

        job_id = self.pipeline.submit_dag()

        self.assertEqual(job_id, 90210)
        self.assertTrue(os.path.exists(self.rundir))

    def test_submit_dag_slurm(self):
        self.pipeline._scheduler = Slurm()
        self.pipeline._scheduler.submit = MagicMock(return_value=13579)

        job_id = self.pipeline.submit_dag()

        self.assertEqual(job_id, 13579)
        self.pipeline._scheduler.submit.assert_called_once()

    def test_submit_dag_dryrun_does_not_touch_scheduler(self):
        job_id = self.pipeline.submit_dag(dryrun=True)
        self.assertEqual(job_id, 90210)


class BLSTransitSearchStubTests(unittest.TestCase):
    """The Phase 1 pipeline is a stub: it should fail loudly, not silently."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.production = FakeProduction("bls-run", os.path.join(self.test_dir, "run"))
        self.pipeline = BLSTransitSearch(self.production)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_build_dag_not_yet_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.pipeline.build_dag()

    def test_submit_dag_not_yet_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.pipeline.submit_dag()

    def test_detect_completion_is_false_when_no_rundir_contents(self):
        self.assertFalse(self.pipeline.detect_completion())

    def test_collect_assets_empty_when_nothing_produced(self):
        self.assertEqual(self.pipeline.collect_assets(), {})


if __name__ == "__main__":
    unittest.main()

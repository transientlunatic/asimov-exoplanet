"""
Unit tests for the pipeline scaffold and, for ``BLSTransitSearch``, the
Phase 1 lifecycle.

``DummyTransitSearchPipeline`` and most of ``BLSTransitSearch`` (ingest,
detect_completion, collect_assets, vet) are exercised against a minimal
duck-typed "production" object, without requiring a full asimov
ledger/project to be set up. ``BLSTransitSearch.build_dag``, however,
renders a real Liquid config template via ``production.make_config`` and
needs a genuine ``asimov.analysis.SimpleAnalysis`` -- that's covered
separately in ``BLSTransitSearchBuildDagTests`` below, using the same
temporary-project pattern asimov core's own pipeline tests use.
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
    def __init__(self, name, meta=None):
        self.name = name
        self.meta = meta or {}


class FakeProduction:
    """A minimal stand-in for asimov.analysis.SimpleAnalysis."""

    def __init__(self, name, rundir, subject_meta=None, meta=None):
        self.name = name
        self.rundir = rundir
        self.event = self.subject = FakeEvent(f"{name}-target", meta=subject_meta)
        self.category = None
        self.meta = meta or {}


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


class BLSTransitSearchLifecycleTests(unittest.TestCase):
    """
    Exercise the parts of ``BLSTransitSearch`` that only need the subject's
    metadata and the run directory, not a real templated config file.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.rundir = os.path.join(self.test_dir, "run")
        self.production = FakeProduction(
            "bls-run",
            self.rundir,
            subject_meta={"photometry": {"mission": "Kepler", "catalog id": 11446443}},
        )
        self.pipeline = BLSTransitSearch(self.production)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_vet_delegates_to_vetting_module(self):
        sentinel_light_curve = object()
        sentinel_search_result = {"period": 1.0, "epoch": 0.0, "duration": 0.1}
        with patch("asimov_exoplanet.vetting.vet", return_value={"flags": []}) as mock_vet:
            result = self.pipeline.vet(sentinel_light_curve, sentinel_search_result)
        mock_vet.assert_called_once_with(sentinel_light_curve, sentinel_search_result)
        self.assertEqual(result, {"flags": []})

    def test_detect_completion_is_false_when_no_rundir_contents(self):
        self.assertFalse(self.pipeline.detect_completion())

    def test_collect_assets_empty_when_nothing_produced(self):
        self.assertEqual(self.pipeline.collect_assets(), {})

    def test_collect_assets_includes_results_and_plot_when_present(self):
        os.makedirs(self.rundir, exist_ok=True)
        for filename in ("results.json", "folded_lightcurve.html"):
            with open(os.path.join(self.rundir, filename), "w") as f:
                f.write("placeholder")

        assets = self.pipeline.collect_assets()
        self.assertIn("results", assets)
        self.assertIn("folded_lightcurve", assets)

    def test_ingest_requires_catalog_id(self):
        production = FakeProduction("no-catalog-id", self.rundir, subject_meta={})
        pipeline = BLSTransitSearch(production)

        with self.assertRaises(ValueError):
            pipeline.ingest()

    def test_ingest_requires_a_configured_rundir(self):
        production = FakeProduction(
            "no-rundir",
            rundir=None,
            subject_meta={"photometry": {"mission": "Kepler", "catalog id": 11446443}},
        )
        pipeline = BLSTransitSearch(production)

        with self.assertRaises(ValueError):
            pipeline.ingest()

    @patch("lightkurve.read")
    @patch("asimov_exoplanet.filesource.MASTFileSource")
    def test_ingest_fetches_and_caches_light_curve(self, mock_filesource_cls, mock_lk_read):
        mock_client = mock_filesource_cls.return_value
        mock_client.fetch.return_value = b"fake fits bytes"
        sentinel_light_curve = object()
        mock_lk_read.return_value = sentinel_light_curve

        result = self.pipeline.ingest()

        mock_client.fetch.assert_called_once_with(11446443, mission="Kepler")
        self.assertIs(result, sentinel_light_curve)

        cached_path = os.path.join(self.rundir, "light_curve.fits")
        self.assertTrue(os.path.exists(cached_path))
        with open(cached_path, "rb") as f:
            self.assertEqual(f.read(), b"fake fits bytes")

    def test_detrend_and_search_delegate_to_photometry_module_with_meta_defaults(self):
        production = FakeProduction(
            "bls-run",
            self.rundir,
            subject_meta={"photometry": {"mission": "Kepler", "catalog id": 11446443}},
            meta={
                "detrend": {"window length": 0.3},
                "bls": {"period min": 1.0, "period max": 5.0, "duration grid": [0.05, 0.1]},
            },
        )
        pipeline = BLSTransitSearch(production)
        sentinel_light_curve = object()
        sentinel_flattened = object()

        with patch("asimov_exoplanet.photometry.detrend", return_value=sentinel_flattened) as mock_detrend:
            result = pipeline.detrend(sentinel_light_curve)
            mock_detrend.assert_called_once_with(sentinel_light_curve, window_length=0.3)
            self.assertIs(result, sentinel_flattened)

        with patch("asimov_exoplanet.photometry.search", return_value={"period": 2.0}) as mock_search:
            result = pipeline.search(sentinel_flattened)
            mock_search.assert_called_once_with(
                sentinel_flattened, period_min=1.0, period_max=5.0, duration_grid=[0.05, 0.1]
            )
            self.assertEqual(result, {"period": 2.0})


class BLSTransitSearchBuildDagTests(unittest.TestCase):
    """
    ``build_dag`` renders this subject's config via
    ``production.make_config``, which needs a real
    ``asimov.analysis.SimpleAnalysis`` -- so this test stands up a
    throwaway asimov project, the same way asimov core's own pipeline
    tests do (see ``tests/test_pipelines/test_testing_pipelines.py`` in
    asimov core).
    """

    @classmethod
    def setUpClass(cls):
        cls.cwd = os.getcwd()

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        os.chdir(self.test_dir)

        from click.testing import CliRunner

        from asimov.cli import project
        from asimov.cli.application import apply_page
        from asimov.ledger import YAMLLedger

        runner = CliRunner()
        result = runner.invoke(project.init, ["Test Project", "--root", self.test_dir])
        self.assertEqual(result.exit_code, 0, result.output)
        self.ledger = YAMLLedger(os.path.join(self.test_dir, ".asimov", "ledger.yml"))

        blueprint = os.path.join(self.test_dir, "subject.yaml")
        with open(blueprint, "w") as f:
            f.write(
                "kind: event\n"
                "name: KIC-11446443\n"
                "photometry:\n"
                "  mission: Kepler\n"
                "  catalog id: 11446443\n"
            )
        apply_page(file=blueprint, event=None, ledger=self.ledger)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_build_dag_renders_config_and_writes_job_script(self):
        from asimov.analysis import SimpleAnalysis

        event = self.ledger.get_event("KIC-11446443")[0]
        rundir = os.path.join(self.test_dir, "run")

        analysis = SimpleAnalysis(
            subject=event,
            name="transit-search",
            pipeline="photometry-bls",
            status="ready",
            ledger=self.ledger,
            rundir=rundir,
            detrend={"window length": 0.3},
            bls={"period min": 1.0, "period max": 5.0, "duration grid": [0.05, 0.1]},
        )

        self.assertIsInstance(analysis.pipeline, BLSTransitSearch)
        analysis.pipeline.build_dag()

        config_path = os.path.join(rundir, "photometry-bls.toml")
        self.assertTrue(os.path.exists(config_path))
        with open(config_path) as f:
            rendered = f.read()

        self.assertIn("catalog_id = 11446443", rendered)
        self.assertIn('mission = "Kepler"', rendered)
        self.assertIn("window_length = 0.3", rendered)
        self.assertIn("period_min = 1.0", rendered)
        self.assertIn("period_max = 5.0", rendered)

        job_script = os.path.join(rundir, "run_transit_search.sh")
        self.assertTrue(os.path.exists(job_script))
        with open(job_script) as f:
            script = f.read()
        self.assertIn("asimov-exoplanet-bls", script)
        self.assertIn(config_path, script)

    def test_sub_file_does_not_quote_executable_or_initialdir(self):
        """
        Regression test: HTCondor's submit-file language takes
        ``executable``/``initialdir`` as the literal remainder of the line --
        it does not strip surrounding quotes the way a shell would. Wrapping
        them in quotes (to "protect" against spaces, as a general-purpose
        code reviewer might suggest) makes HTCondor look for a path with
        literal quote characters in it, so the job never even reaches the
        schedd's queue. This was caught by the real e2e workflow, not by
        this unit test suite -- add the check here too so it can't
        regress silently again.
        """
        from asimov.analysis import SimpleAnalysis

        event = self.ledger.get_event("KIC-11446443")[0]
        rundir = os.path.join(self.test_dir, "run")

        analysis = SimpleAnalysis(
            subject=event,
            name="transit-search",
            pipeline="photometry-bls",
            status="ready",
            ledger=self.ledger,
            rundir=rundir,
        )
        analysis.pipeline.build_dag()

        with open(os.path.join(rundir, "run_transit_search.sub")) as f:
            sub_contents = f.read()

        self.assertIn(f"executable = {rundir}/run_transit_search.sh", sub_contents)
        self.assertIn(f"initialdir = {rundir}", sub_contents)
        self.assertNotIn('executable = "', sub_contents)
        self.assertNotIn('initialdir = "', sub_contents)

    def test_build_dag_renders_defaults_when_no_detrend_or_bls_overrides_given(self):
        """
        Regression test: the config template used to index straight into
        ``production.meta['detrend']``/``['bls']`` and rely on a Liquid
        ``default:`` filter, which only substitutes a fallback for an
        *undefined value*, not for a missing parent key -- so a completely
        bare analysis (no ``detrend``/``bls`` blueprint overrides, the
        expected common case) raised at render time instead of falling
        back to the documented defaults.
        """
        from asimov.analysis import SimpleAnalysis

        event = self.ledger.get_event("KIC-11446443")[0]
        rundir = os.path.join(self.test_dir, "run")

        analysis = SimpleAnalysis(
            subject=event,
            name="transit-search",
            pipeline="photometry-bls",
            status="ready",
            ledger=self.ledger,
            rundir=rundir,
        )

        analysis.pipeline.build_dag()

        config_path = os.path.join(rundir, "photometry-bls.toml")
        with open(config_path) as f:
            rendered = f.read()

        self.assertIn("window_length = 0.5", rendered)
        self.assertIn("period_min = 0.5", rendered)
        self.assertIn("period_max = 20.0", rendered)
        self.assertIn("duration_grid = [0.05, 0.10, 0.20]", rendered)

    def test_pipeline_survives_ledger_save_and_reload(self):
        """
        Regression test: applying a ``kind: analysis`` blueprint (rather
        than constructing ``SimpleAnalysis`` directly in Python) round-trips
        the production through ``ledger.add_analysis`` -> YAML -> a fresh
        ``ledger.get_event()`` reload, exactly as separate ``asimov apply``
        and ``asimov manage build`` CLI invocations would. That reload path
        re-resolves the pipeline via ``known_pipelines[pipeline.name.lower()]``
        (see ``test_entry_points.py``'s
        ``test_pipeline_name_matches_its_own_entry_point_key``), so this
        confirms the whole blueprint-apply-then-reload-then-build workflow
        actually works end-to-end, not just direct-construction shortcuts.
        """
        from asimov.cli.application import apply_page
        from asimov.ledger import YAMLLedger

        analysis_blueprint = os.path.join(self.test_dir, "analysis.yaml")
        with open(analysis_blueprint, "w") as f:
            f.write(
                "kind: analysis\n"
                "name: transit-search\n"
                "event: KIC-11446443\n"
                "pipeline: photometry-bls\n"
            )
        apply_page(file=analysis_blueprint, event=None, ledger=self.ledger)

        reloaded_ledger = YAMLLedger(os.path.join(self.test_dir, ".asimov", "ledger.yml"))
        event = reloaded_ledger.get_event("KIC-11446443")[0]
        analysis = event.productions[0]

        self.assertIsInstance(analysis.pipeline, BLSTransitSearch)

        analysis.rundir = os.path.join(self.test_dir, "run")
        analysis.pipeline.build_dag()
        self.assertTrue(os.path.exists(os.path.join(analysis.rundir, "photometry-bls.toml")))
        self.assertTrue(os.path.exists(os.path.join(analysis.rundir, "run_transit_search.sub")))
        self.assertTrue(os.path.exists(os.path.join(analysis.rundir, "sbatch_submit.sh")))

    def test_build_dag_dryrun_creates_nothing(self):
        from asimov.analysis import SimpleAnalysis

        event = self.ledger.get_event("KIC-11446443")[0]
        rundir = os.path.join(self.test_dir, "run")

        analysis = SimpleAnalysis(
            subject=event,
            name="transit-search",
            pipeline="photometry-bls",
            status="ready",
            ledger=self.ledger,
            rundir=rundir,
        )

        analysis.pipeline.build_dag(dryrun=True)
        self.assertFalse(os.path.exists(rundir))


if __name__ == "__main__":
    unittest.main()

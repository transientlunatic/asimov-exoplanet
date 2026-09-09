"""
Unit tests for the ``asimov-exoplanet-bls`` console script (``cli.run``).

Mocks ``MASTFileSource.fetch`` and ``lightkurve.read`` so this exercises
only our own config-parsing and stage-wiring logic, not network access or
real FITS I/O.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from asimov_exoplanet import cli


class CliRunTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "photometry-bls.toml")
        with open(self.config_path, "w") as f:
            f.write(
                "[target]\n"
                "catalog_id = 11446443\n"
                'mission = "Kepler"\n'
                "\n"
                "[detrend]\n"
                "window_length = 0.3\n"
                "\n"
                "[bls]\n"
                "period_min = 1.0\n"
                "period_max = 5.0\n"
                "duration_grid = [0.05, 0.1]\n"
            )
        self.output_dir = os.path.join(self.test_dir, "run")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("asimov_exoplanet.report.build_target_report")
    @patch("asimov_exoplanet.vetting.vet")
    @patch("asimov_exoplanet.photometry.search")
    @patch("asimov_exoplanet.photometry.detrend")
    @patch("lightkurve.read")
    @patch("asimov_exoplanet.cli.MASTFileSource")
    def test_run_writes_results_json_with_config_values(
        self, mock_filesource_cls, mock_lk_read, mock_detrend, mock_search, mock_vet, mock_report
    ):
        mock_client = mock_filesource_cls.return_value
        mock_client.fetch.return_value = b"fake fits bytes"
        mock_lk_read.return_value = object()
        mock_detrend.return_value = object()
        mock_search.return_value = {
            "period": 3.2,
            "epoch": 0.7,
            "duration": 0.12,
            "depth": 0.01,
            "sde": 15.0,
        }
        mock_vet.return_value = {"flags": ["some vetting flag"], "odd_even": {}, "secondary_eclipse": {}}

        results_path = cli.run(self.config_path, self.output_dir)

        mock_client.fetch.assert_called_once_with(11446443, mission="Kepler")
        mock_detrend.assert_called_once_with(mock_lk_read.return_value, window_length=0.3)
        mock_search.assert_called_once_with(
            mock_detrend.return_value, period_min=1.0, period_max=5.0, duration_grid=[0.05, 0.1]
        )
        mock_vet.assert_called_once_with(mock_detrend.return_value, mock_search.return_value)
        mock_report.assert_called_once_with(
            mock_detrend.return_value,
            mock_search.return_value,
            mock_vet.return_value,
            os.path.join(self.output_dir, "folded_lightcurve.html"),
            target_info={"catalog_id": 11446443, "mission": "Kepler"},
        )

        self.assertEqual(results_path, os.path.join(self.output_dir, "results.json"))
        with open(results_path) as f:
            results = json.load(f)

        self.assertEqual(results["period"], 3.2)
        self.assertEqual(results["vetting_flags"], ["some vetting flag"])

        cached_fits = os.path.join(self.output_dir, "light_curve.fits")
        self.assertTrue(os.path.exists(cached_fits))
        with open(cached_fits, "rb") as f:
            self.assertEqual(f.read(), b"fake fits bytes")

    @patch("asimov_exoplanet.report.build_target_report")
    @patch("asimov_exoplanet.vetting.vet")
    @patch("asimov_exoplanet.photometry.search")
    @patch("asimov_exoplanet.photometry.detrend")
    @patch("lightkurve.read")
    @patch("asimov_exoplanet.cli.MASTFileSource")
    def test_run_uses_config_template_defaults_when_omitted(
        self, mock_filesource_cls, mock_lk_read, mock_detrend, mock_search, mock_vet, mock_report
    ):
        minimal_config_path = os.path.join(self.test_dir, "minimal.toml")
        with open(minimal_config_path, "w") as f:
            f.write("[target]\ncatalog_id = 11446443\n")

        mock_client = mock_filesource_cls.return_value
        mock_client.fetch.return_value = b"fake fits bytes"
        mock_search.return_value = {"period": 1.0, "epoch": 0.0, "duration": 0.1}
        mock_vet.return_value = {"flags": [], "odd_even": {}, "secondary_eclipse": {}}

        cli.run(minimal_config_path, self.output_dir)

        mock_client.fetch.assert_called_once_with(11446443, mission="Kepler")
        mock_detrend.assert_called_once_with(mock_lk_read.return_value, window_length=0.5)
        mock_search.assert_called_once_with(
            mock_detrend.return_value, period_min=0.5, period_max=20.0, duration_grid=[0.05, 0.10, 0.20]
        )
        mock_vet.assert_called_once_with(mock_detrend.return_value, mock_search.return_value)


if __name__ == "__main__":
    unittest.main()

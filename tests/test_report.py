"""
Unit tests for the per-target HTML report. Checks the report is
well-formed and carries the right data, not its visual appearance.
"""

import json
import os
import re
import shutil
import tempfile
import unittest

import numpy as np

try:
    import lightkurve as lk

    from asimov_exoplanet import report

    REPORT_AVAILABLE = True
except ImportError:
    REPORT_AVAILABLE = False


@unittest.skipUnless(REPORT_AVAILABLE, "astropy/lightkurve not installed")
class BuildTargetReportTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        rng = np.random.default_rng(0)
        n = 500
        time = np.arange(n) * 0.0208
        flux = np.ones(n) + rng.normal(0, 0.0003, n)
        self.light_curve = lk.LightCurve(time=time, flux=flux)
        self.search_result = {
            "period": 3.2,
            "epoch": 0.7,
            "duration": 0.12,
            "depth": 0.01,
            "sde": 15.0,
        }
        self.vetting_result = {
            "flags": [],
            "odd_even": {"odd_depth": 0.01, "even_depth": 0.0099, "significance": 0.5, "consistent": True},
            "secondary_eclipse": {"secondary_depth": 0.0, "significance": 0.1, "detected": False},
        }
        self.output_path = os.path.join(self.test_dir, "folded_lightcurve.html")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _embedded_data(self, html):
        match = re.search(r"const data = (\{.*?\});", html, re.DOTALL)
        self.assertIsNotNone(match, "could not find embedded data JSON in report")
        return json.loads(match.group(1))

    def test_writes_html_file_with_embedded_data(self):
        returned_path = report.build_target_report(
            self.light_curve,
            self.search_result,
            self.vetting_result,
            self.output_path,
            target_info={"catalog_id": 11446443, "mission": "Kepler"},
        )

        self.assertEqual(returned_path, self.output_path)
        self.assertTrue(os.path.exists(self.output_path))

        with open(self.output_path) as f:
            html = f.read()

        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("d3", html.lower())

        data = self._embedded_data(html)
        self.assertEqual(data["target"]["catalog_id"], 11446443)
        self.assertEqual(data["result"]["period"], 3.2)
        self.assertEqual(len(data["points"]), len(self.light_curve))

    def test_includes_vetting_flags_in_embedded_data(self):
        flagged_vetting_result = dict(self.vetting_result, flags=["odd/even transit depth mismatch"])

        report.build_target_report(
            self.light_curve, self.search_result, flagged_vetting_result, self.output_path
        )

        with open(self.output_path) as f:
            data = self._embedded_data(f.read())

        self.assertEqual(data["vetting"]["flags"], ["odd/even transit depth mismatch"])

    def test_subsamples_large_light_curves(self):
        rng = np.random.default_rng(1)
        n = report.MAX_EMBEDDED_POINTS * 3
        time = np.arange(n) * 0.001
        flux = np.ones(n) + rng.normal(0, 0.0003, n)
        big_light_curve = lk.LightCurve(time=time, flux=flux)

        report.build_target_report(big_light_curve, self.search_result, self.vetting_result, self.output_path)

        with open(self.output_path) as f:
            data = self._embedded_data(f.read())

        self.assertEqual(len(data["points"]), report.MAX_EMBEDDED_POINTS)

    def test_handles_missing_sde_gracefully(self):
        result_without_sde = dict(self.search_result)
        del result_without_sde["sde"]

        # Should not raise even though .sde is absent.
        report.build_target_report(self.light_curve, result_without_sde, self.vetting_result, self.output_path)

        with open(self.output_path) as f:
            data = self._embedded_data(f.read())
        self.assertIsNone(data["result"]["sde"])

    def test_escapes_script_tag_breakout_in_embedded_data(self):
        """
        Regression test: target_info comes from blueprint metadata (an
        untrusted source, in principle), and was originally embedded via a
        plain ``json.dumps()`` inside a ``<script>`` tag. A mission/
        catalog_id string containing ``</script>`` could break out of the
        script tag and inject arbitrary HTML/JS into the report when opened
        in a browser.
        """
        malicious_target_info = {"catalog_id": "</script><script>alert(1)</script>", "mission": "Kepler"}

        report.build_target_report(
            self.light_curve,
            self.search_result,
            self.vetting_result,
            self.output_path,
            target_info=malicious_target_info,
        )

        with open(self.output_path) as f:
            html = f.read()

        self.assertNotIn("</script><script>alert", html)
        data = self._embedded_data(html)
        self.assertEqual(data["target"]["catalog_id"], malicious_target_info["catalog_id"])


@unittest.skipUnless(REPORT_AVAILABLE, "astropy/lightkurve not installed")
class BuildCatalogReportTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_path = os.path.join(self.test_dir, "catalog_report.html")
        self.results_by_target = {
            "KIC-2": {
                "period": 2.5,
                "epoch": 0.1,
                "duration": 0.1,
                "depth": 0.002,
                "sde": 12.0,
                "vetting_flags": ["odd/even transit depth mismatch (5.0 sigma)"],
            },
            "KIC-1": {
                "period": 1.0,
                "epoch": 0.0,
                "duration": 0.05,
                "depth": 0.001,
                "sde": 20.0,
                "vetting_flags": [],
            },
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _embedded_data(self, html):
        match = re.search(r"const data = (\{.*?\});", html, re.DOTALL)
        self.assertIsNotNone(match, "could not find embedded data JSON in catalog report")
        return json.loads(match.group(1))

    def test_writes_html_file_with_embedded_candidates(self):
        returned_path = report.build_catalog_report(self.results_by_target, self.output_path)

        self.assertEqual(returned_path, self.output_path)
        self.assertTrue(os.path.exists(self.output_path))

        with open(self.output_path) as f:
            html = f.read()

        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("d3", html.lower())

        data = self._embedded_data(html)
        self.assertEqual(data["summary"]["total"], 2)
        self.assertEqual(data["summary"]["flagged"], 1)

        names = [c["name"] for c in data["candidates"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(set(names), {"KIC-1", "KIC-2"})

        by_name = {c["name"]: c for c in data["candidates"]}
        self.assertEqual(by_name["KIC-2"]["flags"], ["odd/even transit depth mismatch (5.0 sigma)"])
        self.assertEqual(by_name["KIC-1"]["flags"], [])
        self.assertEqual(by_name["KIC-1"]["period"], 1.0)
        self.assertEqual(by_name["KIC-1"]["sde"], 20.0)

    def test_handles_empty_catalog(self):
        report.build_catalog_report({}, self.output_path)

        with open(self.output_path) as f:
            data = self._embedded_data(f.read())

        self.assertEqual(data["candidates"], [])
        self.assertEqual(data["summary"], {"total": 0, "flagged": 0})

    def test_escapes_script_tag_breakout_in_target_name(self):
        """
        Same script-tag-breakout risk as ``build_target_report``: subject
        names come from blueprint metadata, so a name (used as a dict key,
        embedded verbatim in the JSON) containing ``</script>`` must not be
        able to break out of the report's inline ``<script>`` tag.
        """
        malicious_results = {"</script><script>alert(1)</script>": {"period": 1.0, "vetting_flags": []}}

        report.build_catalog_report(malicious_results, self.output_path)

        with open(self.output_path) as f:
            html = f.read()

        self.assertNotIn("</script><script>alert", html)
        data = self._embedded_data(html)
        self.assertEqual(data["candidates"][0]["name"], "</script><script>alert(1)</script>")

    def test_table_and_tooltip_rendering_does_not_interpolate_untrusted_data_into_html(self):
        """
        Regression test (Copilot review finding on PR #4): the candidate
        table's ``renderTable()`` and the overview plot's tooltip handler
        originally built markup with D3's ``.html()``, interpolating a
        template literal containing the subject name (and vetting-flag
        text) directly -- both from
        blueprint metadata, an untrusted source -- directly into HTML. A name
        like ``<img src=x onerror=...>`` would then execute as markup when
        the report is opened, exactly the class of bug already fixed for the
        per-target report's target_info (see
        ``test_escapes_script_tag_breakout_in_embedded_data`` above and
        ``BuildTargetReportTests`` in this file). Verified against a real
        headless-browser render (not just this static source check) while
        fixing this: the injected name/flags rendered as escaped text with
        no script execution.

        This can't run the report's JS from a plain unit test, so it checks
        the template source builds cells with ``.text()``/``.attr()``
        instead of interpolating candidate data into ``.html()`` strings.
        """
        self.assertNotIn("rows.html(d =>", report._CATALOG_TEMPLATE)
        self.assertNotIn("${d.name}", report._CATALOG_TEMPLATE)
        self.assertIn(".text(d.name)", report._CATALOG_TEMPLATE)

    def test_overview_plot_handles_empty_or_all_missing_period_catalog(self):
        """
        Regression test (Copilot review finding on PR #4): the overview
        plot's log x-scale used ``d3.scaleLog().domain(d3.extent(...))``
        directly. ``d3.extent()`` returns ``[undefined, undefined]`` on an
        empty array (e.g. no candidates, or none with a recovered period),
        and ``scaleLog().domain([undefined, undefined])`` throws at render
        time. Can't execute the report's JS from a plain unit test, so this
        checks the template source guards the domain -- verified against a
        real headless-browser render (an empty catalog report loaded with no
        JS errors) while fixing this.
        """
        self.assertIn("periodExtent[0] === undefined", report._CATALOG_TEMPLATE)


if __name__ == "__main__":
    unittest.main()

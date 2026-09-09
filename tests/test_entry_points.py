"""
Verify that the asimov-exoplanet plugin registers correctly with asimov's
entry-point discovery mechanisms.

This is the Phase 0 acceptance test: it doesn't exercise any real
photometry code, it just confirms the plumbing (pyproject.toml entry
points, package importability) is wired up correctly end-to-end, the same
way asimov core discovers any other pipeline plugin.
"""

try:
    from importlib.metadata import entry_points
except ImportError:  # pragma: no cover
    from importlib_metadata import entry_points

import unittest


class EntryPointDiscoveryTests(unittest.TestCase):
    def test_pipelines_are_discoverable(self):
        discovered = {ep.name: ep for ep in entry_points(group="asimov.pipelines")}

        self.assertIn("photometry-bls", discovered)
        self.assertIn("photometry-bls-dummy", discovered)

        bls_cls = discovered["photometry-bls"].load()
        dummy_cls = discovered["photometry-bls-dummy"].load()

        from asimov.pipeline import Pipeline
        from asimov_exoplanet.pipeline import BLSTransitSearch, DummyTransitSearchPipeline

        self.assertIs(bls_cls, BLSTransitSearch)
        self.assertIs(dummy_cls, DummyTransitSearchPipeline)
        self.assertTrue(issubclass(bls_cls, Pipeline))
        self.assertTrue(issubclass(dummy_cls, Pipeline))

    def test_pipeline_name_matches_its_own_entry_point_key(self):
        """
        asimov.analysis.Analysis.to_dict() serializes a production's pipeline
        as ``self.pipeline.name.lower()`` (not the entry-point key it was
        originally constructed with), and reconstructs it on reload via
        ``known_pipelines[pipeline.lower()]``. So a pipeline's ``name`` class
        attribute *must* equal its registered ``asimov.pipelines`` entry-point
        key (case-insensitively), or saving and reloading a ledger silently
        breaks pipeline lookup for any analysis using it.
        """
        discovered = {ep.name: ep for ep in entry_points(group="asimov.pipelines")}

        for entry_point_name, entry_point in discovered.items():
            if not entry_point_name.startswith("photometry-bls"):
                continue
            pipeline_cls = entry_point.load()
            self.assertEqual(
                pipeline_cls.name.lower(),
                entry_point_name,
                f"{pipeline_cls.__name__}.name must equal its entry-point key "
                f"{entry_point_name!r} for ledger save/reload to find it again.",
            )

    def test_filesource_hook_is_discoverable(self):
        discovered = {ep.name: ep for ep in entry_points(group="asimov.hooks.filesource")}

        self.assertIn("mast", discovered)

        mast_cls = discovered["mast"].load()

        from asimov_exoplanet.filesource import MASTFileSource

        self.assertIs(mast_cls, MASTFileSource)


if __name__ == "__main__":
    unittest.main()

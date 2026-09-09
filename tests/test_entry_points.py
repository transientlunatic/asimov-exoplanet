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

    def test_filesource_hook_is_discoverable(self):
        discovered = {ep.name: ep for ep in entry_points(group="asimov.hooks.filesource")}

        self.assertIn("mast", discovered)

        mast_cls = discovered["mast"].load()

        from asimov_exoplanet.filesource import MASTFileSource

        self.assertIs(mast_cls, MASTFileSource)


if __name__ == "__main__":
    unittest.main()

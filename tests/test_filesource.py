"""
Unit tests for ``MASTFileSource``. These mock ``lightkurve.search_lightcurve``
directly, so no network access is needed and no real MAST/Kepler FITS
headers are required -- they test our own orchestration logic (search,
download, cache-and-return-bytes), not lightkurve/astroquery internals.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from asimov_exoplanet.filesource import MASTFileSource


class MASTFileSourceTests(unittest.TestCase):
    def setUp(self):
        self.client = MASTFileSource(config=None)

    def test_rejects_unsupported_product(self):
        with self.assertRaises(NotImplementedError):
            self.client.fetch(11446443, product="target pixel file")

    @patch("lightkurve.search_lightcurve")
    def test_raises_when_no_results_found(self, mock_search):
        mock_search.return_value = []

        with self.assertRaises(FileNotFoundError):
            self.client.fetch(11446443, mission="Kepler")

        mock_search.assert_called_once_with("Kepler 11446443", mission="Kepler", author=None)

    @patch("lightkurve.search_lightcurve")
    def test_fetch_returns_bytes_of_downloaded_file(self, mock_search):
        with tempfile.TemporaryDirectory() as tmp:
            cached_path = os.path.join(tmp, "cached_light_curve.fits")
            payload = b"not a real fits file, just test bytes"
            with open(cached_path, "wb") as f:
                f.write(payload)

            downloaded = MagicMock()
            downloaded.meta = {"FILENAME": cached_path}

            search_hit = MagicMock()
            search_hit.download.return_value = downloaded
            mock_search.return_value = [search_hit]

            data = self.client.fetch(11446443, mission="Kepler")

            self.assertEqual(data, payload)
            search_hit.download.assert_called_once()

    @patch("lightkurve.search_lightcurve")
    def test_raises_when_downloaded_file_missing(self, mock_search):
        downloaded = MagicMock()
        downloaded.meta = {"FILENAME": "/nonexistent/path/light_curve.fits"}

        search_hit = MagicMock()
        search_hit.download.return_value = downloaded
        mock_search.return_value = [search_hit]

        with self.assertRaises(RuntimeError):
            self.client.fetch(11446443, mission="Kepler")


if __name__ == "__main__":
    unittest.main()

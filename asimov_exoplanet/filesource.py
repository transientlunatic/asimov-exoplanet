"""
``asimov.hooks.filesource`` implementation for fetching stellar photometry
from MAST (Kepler/K2/TESS).

This mirrors the pattern used by the ``asimov-gracedb`` plugin for fetching
GWOSC frames: a class which takes the global Asimov ``config`` object in its
constructor, and exposes a single ``fetch(target_id, product)`` method
returning the raw bytes of the requested data product.
"""

import os
import tempfile


class MASTFileSource:
    """
    Fetch light curve data products from MAST for a given catalog target.

    Parameters
    ----------
    config : configparser.ConfigParser
        The global asimov configuration object, as passed to every
        ``asimov.hooks.filesource`` plugin. Not currently used (MAST
        queries need no credentials), but accepted for interface
        compatibility with other filesource hooks.

    Notes
    -----
    Uses ``lightkurve.search_lightcurve`` to find and download the light
    curve, then returns the raw bytes of the FITS file lightkurve cached
    to disk -- callers are expected to persist those bytes themselves
    (e.g. under the analysis run directory).
    """

    name = "mast"

    def __init__(self, config):
        self.config = config

    def fetch(self, target_id, product="light curve", mission="Kepler", author=None):
        """
        Fetch a data product for a target from MAST.

        Parameters
        ----------
        target_id : str or int
            The catalog identifier for the target (e.g. a KIC, EPIC, or
            TIC number).
        product : str, optional
            The data product to fetch. Currently only "light curve" is
            supported.
        mission : str, optional
            The mission to search (e.g. "Kepler", "K2", "TESS").
        author : str, optional
            Restrict the search to light curves produced by a specific
            pipeline (e.g. "Kepler", "SPOC"). Left unset to accept
            whichever the mission's default pipeline produced.

        Returns
        -------
        bytes
            The raw contents of the fetched FITS file.
        """
        if product != "light curve":
            raise NotImplementedError(
                f"Unsupported product {product!r}; only 'light curve' is currently supported."
            )

        import lightkurve as lk

        search_result = lk.search_lightcurve(f"{mission} {target_id}", mission=mission, author=author)
        if len(search_result) == 0:
            raise FileNotFoundError(
                f"No {mission} light curve found on MAST for target {target_id!r}."
            )

        with tempfile.TemporaryDirectory() as download_dir:
            light_curve = search_result[0].download(download_dir=download_dir)
            fits_path = light_curve.meta.get("FILENAME")
            if not fits_path or not os.path.exists(fits_path):
                raise RuntimeError(
                    f"lightkurve did not report a cached FITS file path for target {target_id!r}."
                )
            with open(fits_path, "rb") as f:
                return f.read()

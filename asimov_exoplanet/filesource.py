"""
``asimov.hooks.filesource`` implementation for fetching stellar photometry
from MAST (Kepler/K2/TESS).

This mirrors the pattern used by the ``asimov-gracedb`` plugin for fetching
GWOSC frames: a class which takes the global Asimov ``config`` object in its
constructor, and exposes a single ``fetch(target_id, product)`` method
returning the raw bytes of the requested data product.
"""


class MASTFileSource:
    """
    Fetch light curve data products from MAST for a given catalog target.

    Parameters
    ----------
    config : configparser.ConfigParser
        The global asimov configuration object, as passed to every
        ``asimov.hooks.filesource`` plugin.

    Notes
    -----
    This is a Phase 0/1 scaffold: ``fetch`` is not yet implemented. The
    intended implementation uses ``lightkurve``/``astroquery.mast`` to
    download the light curve FITS file for the given catalog ID (KIC/EPIC/
    TIC) and mission, and returns its raw bytes so the calling pipeline can
    cache it under the analysis run directory.
    """

    name = "mast"

    def __init__(self, config):
        self.config = config

    def fetch(self, target_id, product="light curve"):
        """
        Fetch a data product for a target from MAST.

        Parameters
        ----------
        target_id : str
            The catalog identifier for the target (e.g. a KIC, EPIC, or TIC
            number).
        product : str, optional
            The data product to fetch. Currently only "light curve" is
            envisaged.

        Returns
        -------
        bytes
            The raw contents of the fetched FITS file.
        """
        raise NotImplementedError(
            "Phase 1: MAST ingestion via lightkurve/astroquery.mast is not "
            "yet implemented."
        )

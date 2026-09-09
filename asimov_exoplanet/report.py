"""
Reporting for the exoplanet transit-search pipeline.

Phase 2 will add per-target reports (a folded-light-curve plot alongside
the ``results.json`` produced by ``BLSTransitSearch``) and, for Phase 3,
per-catalog aggregate reports (a candidate table and completeness plots for
injection-recovery studies). Not yet implemented.
"""


def build_target_report(results, output_path):
    """
    Build a per-target report (folded light curve plot + summary) from a
    transit-search ``results.json``-style dictionary.

    Parameters
    ----------
    results : dict
        The transit-search result (period, epoch, duration, depth, SDE,
        vetting flags).
    output_path : str
        Where to write the report.
    """
    raise NotImplementedError("Phase 2: per-target reporting is not yet implemented.")


def build_catalog_report(results_by_target, output_path):
    """
    Build an aggregate report across a catalog-scale campaign.

    Parameters
    ----------
    results_by_target : dict
        Mapping of subject name to its transit-search result dictionary.
    output_path : str
        Where to write the report.
    """
    raise NotImplementedError("Phase 3: catalog-scale reporting is not yet implemented.")

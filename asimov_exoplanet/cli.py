"""
Command-line entry points that actually execute BLS transit-search work.

``BLSTransitSearch.build_dag`` writes a job script which invokes ``run()``
below (as the ``asimov-exoplanet-bls`` console script) with the path to a
rendered TOML config file (see ``config_template.toml``) and an output
directory, for a single target. For a catalog-scale campaign
(``ProjectAnalysis``), it also writes one final aggregation job invoking
``aggregate()`` (as the ``asimov-exoplanet-bls-catalog-report`` console
script) once every target's job has finished, to build the campaign-wide
candidate report. Keeping the real work here, as plain functions, means the
astronomy code in ``photometry.py``/``filesource.py``/``vetting.py`` stays
testable without needing a scheduler, a subprocess, or an Asimov
``production`` object at all.
"""

import argparse
import glob
import json
import os
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from . import photometry, report, vetting
from .filesource import MASTFileSource


def run(config_path, output_dir):
    """
    Run ingest -> detrend -> BLS search -> vet -> report for one target,
    writing ``results.json`` and ``folded_lightcurve.html`` into
    ``output_dir``.

    Parameters
    ----------
    config_path : str
        Path to a TOML config file rendered from ``config_template.toml``.
    output_dir : str
        Directory to cache the downloaded light curve and write
        ``results.json`` into.

    Returns
    -------
    str
        The path to the written ``results.json``.
    """
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    target = config["target"]
    detrend_config = config.get("detrend", {})
    bls_config = config.get("bls", {})

    os.makedirs(output_dir, exist_ok=True)

    try:
        from asimov import config as asimov_config
    except ImportError:
        asimov_config = None

    client = MASTFileSource(asimov_config)
    fits_bytes = client.fetch(target["catalog_id"], mission=target.get("mission", "Kepler"))

    fits_path = os.path.join(output_dir, "light_curve.fits")
    with open(fits_path, "wb") as f:
        f.write(fits_bytes)

    import lightkurve as lk

    light_curve = lk.read(fits_path)
    flattened = photometry.detrend(light_curve, window_length=detrend_config.get("window_length", 0.5))
    result = photometry.search(
        flattened,
        period_min=bls_config.get("period_min", 0.5),
        period_max=bls_config.get("period_max", 20.0),
        duration_grid=bls_config.get("duration_grid", [0.05, 0.10, 0.20]),
    )
    vetting_result = vetting.vet(flattened, result)
    result["vetting_flags"] = vetting_result["flags"]

    results_path = os.path.join(output_dir, "results.json")
    with open(results_path, "w") as f:
        json.dump(result, f, indent=2)

    report.build_target_report(
        flattened,
        result,
        vetting_result,
        os.path.join(output_dir, "folded_lightcurve.html"),
        target_info=target,
    )

    return results_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a BLS transit-search job for one target.")
    parser.add_argument("config", help="Path to the rendered pipeline TOML config.")
    parser.add_argument("output_dir", help="Directory to cache data and write results.json into.")
    args = parser.parse_args(argv)
    run(args.config, args.output_dir)


def aggregate(rundir):
    """
    Aggregate a catalog campaign's per-target results into a candidate
    report.

    Scans ``<rundir>/<subject name>/results.json`` for every subject
    subdirectory (one per target, each written by a prior ``run()`` call)
    and builds the campaign-wide candidate report.

    Parameters
    ----------
    rundir : str
        A catalog campaign's run directory (a ``ProjectAnalysis``'s
        ``production.rundir``), containing one subdirectory per subject.

    Returns
    -------
    str
        The path to the written ``catalog_report.html``.
    """
    results_by_target = {}
    for results_path in sorted(glob.glob(os.path.join(rundir, "*", "results.json"))):
        subject_name = os.path.basename(os.path.dirname(results_path))
        with open(results_path) as f:
            results_by_target[subject_name] = json.load(f)

    with open(os.path.join(rundir, "candidates.json"), "w") as f:
        json.dump(results_by_target, f, indent=2)

    report_path = os.path.join(rundir, "catalog_report.html")
    report.build_catalog_report(results_by_target, report_path)

    return report_path


def main_catalog_report(argv=None):
    parser = argparse.ArgumentParser(
        description="Aggregate a catalog campaign's per-target results into a candidate report."
    )
    parser.add_argument(
        "rundir", help="The catalog campaign's run directory (contains one subdirectory per target)."
    )
    args = parser.parse_args(argv)
    aggregate(args.rundir)


if __name__ == "__main__":
    main()

"""
Command-line entry point that actually executes a BLS transit-search job.

``BLSTransitSearch.build_dag`` writes a job script which invokes this
module (as the ``asimov-exoplanet-bls`` console script) with the path to a
rendered TOML config file (see ``config_template.toml``) and an output
directory. Keeping the real work here, as a plain function taking a config
dict, means the astronomy code in ``photometry.py`` and ``filesource.py``
stays testable without needing a scheduler, a subprocess, or an Asimov
``production`` object at all.
"""

import argparse
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


if __name__ == "__main__":
    main()

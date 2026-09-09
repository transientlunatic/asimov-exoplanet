"""
Cheap, deterministic vetting checks for a candidate transit signal.

These are the checks a human would run first, before spending any real
follow-up effort on a BLS candidate: is the transit depth consistent
between odd- and even-numbered cycles (an alternating depth is the classic
signature of a diluted eclipsing binary at twice the true period), and is
there a secondary eclipse at phase 0.5 (a stellar companion, not a planet)?

Both operate on the same phase-folded light curve BLS already searched, so
no extra data is needed. What Phase 2 does *not* do (see ``DESIGN.md``):

- Per-quarter/sector consistency -- this needs multiple quarters/sectors of
  data, and ``BLSTransitSearch.ingest()`` currently fetches only the first
  ``lightkurve.search_lightcurve`` result. Deferred rather than silently
  expanding ingest's scope.
- Centroid/pixel-level vetting -- needs target pixel files, not just the
  light curve. Out of scope for this MVP; see ``DESIGN.md``.

As with ``photometry.py``, these are kept as plain functions operating on a
``lightkurve.LightCurve`` so they're unit-testable against synthetic data
with no Asimov machinery involved.
"""

import numpy as np

#: Significance threshold (in standard deviations) above which a check is
#: flagged. 3 sigma is the conventional "this probably isn't noise"
#: threshold for cheap deterministic checks like these -- not a claim of
#: any particular false-alarm rate.
SIGNIFICANCE_THRESHOLD = 3.0


def _phase_fold(times, period, epoch):
    """Fold times onto [-0.5, 0.5) x period, centred on the epoch."""
    return ((times - epoch + 0.5 * period) % period) - 0.5 * period


def _cycle_number(times, period, epoch):
    """Which transit cycle (0, 1, 2, ...) each time falls nearest to."""
    return np.round((times - epoch) / period).astype(int)


def _depth_and_uncertainty(flux):
    """Mean transit depth (1 - mean flux) and the standard error on it."""
    if len(flux) == 0:
        return None, None
    depth = 1.0 - np.mean(flux)
    sem = np.std(flux, ddof=1) / np.sqrt(len(flux)) if len(flux) > 1 else np.inf
    return depth, sem


def check_odd_even(light_curve, period, epoch, duration):
    """
    Compare the transit depth measured on odd- vs. even-numbered cycles.

    A significant difference is the classic signature of a diluted
    eclipsing binary at twice the true orbital period (alternating
    primary/secondary eclipses of slightly different depth, masquerading
    as a single periodic transit).

    Parameters
    ----------
    light_curve : lightkurve.LightCurve
        The (typically detrended) light curve BLS was run on.
    period, epoch, duration : float
        The BLS-recovered period, transit epoch, and duration, in the same
        time units as ``light_curve.time``.

    Returns
    -------
    dict
        ``odd_depth``, ``even_depth``, ``significance`` (the depth
        difference in standard deviations, ``None`` if it can't be
        computed), and ``consistent`` (bool, ``True`` if not flagged).
    """
    times = light_curve.time.value
    flux = light_curve.flux.value if hasattr(light_curve.flux, "value") else np.asarray(light_curve.flux)

    phase = _phase_fold(times, period, epoch)
    in_transit = np.abs(phase) < (duration / 2)
    cycle = _cycle_number(times, period, epoch)

    is_odd = (cycle % 2) != 0
    odd_depth, odd_sem = _depth_and_uncertainty(flux[in_transit & is_odd])
    even_depth, even_sem = _depth_and_uncertainty(flux[in_transit & ~is_odd])

    if odd_depth is None or even_depth is None:
        return {
            "odd_depth": odd_depth,
            "even_depth": even_depth,
            "significance": None,
            "consistent": True,
        }

    combined_sem = np.sqrt(odd_sem**2 + even_sem**2)
    significance = abs(odd_depth - even_depth) / combined_sem if combined_sem > 0 else 0.0

    return {
        "odd_depth": float(odd_depth),
        "even_depth": float(even_depth),
        "significance": float(significance),
        "consistent": bool(significance < SIGNIFICANCE_THRESHOLD),
    }


def check_secondary_eclipse(light_curve, period, epoch, duration):
    """
    Search for a secondary eclipse at phase 0.5.

    A significant dip half a period away from the primary transit is the
    signature of a stellar or brown-dwarf companion (an eclipsing binary),
    not a planet -- planets are far too faint to produce a detectable
    secondary eclipse with this kind of simple box-depth measurement.

    Parameters
    ----------
    light_curve : lightkurve.LightCurve
    period, epoch, duration : float
        As for :func:`check_odd_even`.

    Returns
    -------
    dict
        ``secondary_depth``, ``significance``, ``detected`` (bool, ``True``
        if a significant secondary eclipse was found), and
        ``baseline_scatter`` (the out-of-transit flux scatter used as the
        significance threshold's reference level; ``None`` if it couldn't
        be computed).
    """
    times = light_curve.time.value
    flux = light_curve.flux.value if hasattr(light_curve.flux, "value") else np.asarray(light_curve.flux)

    phase = _phase_fold(times, period, epoch)
    secondary_phase = _phase_fold(times, period, epoch - 0.5 * period)

    in_secondary = np.abs(secondary_phase) < (duration / 2)
    out_of_transit = (np.abs(phase) > duration) & (np.abs(secondary_phase) > duration)

    secondary_depth, secondary_sem = _depth_and_uncertainty(flux[in_secondary])
    baseline_scatter = np.std(flux[out_of_transit], ddof=1) if np.sum(out_of_transit) > 1 else np.inf

    if secondary_depth is None:
        return {"secondary_depth": None, "significance": None, "detected": False}

    significance = secondary_depth / secondary_sem if secondary_sem and secondary_sem > 0 else 0.0

    return {
        "secondary_depth": float(secondary_depth),
        "significance": float(significance),
        "detected": bool(significance > SIGNIFICANCE_THRESHOLD and secondary_depth > 0),
        "baseline_scatter": float(baseline_scatter) if np.isfinite(baseline_scatter) else None,
    }


def vet(light_curve, search_result):
    """
    Run all Phase 2 vetting checks on a BLS candidate.

    Parameters
    ----------
    light_curve : lightkurve.LightCurve
    search_result : dict
        A result dict as returned by :func:`asimov_exoplanet.photometry.search`
        (must have ``period``, ``epoch``, ``duration`` keys).

    Returns
    -------
    dict
        ``odd_even`` and ``secondary_eclipse`` (the dicts returned by the
        individual checks above), plus a flat ``flags`` list of
        human-readable warning strings for anything that failed.
    """
    period = search_result["period"]
    epoch = search_result["epoch"]
    duration = search_result["duration"]

    odd_even = check_odd_even(light_curve, period, epoch, duration)
    secondary = check_secondary_eclipse(light_curve, period, epoch, duration)

    flags = []
    if not odd_even["consistent"]:
        flags.append(
            f"odd/even transit depth mismatch ({odd_even['significance']:.1f} sigma) "
            "-- possible diluted eclipsing binary at twice this period"
        )
    if secondary["detected"]:
        flags.append(
            f"secondary eclipse detected at phase 0.5 ({secondary['significance']:.1f} sigma) "
            "-- likely an eclipsing binary, not a planet"
        )

    return {
        "odd_even": odd_even,
        "secondary_eclipse": secondary,
        "flags": flags,
    }

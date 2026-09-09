"""
Core photometry routines: detrending and Box Least Squares transit search.

These are kept as plain functions operating on ``lightkurve.LightCurve``
objects (rather than methods on ``BLSTransitSearch``) so they can be unit
tested directly against synthetic light curves, with no ``Pipeline`` or
Asimov ``production`` object involved at all.
"""

import numpy as np
from astropy.timeseries import BoxLeastSquares


def _window_length_in_cadences(light_curve, window_length_days):
    """Convert a window length in days to an odd number of cadences."""
    time_values = light_curve.time.value
    if len(time_values) < 2:
        raise ValueError("Light curve has fewer than two points; cannot estimate cadence.")

    cadence_days = np.median(np.diff(time_values))
    if not np.isfinite(cadence_days) or cadence_days <= 0:
        raise ValueError("Could not determine a positive cadence from the light curve's time values.")

    n_cadences = int(round(window_length_days / cadence_days))
    if n_cadences % 2 == 0:
        n_cadences += 1
    return max(n_cadences, 3)


def detrend(light_curve, window_length=0.5):
    """
    Remove long-term stellar variability and instrumental trends.

    Uses ``lightkurve``'s ``flatten()``, which divides out a
    Savitzky-Golay filter fit to the light curve.

    Parameters
    ----------
    light_curve : lightkurve.LightCurve
        The raw light curve to detrend.
    window_length : float, optional
        Approximate window length of the filter, in days. Converted
        internally to the nearest odd number of cadences, since
        ``flatten()`` expects a cadence count rather than a duration.

    Returns
    -------
    lightkurve.LightCurve
        The flattened (detrended) light curve, with NaNs removed.
    """
    clean = light_curve.remove_nans()
    n_cadences = _window_length_in_cadences(clean, window_length)
    return clean.flatten(window_length=n_cadences)


def _sanitize_flux_err(light_curve):
    flux_err = light_curve.flux_err
    if flux_err is None:
        return None
    values = flux_err.value if hasattr(flux_err, "value") else np.asarray(flux_err)
    if not np.any(np.isfinite(values)) or np.all(values == 0):
        return None
    return values


def search(light_curve, period_min=0.5, period_max=20.0, duration_grid=(0.05, 0.10, 0.20), n_periods=10000):
    """
    Run a Box Least Squares transit search over a period grid.

    Parameters
    ----------
    light_curve : lightkurve.LightCurve
        The (typically detrended) light curve to search.
    period_min, period_max : float
        The period range to search, in days.
    duration_grid : sequence of float, optional
        Candidate transit durations to try at each period, in days. At
        each period the duration giving the highest BLS power is kept.
    n_periods : int, optional
        Number of periods to sample linearly between ``period_min`` and
        ``period_max``.

    Returns
    -------
    dict
        ``period``, ``epoch``, ``duration``, ``depth``, and ``sde`` (a
        signal-detection-efficiency-like statistic: the best power
        expressed as a number of standard deviations above the mean
        power across the period grid).
    """
    clean = light_curve.remove_nans()
    times = clean.time.value
    fluxes = clean.flux.value if hasattr(clean.flux, "value") else np.asarray(clean.flux)
    flux_err = _sanitize_flux_err(clean)

    bls = BoxLeastSquares(times, fluxes, flux_err)
    periods = np.linspace(period_min, period_max, n_periods)
    durations = np.asarray(duration_grid, dtype=float)
    periodogram = bls.power(periods, durations)

    best_index = np.argmax(periodogram.power)
    power = periodogram.power
    power_std = np.std(power)
    sde = float((power[best_index] - np.mean(power)) / power_std) if power_std > 0 else float("nan")

    return {
        "period": float(periodogram.period[best_index]),
        "epoch": float(periodogram.transit_time[best_index]),
        "duration": float(periodogram.duration[best_index]),
        "depth": float(periodogram.depth[best_index]),
        "sde": sde,
    }

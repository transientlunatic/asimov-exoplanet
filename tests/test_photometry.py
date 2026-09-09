"""
Unit tests for detrending and BLS transit search against synthetic
light curves with a known injected transit. No network access or Asimov
machinery involved -- these test ``photometry.py`` as plain functions.
"""

import unittest

import numpy as np

try:
    import lightkurve as lk

    from asimov_exoplanet import photometry

    PHOTOMETRY_AVAILABLE = True
except ImportError:
    PHOTOMETRY_AVAILABLE = False


def _synthetic_light_curve(
    rng,
    n_points=3000,
    cadence_days=0.0208,
    period=3.2,
    t0=0.7,
    duration=0.12,
    depth=0.01,
    trend_amplitude=0.001,
    trend_period=15.0,
    noise_std=0.0005,
):
    """Build a synthetic light curve with a box-shaped injected transit."""
    time = np.arange(n_points) * cadence_days

    phase = ((time - t0 + 0.5 * period) % period) - 0.5 * period
    in_transit = np.abs(phase) < (duration / 2)

    flux = np.ones(n_points)
    flux[in_transit] -= depth
    flux += trend_amplitude * np.sin(2 * np.pi * time / trend_period)
    flux += rng.normal(0, noise_std, n_points)

    return lk.LightCurve(time=time, flux=flux)


@unittest.skipUnless(PHOTOMETRY_AVAILABLE, "astropy/lightkurve not installed")
class DetrendTests(unittest.TestCase):
    def test_detrend_removes_long_term_trend(self):
        rng = np.random.default_rng(0)
        light_curve = _synthetic_light_curve(rng, depth=0.0, trend_amplitude=0.01)

        flattened = photometry.detrend(light_curve, window_length=0.5)

        # The raw light curve has an 0.01-amplitude sinusoidal trend; a
        # properly detrended curve should have much smaller scatter than
        # that around a flux of 1.0.
        self.assertLess(np.nanstd(flattened.flux.value), 0.005)
        self.assertAlmostEqual(np.nanmean(flattened.flux.value), 1.0, places=2)

    def test_detrend_returns_same_number_of_points(self):
        rng = np.random.default_rng(1)
        light_curve = _synthetic_light_curve(rng)
        flattened = photometry.detrend(light_curve, window_length=0.5)
        self.assertEqual(len(flattened), len(light_curve))


@unittest.skipUnless(PHOTOMETRY_AVAILABLE, "astropy/lightkurve not installed")
class SearchTests(unittest.TestCase):
    def test_search_recovers_injected_transit(self):
        rng = np.random.default_rng(42)
        true_period = 3.2
        true_depth = 0.01
        true_duration = 0.12

        light_curve = _synthetic_light_curve(
            rng, period=true_period, depth=true_depth, duration=true_duration
        )
        flattened = photometry.detrend(light_curve, window_length=0.5)

        result = photometry.search(
            flattened,
            period_min=0.5,
            period_max=20.0,
            duration_grid=[0.05, 0.10, 0.20],
            n_periods=20000,
        )

        self.assertAlmostEqual(result["period"], true_period, delta=0.05)
        self.assertAlmostEqual(result["depth"], true_depth, delta=0.005)
        self.assertGreater(result["sde"], 8.0)

    def test_search_returns_expected_keys(self):
        rng = np.random.default_rng(7)
        light_curve = _synthetic_light_curve(rng)
        flattened = photometry.detrend(light_curve, window_length=0.5)

        result = photometry.search(flattened, n_periods=2000)

        for key in ("period", "epoch", "duration", "depth", "sde"):
            self.assertIn(key, result)
            self.assertIsInstance(result[key], float)

    def test_search_low_significance_for_pure_noise(self):
        rng = np.random.default_rng(99)
        light_curve = _synthetic_light_curve(rng, depth=0.0, trend_amplitude=0.0)
        flattened = photometry.detrend(light_curve, window_length=0.5)

        result = photometry.search(flattened, n_periods=2000)

        self.assertLess(result["sde"], 8.0)


if __name__ == "__main__":
    unittest.main()

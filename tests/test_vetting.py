"""
Unit tests for the Phase 2 vetting checks, against synthetic light curves
with known injected signals. No network access or Asimov machinery
involved.
"""

import unittest

import numpy as np

try:
    import lightkurve as lk

    from asimov_exoplanet import vetting

    VETTING_AVAILABLE = True
except ImportError:
    VETTING_AVAILABLE = False


PERIOD = 3.2
EPOCH = 0.7
DURATION = 0.12
DEPTH = 0.01


def _base_light_curve(rng, n_points=4000, cadence_days=0.0208, noise_std=0.0003):
    time = np.arange(n_points) * cadence_days
    flux = np.ones(n_points)
    return time, flux, rng.normal(0, noise_std, n_points)


def _in_transit_mask(time, period=PERIOD, epoch=EPOCH, duration=DURATION):
    phase = ((time - epoch + 0.5 * period) % period) - 0.5 * period
    return np.abs(phase) < (duration / 2)


@unittest.skipUnless(VETTING_AVAILABLE, "astropy/lightkurve not installed")
class OddEvenTests(unittest.TestCase):
    def test_consistent_depth_passes(self):
        rng = np.random.default_rng(1)
        time, flux, noise = _base_light_curve(rng)
        flux[_in_transit_mask(time)] -= DEPTH
        flux += noise

        light_curve = lk.LightCurve(time=time, flux=flux)
        result = vetting.check_odd_even(light_curve, PERIOD, EPOCH, DURATION)

        self.assertTrue(result["consistent"])
        self.assertLess(result["significance"], vetting.SIGNIFICANCE_THRESHOLD)
        self.assertAlmostEqual(result["odd_depth"], DEPTH, delta=0.002)
        self.assertAlmostEqual(result["even_depth"], DEPTH, delta=0.002)

    def test_alternating_depth_is_flagged(self):
        rng = np.random.default_rng(2)
        time, flux, noise = _base_light_curve(rng)
        cycle = np.round((time - EPOCH) / PERIOD).astype(int)
        in_transit = _in_transit_mask(time)
        flux[in_transit & (cycle % 2 != 0)] -= 0.015
        flux[in_transit & (cycle % 2 == 0)] -= 0.005
        flux += noise

        light_curve = lk.LightCurve(time=time, flux=flux)
        result = vetting.check_odd_even(light_curve, PERIOD, EPOCH, DURATION)

        self.assertFalse(result["consistent"])
        self.assertGreater(result["significance"], vetting.SIGNIFICANCE_THRESHOLD)


@unittest.skipUnless(VETTING_AVAILABLE, "astropy/lightkurve not installed")
class SecondaryEclipseTests(unittest.TestCase):
    def test_no_secondary_eclipse_passes(self):
        rng = np.random.default_rng(3)
        time, flux, noise = _base_light_curve(rng)
        flux[_in_transit_mask(time)] -= DEPTH
        flux += noise

        light_curve = lk.LightCurve(time=time, flux=flux)
        result = vetting.check_secondary_eclipse(light_curve, PERIOD, EPOCH, DURATION)

        self.assertFalse(result["detected"])

    def test_secondary_eclipse_is_flagged(self):
        rng = np.random.default_rng(4)
        time, flux, noise = _base_light_curve(rng)
        flux[_in_transit_mask(time)] -= DEPTH

        secondary_phase = ((time - (EPOCH - 0.5 * PERIOD) + 0.5 * PERIOD) % PERIOD) - 0.5 * PERIOD
        flux[np.abs(secondary_phase) < DURATION / 2] -= 0.003
        flux += noise

        light_curve = lk.LightCurve(time=time, flux=flux)
        result = vetting.check_secondary_eclipse(light_curve, PERIOD, EPOCH, DURATION)

        self.assertTrue(result["detected"])
        self.assertGreater(result["significance"], vetting.SIGNIFICANCE_THRESHOLD)
        self.assertAlmostEqual(result["secondary_depth"], 0.003, delta=0.001)


@unittest.skipUnless(VETTING_AVAILABLE, "astropy/lightkurve not installed")
class VetTests(unittest.TestCase):
    def test_clean_transit_has_no_flags(self):
        rng = np.random.default_rng(5)
        time, flux, noise = _base_light_curve(rng)
        flux[_in_transit_mask(time)] -= DEPTH
        flux += noise

        light_curve = lk.LightCurve(time=time, flux=flux)
        result = vetting.vet(light_curve, {"period": PERIOD, "epoch": EPOCH, "duration": DURATION})

        self.assertEqual(result["flags"], [])
        self.assertIn("odd_even", result)
        self.assertIn("secondary_eclipse", result)

    def test_eclipsing_binary_like_signal_is_flagged(self):
        rng = np.random.default_rng(6)
        time, flux, noise = _base_light_curve(rng)
        flux[_in_transit_mask(time)] -= DEPTH
        secondary_phase = ((time - (EPOCH - 0.5 * PERIOD) + 0.5 * PERIOD) % PERIOD) - 0.5 * PERIOD
        flux[np.abs(secondary_phase) < DURATION / 2] -= 0.004
        flux += noise

        light_curve = lk.LightCurve(time=time, flux=flux)
        result = vetting.vet(light_curve, {"period": PERIOD, "epoch": EPOCH, "duration": DURATION})

        self.assertEqual(len(result["flags"]), 1)
        self.assertIn("secondary eclipse", result["flags"][0])


if __name__ == "__main__":
    unittest.main()

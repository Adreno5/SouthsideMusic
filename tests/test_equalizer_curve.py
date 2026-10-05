from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve, freqz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.audio_processing import (
    applyEqualizer,
    buildEqualizerKernel,
    sampleEqualizerCurve,
)


class EqualizerCurveTests(unittest.TestCase):
    def testCurveMatchesEditorBezier(self) -> None:
        bands = [(20.0, 0.0), (73.0, 20.0), (20000.0, 0.0)]
        x = np.log([frequency for frequency, _ in bands])
        for index in range(2):
            t = np.linspace(0.0, 1.0, 101)
            left, right = bands[index][1], bands[index + 1][1]
            width = x[index + 1] - x[index]
            slope = (right - left) / width
            control_left = left + (slope if index == 0 else 0.0) * width / 3
            control_right = right - (slope if index == 1 else 0.0) * width / 3
            expected = (
                (1 - t) ** 3 * left
                + 3 * (1 - t) ** 2 * t * control_left
                + 3 * (1 - t) * t**2 * control_right
                + t**3 * right
            )
            frequencies = np.exp(x[index] + t * width)
            np.testing.assert_allclose(
                sampleEqualizerCurve(bands, frequencies), expected, atol=1e-12
            )

    def testFilterFollowsBroadCurveAtDifferentRates(self) -> None:
        bands = [(20.0, 0.0), (73.0, 20.0), (20000.0, 0.0)]
        frequencies = np.geomspace(20.0, 20000.0, 2000)
        expected = sampleEqualizerCurve(bands, frequencies)
        for rate in (44100, 48000, 96000, 192000):
            with self.subTest(rate=rate):
                kernel = buildEqualizerKernel(bands, rate)
                assert kernel is not None
                _, response = freqz(kernel, worN=frequencies, fs=rate)
                error = np.abs(20 * np.log10(np.abs(response)) - expected)
                self.assertLess(float(np.max(error)), 0.75)
                self.assertLess(float(np.max(error[frequencies >= 40])), 0.1)

    def testMixedBoostAndCut(self) -> None:
        bands = [(20.0, 0.0), (200.0, 12.0), (2000.0, -15.0), (20000.0, 0.0)]
        frequencies = np.geomspace(40.0, 20000.0, 2000)
        kernel = buildEqualizerKernel(bands, 48000)
        assert kernel is not None
        _, response = freqz(kernel, worN=frequencies, fs=48000)
        np.testing.assert_allclose(
            20 * np.log10(np.abs(response)),
            sampleEqualizerCurve(bands, frequencies),
            atol=0.15,
        )

    def testBlockBoundariesMatchContinuousConvolution(self) -> None:
        kernel = buildEqualizerKernel(
            [(20.0, 0.0), (200.0, 12.0), (2000.0, -15.0), (20000.0, 0.0)],
            48000,
        )
        assert kernel is not None
        source = (
            np.random.default_rng(42).normal(0.0, 0.02, (12000, 2)).astype(np.float32)
        )
        retained = source.copy()
        state = None
        blocks = []
        position = 0
        for size in (512, 2048, 333, 4096, 5011):
            block, state = applyEqualizer(
                source[position : position + size], kernel, state
            )
            blocks.append(block)
            position += size
        expected = fftconvolve(source, kernel[:, None], axes=0)[: len(source)]
        np.testing.assert_allclose(np.concatenate(blocks), expected, atol=1e-6)
        np.testing.assert_array_equal(source, retained)

    def testFlatCurveBypassesOrAppliesConstantGain(self) -> None:
        self.assertIsNone(buildEqualizerKernel([(20.0, 0.0), (20000.0, 0.0)], 48000))
        self.assertIsNone(buildEqualizerKernel([], 48000))
        self.assertIsNone(buildEqualizerKernel([(20.0, 3.0)], 0))
        kernel = buildEqualizerKernel([(20.0, 6.0), (20000.0, 6.0)], 48000)
        assert kernel is not None
        self.assertEqual(len(kernel), 1)
        source = np.ones((256, 2), dtype=np.float32)
        output, state = applyEqualizer(source, kernel, None)
        np.testing.assert_allclose(output, 10.0 ** (6.0 / 20.0))
        self.assertIsNone(state)

    def testNyquistAndInvalidPoints(self) -> None:
        bands = [(20.0, 0.0), (200.0, -12.0), (20000.0, 0.0)]
        frequencies = np.geomspace(40.0, 7500.0, 1000)
        kernel = buildEqualizerKernel(bands, 16000)
        assert kernel is not None
        _, response = freqz(kernel, worN=frequencies, fs=16000)
        np.testing.assert_allclose(
            20 * np.log10(np.abs(response)),
            sampleEqualizerCurve(bands, frequencies),
            atol=0.15,
        )
        np.testing.assert_array_equal(
            sampleEqualizerCurve([(0.0, 2.0), (np.nan, 1.0)], frequencies), 0.0
        )

    def testAdjacentRepresentableFrequencies(self) -> None:
        bands = [
            (20.0, 0.0),
            (73.0, 20.0),
            (float(np.nextafter(73.0, 20000.0)), -20.0),
            (20000.0, 0.0),
        ]
        frequencies = np.geomspace(20.0, 20000.0, 2000)
        self.assertTrue(np.all(np.isfinite(sampleEqualizerCurve(bands, frequencies))))
        kernel = buildEqualizerKernel(bands, 48000)
        assert kernel is not None
        self.assertTrue(np.all(np.isfinite(kernel)))

    def testCoincidentFrequenciesKeepBothSides(self) -> None:
        for frequency in (73.0, 161.3):
            for right_frequency in (frequency, float(np.nextafter(frequency, 20000.0))):
                with self.subTest(frequency=frequency, right_frequency=right_frequency):
                    bands = [
                        (20.0, 0.0),
                        (frequency, 14.0),
                        (right_frequency, -4.0),
                        (20000.0, 0.0),
                    ]
                    gains = sampleEqualizerCurve(
                        bands, np.array([frequency * 0.999, right_frequency * 1.001])
                    )
                    self.assertGreater(gains[0], 13.9)
                    self.assertLess(gains[1], -3.9)
                    kernel = buildEqualizerKernel(bands, 48000)
                    assert kernel is not None
                    self.assertTrue(np.all(np.isfinite(kernel)))

    def testCoincidentEndpointFrequencies(self) -> None:
        bands = [(20.0, 2.0), (20.0, 6.0), (20000.0, -3.0), (20000.0, -8.0)]
        gains = sampleEqualizerCurve(bands, np.array([0.0, 20.0, 20000.0, 24000.0]))
        np.testing.assert_array_equal(gains, [6.0, 6.0, -8.0, -8.0])
        np.testing.assert_array_equal(
            sampleEqualizerCurve(
                [(73.0, 14.0), (73.0, -4.0)], np.array([20.0, 20000.0])
            ),
            [-4.0, -4.0],
        )


if __name__ == '__main__':
    unittest.main()

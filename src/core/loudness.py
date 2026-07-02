from __future__ import annotations

from functools import lru_cache
import logging
from typing import TYPE_CHECKING

from textwrap import dedent
import scipy.signal
import warnings
import numpy as np

if TYPE_CHECKING:
    from pydub import AudioSegment

_logger = logging.getLogger(__name__)


def validAudio(data, rate, block_size):
    """validate input is numpy floating array with correct shape and dimensions."""
    if not isinstance(data, np.ndarray):
        raise ValueError('Data must be of type numpy.ndarray.')

    if not np.issubdtype(data.dtype, np.floating):
        raise ValueError('Data must be floating point.')

    if data.ndim == 2 and data.shape[1] > 5:
        raise ValueError('Audio must have five channels or less.')

    if data.shape[0] < block_size * rate:
        raise ValueError('Audio must have length greater than the block size.')

    return True


class IirFilter(object):
    """iir filter for frequency weighting pre-filtering."""

    def __init__(self, G, Q, fc, rate, filter_type, passband_gain=1.0):
        self.G = G
        self.Q = Q
        self.fc = fc
        self.rate = rate
        self.filter_type = filter_type
        self.passband_gain = passband_gain

    def __str__(self):
        filter_info = dedent(
            """
        ------------------------------
        type: {type}
        ------------------------------
        Gain          = {G} dB
        Q factor      = {Q} 
        Center freq.  = {fc} Hz
        Sample rate   = {rate} Hz
        Passband gain = {passband_gain} dB
        ------------------------------
        b0 = {_b0}
        b1 = {_b1}
        b2 = {_b2}
        a0 = {_a0}
        a1 = {_a1}
        a2 = {_a2}
        ------------------------------
        """.format(
                type=self.filter_type,
                G=self.G,
                Q=self.Q,
                fc=self.fc,
                rate=self.rate,
                passband_gain=self.passband_gain,
                _b0=self.b[0],
                _b1=self.b[1],
                _b2=self.b[2],
                _a0=self.a[0],
                _a1=self.a[1],
                _a2=self.a[2],
            )
        )

        return filter_info

    def generateCoefficients(self):
        """generate biquad filter coefficients from instance parameters.

        based on rbj cookbook formulae for audio equalizer biquad filter coefficients.
        for itu specification compliance, use the 'DeMan' filter types.
        """
        A = 10 ** (self.G / 40.0)
        w0 = 2.0 * np.pi * (self.fc / self.rate)
        alpha = np.sin(w0) / (2.0 * self.Q)

        if self.filter_type == 'high_shelf':
            b0 = A * ((A + 1) + (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha)
            b1 = -2 * A * ((A - 1) + (A + 1) * np.cos(w0))
            b2 = A * ((A + 1) + (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha)
            a0 = (A + 1) - (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha
            a1 = 2 * ((A - 1) - (A + 1) * np.cos(w0))
            a2 = (A + 1) - (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha
        elif self.filter_type == 'low_shelf':
            b0 = A * ((A + 1) - (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha)
            b1 = 2 * A * ((A - 1) - (A + 1) * np.cos(w0))
            b2 = A * ((A + 1) - (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha)
            a0 = (A + 1) + (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha
            a1 = -2 * ((A - 1) + (A + 1) * np.cos(w0))
            a2 = (A + 1) + (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha
        elif self.filter_type == 'high_pass':
            b0 = (1 + np.cos(w0)) / 2
            b1 = -(1 + np.cos(w0))
            b2 = (1 + np.cos(w0)) / 2
            a0 = 1 + alpha
            a1 = -2 * np.cos(w0)
            a2 = 1 - alpha
        elif self.filter_type == 'low_pass':
            b0 = (1 - np.cos(w0)) / 2
            b1 = 1 - np.cos(w0)
            b2 = (1 - np.cos(w0)) / 2
            a0 = 1 + alpha
            a1 = -2 * np.cos(w0)
            a2 = 1 - alpha
        elif self.filter_type == 'peaking':
            b0 = 1 + alpha * A
            b1 = -2 * np.cos(w0)
            b2 = 1 - alpha * A
            a0 = 1 + alpha / A
            a1 = -2 * np.cos(w0)
            a2 = 1 - alpha / A
        elif self.filter_type == 'notch':
            b0 = 1
            b1 = -2 * np.cos(w0)
            b2 = 1
            a0 = 1 + alpha
            a1 = -2 * np.cos(w0)
            a2 = 1 - alpha
        elif self.filter_type == 'high_shelf_DeMan':
            K = np.tan(np.pi * self.fc / self.rate)
            Vh = np.power(10.0, self.G / 20.0)
            Vb = np.power(Vh, 0.499666774155)
            a0_ = 1.0 + K / self.Q + K * K
            b0 = (Vh + Vb * K / self.Q + K * K) / a0_
            b1 = 2.0 * (K * K - Vh) / a0_
            b2 = (Vh - Vb * K / self.Q + K * K) / a0_
            a0 = 1.0
            a1 = 2.0 * (K * K - 1.0) / a0_
            a2 = (1.0 - K / self.Q + K * K) / a0_
        elif self.filter_type == 'high_pass_DeMan':
            K = np.tan(np.pi * self.fc / self.rate)
            a0 = 1.0
            a1 = 2.0 * (K * K - 1.0) / (1.0 + K / self.Q + K * K)
            a2 = (1.0 - K / self.Q + K * K) / (1.0 + K / self.Q + K * K)
            b0 = 1.0
            b1 = -2.0
            b2 = 1.0
        else:
            raise ValueError('Invalid filter type', self.filter_type)

        return np.array([b0, b1, b2]) / a0, np.array([a0, a1, a2]) / a0

    def applyFilter(self, data):
        """apply the iir filter to the input signal."""
        return self.passband_gain * scipy.signal.lfilter(self.b, self.a, data)  # type: ignore

    @property
    def a(self):
        return self.generateCoefficients()[1]

    @property
    def b(self):
        return self.generateCoefficients()[0]


class Meter(object):
    """loudness meter based on ITU-R BS.1770-4 gating algorithm."""

    def __init__(
        self, rate, filter_class='K-weighting', block_size=0.400, overlap=0.75
    ):
        self.rate = rate
        self.filter_class = filter_class
        self.block_size = block_size
        self.overlap = overlap
        self.blockwise_loudness = []

    def integratedLoudness(self, data):
        """measure integrated gated loudness of a signal in db LUFS.

        input data shape: (samples, ch) or (samples,) for mono, up to 5 channels.
        channel order: [Left, Right, Center, Left surround, Right surround].
        """
        input_data = data.copy()
        validAudio(input_data, self.rate, self.block_size)

        if input_data.ndim == 1:
            input_data = np.reshape(input_data, (input_data.shape[0], 1))

        numChannels = input_data.shape[1]
        numSamples = input_data.shape[0]

        for filter_class, filter_stage in self._filters.items():
            for ch in range(numChannels):
                input_data[:, ch] = filter_stage.applyFilter(input_data[:, ch])

        G = [1.0, 1.0, 1.0, 1.41, 1.41]
        T_g = self.block_size
        Gamma_a = -70.0
        overlap = self.overlap
        step = 1.0 - overlap

        T = numSamples / self.rate
        numBlocks = int(np.round(((T - T_g) / (T_g * step))) + 1)  # (see end of eq. 3)
        j_range = np.arange(0, numBlocks)
        z = np.zeros(shape=(numChannels, numBlocks))

        for i in range(numChannels):
            for j in j_range:
                start = int(T_g * (j * step) * self.rate)
                u = int(T_g * (j * step + 1) * self.rate)
                z[i, j] = (1.0 / (T_g * self.rate)) * np.sum(
                    np.square(input_data[start:u, i])
                )

        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=RuntimeWarning)
            loudness_blocks = [
                -0.691
                + 10.0 * np.log10(np.sum([G[i] * z[i, j] for i in range(numChannels)]))
                for j in j_range
            ]
        self.blockwise_loudness = loudness_blocks

        J_g = [j for j, l_j in enumerate(loudness_blocks) if l_j >= Gamma_a]

        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=RuntimeWarning)
            z_avg_gated = [np.mean([z[i, j] for j in J_g]) for i in range(numChannels)]
        Gamma_r = (
            -0.691
            + 10.0
            * np.log10(np.sum([G[i] * z_avg_gated[i] for i in range(numChannels)]))
            - 10.0
        )

        J_g = [
            j
            for j, l_j in enumerate(loudness_blocks)
            if (l_j > Gamma_r and l_j > Gamma_a)
        ]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=RuntimeWarning)
            z_avg_gated = np.nan_to_num(
                np.array([np.mean([z[i, j] for j in J_g]) for i in range(numChannels)])
            )

        # calculate final loudness gated loudness (see eq. 7)
        with np.errstate(divide='ignore'):
            LUFS = -0.691 + 10.0 * np.log10(
                np.sum([G[i] * z_avg_gated[i] for i in range(numChannels)])
            )

        return LUFS

    def loudnessRange(self, data):
        """measure the loudness range (LRA) of a signal in LU units.

        returns NaN if the signal is too quiet to compute LRA.
        """
        original_block_size = self.block_size
        original_overlap = self.overlap

        try:
            self.block_size = 3.0
            self.overlap = 0.97
            data = self._appendSilence(data, silence_duration_sec=1.5)
            self.integratedLoudness(data)
            if not self.blockwise_loudness:
                raise ValueError('No blockwise loudness found')
            ABS_THRES = -70
            REL_THRES = -20
            PRC_LOW = 10
            PRC_HIGH = 95

            stl_absgated_vec = [x for x in self.blockwise_loudness if x >= ABS_THRES]

            if len(stl_absgated_vec) == 0:
                return np.nan

            # Apply the relative-threshold gating
            n = len(stl_absgated_vec)
            stl_power = np.sum(np.power(10, np.divide(stl_absgated_vec, 10))) / n
            stl_integrated = 10 * np.log10(stl_power)
            stl_relgated_vec = [
                x for x in stl_absgated_vec if x >= stl_integrated + REL_THRES
            ]

            if len(stl_relgated_vec) == 0:
                return np.nan

            stl_perc_low = np.percentile(stl_relgated_vec, PRC_LOW)
            stl_perc_high = np.percentile(stl_relgated_vec, PRC_HIGH)
            LRA = stl_perc_high - stl_perc_low
            return LRA
        finally:
            self.block_size = original_block_size
            self.overlap = original_overlap

    def _appendSilence(self, data, silence_duration_sec):
        num_silence_samples = int(silence_duration_sec * self.rate)
        silence = np.zeros(num_silence_samples)

        if len(data.shape) == 1:
            new_audio_data = np.concatenate((data, silence))
        elif len(data.shape) == 2:
            num_channels = data.shape[1]
            silence = np.zeros((num_silence_samples, num_channels))
            new_audio_data = np.concatenate((data, silence), axis=0)
        else:
            raise ValueError('Invalid shape for audio data')
        return new_audio_data

    @property
    def filter_class(self):
        return self._filter_class

    @filter_class.setter
    def filter_class(self, value):
        self._filters = {}  # reset (clear) filters
        self._filter_class = value
        if self._filter_class == 'K-weighting':
            self._filters['high_shelf'] = IirFilter(
                4.0, 1 / np.sqrt(2), 1500.0, self.rate, 'high_shelf'
            )
            self._filters['high_pass'] = IirFilter(
                0.0, 0.5, 38.0, self.rate, 'high_pass'
            )
        elif self._filter_class == 'Fenton/Lee 1':
            self._filters['high_shelf'] = IirFilter(
                5.0, 1 / np.sqrt(2), 1500.0, self.rate, 'high_shelf'
            )
            self._filters['high_pass'] = IirFilter(
                0.0, 0.5, 130.0, self.rate, 'high_pass'
            )
            self._filters['peaking'] = IirFilter(
                0.0, 1 / np.sqrt(2), 500.0, self.rate, 'peaking'
            )
        elif self._filter_class == 'Fenton/Lee 2':  # not yet implemented
            self._filters['high_self'] = IirFilter(
                4.0, 1 / np.sqrt(2), 1500.0, self.rate, 'high_shelf'
            )
            self._filters['high_pass'] = IirFilter(
                0.0, 0.5, 38.0, self.rate, 'high_pass'
            )
        elif self._filter_class == 'Dash et al.':
            self._filters['high_pass'] = IirFilter(
                0.0, 0.375, 149.0, self.rate, 'high_pass'
            )
            self._filters['peaking'] = IirFilter(
                -2.93820927, 1.68878655, 1000.0, self.rate, 'peaking'
            )
        elif self._filter_class == 'DeMan':
            self._filters['high_shelf_DeMan'] = IirFilter(
                3.99984385397,
                0.7071752369554193,
                1681.9744509555319,
                self.rate,
                'high_shelf_DeMan',
            )
            self._filters['high_pass_DeMan'] = IirFilter(
                0.0, 0.5003270373253953, 38.13547087613982, self.rate, 'high_pass_DeMan'
            )
        elif self._filter_class == 'custom':
            pass
        else:
            raise ValueError('Invalid filter class:', self._filter_class)


def getAdjustedGainFactor(target_lufs: float, audio: AudioSegment) -> float:
    return getAdjustedGainFactorImpl(target_lufs, audio)


def getAdjustedGainFactorFromSamples(
    target_lufs: float,
    samples_bytes: bytes,
    sample_width: int,
    frame_rate: int,
) -> float:
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map[sample_width]
    samples = np.frombuffer(samples_bytes, dtype=dtype).astype(np.float32)
    max_val = np.iinfo(dtype).max  # type: ignore[type-var]
    samples = samples / max_val  # type: ignore[assignment]

    meter = Meter(frame_rate)
    loudness = meter.integratedLoudness(samples)

    gain = 10 ** ((target_lufs - loudness) / 20.0)
    _logger.info(f'loudness adjusted, {gain=}, {target_lufs=}')
    return gain


@lru_cache(maxsize=1024)
def getAdjustedGainFactorImpl(target_lufs: float, audio: AudioSegment) -> float:
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map[audio.sample_width]
    max_val = np.iinfo(dtype).max  # type: ignore[type-var]
    samples = samples / max_val  # type: ignore[assignment]

    meter = Meter(audio.frame_rate)
    loudness = meter.integratedLoudness(samples)

    gain = 10 ** ((target_lufs - loudness) / 20.0)
    _logger.info(f'loudness adjusted, {gain=}, {target_lufs=}')
    return gain

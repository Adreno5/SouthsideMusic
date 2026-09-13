#!/usr/bin/env python3
"""Beat detection test script for SouthsideMusic.

Tests BeatDetector on a diverse sample of tracks and generates:
- Beat interval stability metrics (coefficient of variation)
- Detection value stability over time
- Beat points per minute
- Visual plots with waveform + beat markers
- JSON results summary
"""

import json
import sys
from pathlib import Path
from typing import TypedDict

import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
from scipy.io import wavfile

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))
from core.beat import BeatDetector, BeatFrame


class TrackInfo(TypedDict):
    id: str
    name: str
    artists: str
    duration_ms: int
    hash: str
    folder: str


class BeatMetrics(TypedDict):
    track_id: str
    track_name: str
    artists: str
    duration_s: float
    beat_count: int
    beats_per_minute: float
    interval_mean_s: float
    interval_std_s: float
    interval_cv: float
    detection_value_mean: float
    detection_value_std: float
    detection_value_cv: float
    detection_value_min: float
    detection_value_max: float
    has_audio: bool
    error: str | None


# Diverse sample spanning different durations/tempos
# Using actual files present in data/music directory
TEST_TRACKS: list[TrackInfo] = [
    {
        'id': 'track1',
        'name': 'Track 1',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': '75b4cc1f8d8520ec10efab10810db7d13b7ae1c6de36bb72f305c34a8bb695d6',
        'folder': ''
    },
    {
        'id': 'track2',
        'name': 'Track 2',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': '0eb15285070e22011fc7d60aacdf7e07aff0769afc4a1e1c9f0d9673990305dc',
        'folder': ''
    },
    {
        'id': 'track3',
        'name': 'Track 3',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': '33ba5d3a5ff6d749b4b772d21170704d4996161e44338d53e2a9b550c2260d68',
        'folder': ''
    },
    {
        'id': 'track4',
        'name': 'Track 4',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': '3854512a96b56af53e32141b5f169f518b0ad16e04ea78a7adaaa0db73802e75',
        'folder': ''
    },
    {
        'id': 'track5',
        'name': 'Track 5',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': '4aea5466068f7573a72a01f12d5cdeb8111834d6fb7a2484564727aa9aaccbf4',
        'folder': ''
    },
    {
        'id': 'track6',
        'name': 'Track 6',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': '51b4a264d0e603b3bee430bcb4549616575af0aceecb28dba7a3d4be7e6a9b6b',
        'folder': ''
    },
    {
        'id': 'track7',
        'name': 'Track 7',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': '7e57f0da163f5532f1a8d22aa2faa93bf2d14875393f9a638ba0b87bbf80f25e',
        'folder': ''
    },
    {
        'id': 'track8',
        'name': 'Track 8',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': '9cfef71e1624afc328576787efbbe3491363332f68acb936a7fadb429f1defa4',
        'folder': ''
    },
    {
        'id': 'track9',
        'name': 'Track 9',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': 'acb1e6f977cb1c6cd8b1db02b6b171c840dfcdb1fe7f12c81d3698b25c48fe9a',
        'folder': ''
    },
    {
        'id': 'track10',
        'name': 'Track 10',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': 'b32bd8beede9750c4d14a5880e42401c49b233101a46d78ef629b2a9a41603bf',
        'folder': ''
    },
    {
        'id': 'track11',
        'name': 'Track 11',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': 'bac84d61891cdbff77eabdfb1941db330255468dc2a66222ac6fe9c428e3866f',
        'folder': ''
    },
    {
        'id': 'track12',
        'name': 'Track 12',
        'artists': 'Unknown',
        'duration_ms': 0,
        'hash': 'bddb4ec69c84d9601961aa23d39c6172768cebf1281217a6f5e000778c6f0a37',
        'folder': ''
    },
]


def find_audio_file(track: TrackInfo, music_dir: Path) -> Path | None:
    """Locate the audio file for a track."""
    if track['hash']:
        # Try hash-based file (no extension)
        audio_path = music_dir / track['hash']
        if audio_path.exists():
            return audio_path

        wav_path = music_dir / f"{track['hash']}.wav"
        if wav_path.exists():
            return wav_path

    # Fallback: try song ID
    audio_path = music_dir / track['id']
    if audio_path.exists():
        return audio_path

    wav_path = music_dir / f"{track['id']}.wav"
    if wav_path.exists():
        return wav_path

    return None


def load_audio(file_path: Path) -> tuple[np.ndarray, int] | None:
    """Load audio file and return (samples, sample_rate)."""
    try:
        # First try as WAV
        try:
            sample_rate, data = wavfile.read(file_path)
        except Exception:
            # If WAV fails, try to convert from FLAC using librosa or pydub
            try:
                import librosa
                data, sample_rate = librosa.load(file_path, sr=None, mono=False)
                # librosa returns float32 [-1, 1] already
                if data.ndim == 2:
                    data = np.mean(data, axis=0)
                return data, sample_rate
            except ImportError:
                # Try pydub as fallback
                from pydub import AudioSegment
                audio = AudioSegment.from_file(str(file_path))
                sample_rate = audio.frame_rate
                # Convert to numpy array
                samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
                # Normalize to [-1, 1]
                if audio.sample_width == 2:
                    samples = samples / 32768.0
                elif audio.sample_width == 4:
                    samples = samples / 2147483648.0
                # Convert stereo to mono
                if audio.channels == 2:
                    samples = samples.reshape((-1, 2)).mean(axis=1)
                return samples, sample_rate

        # Convert to float32 [-1, 1]
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        elif data.dtype == np.uint8:
            data = (data.astype(np.float32) - 128) / 128.0
        else:
            data = data.astype(np.float32)

        # Convert stereo to mono
        if data.ndim == 2:
            data = np.mean(data, axis=1)

        return data, sample_rate
    except Exception as e:
        print(f"Failed to load {file_path}: {e}")
        return None


def analyze_track(track: TrackInfo, audio_path: Path) -> BeatMetrics:
    """Run beat detection and compute metrics."""
    result: BeatMetrics = {
        'track_id': track['id'],
        'track_name': track['name'],
        'artists': track['artists'],
        'duration_s': track['duration_ms'] / 1000.0,
        'beat_count': 0,
        'beats_per_minute': 0.0,
        'interval_mean_s': 0.0,
        'interval_std_s': 0.0,
        'interval_cv': 0.0,
        'detection_value_mean': 0.0,
        'detection_value_std': 0.0,
        'detection_value_cv': 0.0,
        'detection_value_min': 0.0,
        'detection_value_max': 0.0,
        'has_audio': True,
        'error': None
    }

    try:
        audio_data = load_audio(audio_path)
        if audio_data is None:
            result['has_audio'] = False
            result['error'] = 'Failed to load audio'
            return result

        samples, sample_rate = audio_data

        # Run beat detector
        detector = BeatDetector()
        frames: list[BeatFrame] = detector.process(
            samples,
            sample_rate,
            sensitivity=1.0,
            smoothing=0.35,
            point_threshold=0.25
        )

        if not frames:
            result['error'] = 'No frames returned'
            return result

        # Extract beat points
        beat_times = [f.time for f in frames if f.is_point]
        detection_values = [f.intensity for f in frames]

        result['beat_count'] = len(beat_times)

        if len(beat_times) >= 2:
            duration_s = frames[-1].time - frames[0].time
            result['beats_per_minute'] = (len(beat_times) / duration_s) * 60.0 if duration_s > 0 else 0.0

            # Beat interval statistics
            intervals = np.diff(beat_times)
            result['interval_mean_s'] = float(np.mean(intervals))
            result['interval_std_s'] = float(np.std(intervals))
            result['interval_cv'] = float(np.std(intervals) / np.mean(intervals)) if np.mean(intervals) > 0 else 0.0

        # Detection value statistics
        if detection_values:
            result['detection_value_mean'] = float(np.mean(detection_values))
            result['detection_value_std'] = float(np.std(detection_values))
            result['detection_value_cv'] = float(
                np.std(detection_values) / np.mean(detection_values)
            ) if np.mean(detection_values) > 0 else 0.0
            result['detection_value_min'] = float(np.min(detection_values))
            result['detection_value_max'] = float(np.max(detection_values))

        return result

    except Exception as e:
        result['error'] = str(e)
        return result


def plot_beats(track: TrackInfo, audio_path: Path, output_dir: Path) -> None:
    """Generate waveform plot with beat markers."""
    try:
        audio_data = load_audio(audio_path)
        if audio_data is None:
            return

        samples, sample_rate = audio_data

        # Run detector
        detector = BeatDetector()
        frames = detector.process(samples, sample_rate)

        if not frames:
            return

        # Extract data
        times = [f.time for f in frames]
        intensities = [f.intensity for f in frames]
        beat_times = [f.time for f in frames if f.is_point]

        # Create figure
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
        fig.patch.set_facecolor('#0A0D12')

        # Waveform
        duration = len(samples) / sample_rate
        time_axis = np.linspace(0, duration, len(samples))
        ax1.plot(time_axis, samples, color='#38BDF8', linewidth=0.4, alpha=0.7)
        ax1.set_ylabel('Amplitude', color='#E0E6ED', fontsize=10)
        ax1.set_title(
            f"{track['name']} - {track['artists']}\n{len(beat_times)} beats detected",
            color='#E0E6ED',
            fontsize=12,
            pad=12
        )
        ax1.set_facecolor('#0F131C')
        ax1.tick_params(colors='#8B92A0', labelsize=9)
        ax1.spines['bottom'].set_color('#1E2636')
        ax1.spines['top'].set_color('#1E2636')
        ax1.spines['left'].set_color('#1E2636')
        ax1.spines['right'].set_color('#1E2636')
        ax1.grid(True, alpha=0.15, color='#38BDF8', linewidth=0.5)

        # Mark beats on waveform
        for bt in beat_times:
            ax1.axvline(bt, color='#E9A568', alpha=0.6, linewidth=1.2, linestyle='--')

        # Detection intensity
        ax2.plot(times, intensities, color='#6EE7B7', linewidth=1.5)
        ax2.scatter(beat_times, [1.0] * len(beat_times), color='#E9A568', s=40, zorder=5, alpha=0.8)
        ax2.set_xlabel('Time (s)', color='#E0E6ED', fontsize=10)
        ax2.set_ylabel('Detection Intensity', color='#E0E6ED', fontsize=10)
        ax2.set_facecolor('#0F131C')
        ax2.tick_params(colors='#8B92A0', labelsize=9)
        ax2.spines['bottom'].set_color('#1E2636')
        ax2.spines['top'].set_color('#1E2636')
        ax2.spines['left'].set_color('#1E2636')
        ax2.spines['right'].set_color('#1E2636')
        ax2.grid(True, alpha=0.15, color='#38BDF8', linewidth=0.5)
        ax2.set_ylim(-0.05, 1.15)

        plt.tight_layout()

        # Save
        safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in track['name'])
        output_path = output_dir / f"beat_analysis_{track['id']}_{safe_name[:40]}.png"
        plt.savefig(output_path, dpi=120, facecolor='#0A0D12')
        plt.close()

        print(f"  Saved plot: {output_path.name}")

    except Exception as e:
        print(f"  Failed to plot {track['name']}: {e}")


def main() -> None:
    """Run beat detection tests on all sample tracks."""
    base_dir = Path(__file__).parent
    music_dir = base_dir / 'data' / 'music'
    output_dir = base_dir / 'beat_test_results'
    output_dir.mkdir(exist_ok=True)

    print("Beat Detection Test Suite")
    print("=" * 60)
    print(f"Testing {len(TEST_TRACKS)} tracks")
    print(f"Music directory: {music_dir}")
    print(f"Output directory: {output_dir}")
    print()

    results: list[BeatMetrics] = []

    for i, track in enumerate(TEST_TRACKS, 1):
        print(f"[{i}/{len(TEST_TRACKS)}] {track['name']} - {track['artists']}")

        audio_path = find_audio_file(track, music_dir)

        if audio_path is None:
            print(f"  [!] Audio file not found")
            results.append({
                'track_id': track['id'],
                'track_name': track['name'],
                'artists': track['artists'],
                'duration_s': track['duration_ms'] / 1000.0,
                'beat_count': 0,
                'beats_per_minute': 0.0,
                'interval_mean_s': 0.0,
                'interval_std_s': 0.0,
                'interval_cv': 0.0,
                'detection_value_mean': 0.0,
                'detection_value_std': 0.0,
                'detection_value_cv': 0.0,
                'detection_value_min': 0.0,
                'detection_value_max': 0.0,
                'has_audio': False,
                'error': 'Audio file not found'
            })
            continue

        print(f"  Audio: {audio_path.name}")

        # Analyze
        metrics = analyze_track(track, audio_path)
        results.append(metrics)

        if metrics['error']:
            print(f"  [!] Error: {metrics['error']}")
        else:
            print(f"  [OK] Beats: {metrics['beat_count']}")
            print(f"    BPM: {metrics['beats_per_minute']:.1f}")
            print(f"    Interval CV: {metrics['interval_cv']:.3f}")
            print(f"    Detection value: {metrics['detection_value_mean']:.3f} "
                  f"(CV: {metrics['detection_value_cv']:.3f})")

        # Generate plot
        if metrics['has_audio'] and not metrics['error']:
            plot_beats(track, audio_path, output_dir)

        print()

    # Save JSON results
    json_path = output_dir / 'beat_test_results.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("=" * 60)
    print(f"Results saved to: {json_path}")
    print()

    # Summary statistics
    successful = [r for r in results if r['has_audio'] and not r['error']]
    if successful:
        print("Summary Statistics:")
        print(f"  Tracks analyzed: {len(successful)}/{len(TEST_TRACKS)}")
        print(f"  Avg BPM: {np.mean([r['beats_per_minute'] for r in successful]):.1f}")
        print(f"  Avg interval CV: {np.mean([r['interval_cv'] for r in successful]):.3f}")
        print(f"  Avg detection CV: {np.mean([r['detection_value_cv'] for r in successful]):.3f}")


if __name__ == '__main__':
    main()

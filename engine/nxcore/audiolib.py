"""Shared audio helpers (numpy + ffmpeg only, no librosa) — Justin EDIT Suite."""
import os
import subprocess

import numpy as np

SR = 22050
HOP = 512                      # 23.2 ms @ 22.05 kHz
NFFT = 2048
AUDIO_EXT = (".wav", ".mp3", ".aif", ".aiff", ".flac", ".m4a", ".ogg", ".aac")


def decode(path, sr=SR, max_sec=None, start=0.0):
    """Decode any audio/video file to mono float32 via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", path]
    if max_sec:
        cmd += ["-t", str(max_sec)]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout, np.float32).copy()


def file_key(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


def frames(x, n=NFFT, hop=HOP):
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    cnt = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(cnt)[:, None]
    return x[idx]


def stft_mag(x, n=NFFT, hop=HOP):
    return np.abs(np.fft.rfft(frames(x, n, hop) * np.hanning(n)[None, :], axis=1)).astype(np.float32)


def band_edges(sr=SR, n=NFFT, nb=12, fmin=40.0, fmax=10000.0):
    f = np.fft.rfftfreq(n, 1.0 / sr)
    e = np.geomspace(fmin, min(fmax, sr / 2 - 1), nb + 1)
    return [(int(np.searchsorted(f, e[i])), int(max(np.searchsorted(f, e[i + 1]), np.searchsorted(f, e[i]) + 1))) for i in range(nb)]


def db(x, floor=1e-9):
    return 20.0 * np.log10(np.maximum(x, floor))


def rms_env(x, sr=SR, win_ms=10):
    w = max(1, int(sr * win_ms / 1000))
    n = len(x) // w
    if n == 0:
        return np.zeros(1, np.float32)
    return np.sqrt(np.mean(x[: n * w].reshape(n, w) ** 2, axis=1) + 1e-12)

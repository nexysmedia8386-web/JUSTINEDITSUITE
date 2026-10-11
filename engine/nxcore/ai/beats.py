"""Neural beat + downbeat tracking with beat_this (CPJKU, "Beat this! Accurate beat tracking without DBN
postprocessing", ISMIR 2024, MIT). Replaces the onset/autocorrelation tracker of nxcore.music when available:
far fewer half/double-tempo and off-beat errors, real downbeats (bar 1) instead of "loudest kick of 4".

    beats, downbeats = track(signal, sr)        # seconds; None when beat_this / torch is unavailable

pip install https://github.com/CPJKU/beat_this/archive/main.zip   (weights "final0" download once, ~80 MB)
"""
import numpy as np

from . import cached, device, enabled, has, torch_ok

ENGINE = "beat_this"


def available():
    return enabled("beats") and torch_ok() and has("beat_this")


def _model():
    from beat_this.inference import Audio2Beats
    return Audio2Beats(checkpoint_path="final0", device=device(), dbn=False)


def track(signal, sr):
    """mono float signal → (beats, downbeats) as sorted float lists in seconds, or None."""
    if not available():
        return None
    x = np.asarray(signal, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    m = cached("beat_this", _model)
    b, d = m(x, sr)
    b = [round(float(t), 3) for t in np.asarray(b).ravel()]
    d = [round(float(t), 3) for t in np.asarray(d).ravel()]
    if len(b) < 4:
        return None
    return b, d

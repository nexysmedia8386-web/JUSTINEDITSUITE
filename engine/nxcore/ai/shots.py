"""Shot boundary detection with TransNetV2 (Souček & Lokoč 2020; PyTorch port transnetv2-pytorch, MIT, weights
bundled). Finds hard cuts AND gradual transitions (dissolves, fades, in-camera transitions) frame-accurately; the
classic keyframe-difference detector in nxcore.segments only sees hard changes between keyframes (~1 s apart).

    cuts(path) → [seconds]  (start of each new shot, first shot excluded) or None when unavailable
"""
import threading

import numpy as np

from . import cached, device, enabled, has, torch_ok

ENGINE = "transnetv2"
THR = 0.5
_run_lock = threading.Lock()            # one GPU inference at a time (segments runs several clips in parallel)


def available():
    return enabled("shots") and torch_ok() and has("transnetv2_pytorch")


def wanted(info):
    """Only EDITED material has cuts / dissolves inside a file (exports, downloads, screen recordings…). A camera
    original is one continuous take: measured on a 6-min DJI 4K clip TransNetV2 cost ~50 s (every frame decoded) and
    found 0 cuts — the keyframe detector already handles scene changes within a take. So: files without a known
    camera only; NX_AI_SHOTS=all forces every clip."""
    import os
    if os.environ.get("NX_AI_SHOTS", "").lower() == "all":
        return True
    return not (info or {}).get("cam")


def _model():
    import contextlib
    import io
    from transnetv2_pytorch import TransNetV2
    with contextlib.redirect_stdout(io.StringIO()):   # the port prints device banners
        m = TransNetV2(device=device())
    return m.eval()


def cuts(path, fps=None, thr=THR):
    """Frame-accurate shot starts in seconds. fps: the clip's frame rate (media.json) to convert frame → time."""
    if not available():
        return None
    import torch
    from . import frames as _fr
    fr = list(_fr.stream(path, None, 48, 27))
    if len(fr) < 30:
        return []
    m = cached("transnetv2", _model)
    x = torch.from_numpy(np.stack(fr)).to(m.device)
    with _run_lock, torch.no_grad():
        single, _ = m.predict_frames(x, quiet=True)
    p = single.float().cpu().numpy().ravel()
    scenes = m.predictions_to_scenes(p, thr)
    rate = float(fps or 30.0)
    return [round(float(s[0]) / rate, 3) for s in scenes[1:]]

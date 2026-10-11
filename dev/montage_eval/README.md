# Montage evaluation — "does the shot take the right seconds?"

Built 2026-10-10 when the editor reported that most beat-cut shots missed the moment worth taking.
Source: the editor's GoPro folder `G:/PQ/SOURCE/video Gopro` (84 clips, music `G:/PQ/INTRO 1.mp3`).

| Script | What it does |
|---|---|
| `shot_sheet.py [i …]` | Contact sheet of plan shot *i*: the whole clip at 2 fps, the window taken outlined, the activity curve under it. The labels below came from looking at these. |
| `moment_eval.py [m e in fg]` | 14 hand-labelled shots (6 that took the wrong seconds, 8 that were fine). Re-analyses those clips once with the current stability pass (cached in `stab_v8.pkl`), then runs `Material.best_sub` per weight set and counts hits. |
| `montage_ab.py` | Full montage of the folder: old code vs gap fix only vs current → empty slots, share of the clip's action the chosen windows hold, reuse. |

Results when shipped (v2.6.1): labelled set wrong-moment 6 → 2, already-right 8/8 kept; whole montage
action held (median) 52 % → 71 %, empty "THIẾU HÌNH" slots 8 → 0, no overlapping footage, 82 clips used.

Label changes made after review (both reasons are in `moment_eval.py`): GX019705 widened to 2.0–8.0 (the tilt-up
reveal at 2.2 s is a real moment), GX019746 + 13.6–17.4 (turns and looks at the camera).

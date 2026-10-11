# Automatic video editing quality: 2023–2026 methods Justin EDIT Suite could adopt (beyond LO_TRINH_THUAT_TOAN.md)

Scope note: the items already done in `docs/LO_TRINH_THUAT_TOAN.md` are not re-recommended: A1 global shot assignment, A2 visual beats, A3 pan continuity, A4 missed fillers, A5 1/f pacing, B1 preference learning, B2 SigLIP2→Qwen3-VL moment search, B3 DPP/submodular coverage, B4 LLM chapters, B5 thumbnails, C1 retention learning, C2 PANNs audio search, C3 WeSpeaker diarization, D1 moment/exposure curves, D2 gap filling. Where a new method overlaps one of these, I say so. "Classic" = before 2023. License flags: **NC** = non-commercial or research-only. Cost estimates are my own engineering judgment, not taken from sources.

---

## 1. Highlight and "interesting moment" detection, and video summarization, usable offline

### Takeaway
Query-based highlight DETRs (CG-DETR, TR-DETR, QD-DETR) are now packaged for inference in Lighthouse (Apache-2.0 code), and they reach about 64–67 HIT@1 on QVHighlights. But their weights are trained on QVHighlights annotations, which are **CC BY-NC-SA, research only**. The inference API also caps videos at 150 s, and the models are tuned to vlog/news queries, not to GoPro action. For this tool, cheaper signals that need no license review should come first: GoPro GPMF telemetry, cross-clip "recurrence" pseudo-labels, and training-free clip scoring. The training-free clip scoring is close to what B2 already does.

### Cited Findings
**Supervised moment-retrieval/highlight DETRs (2023–2024)**
- Lighthouse (LY Corp, EMNLP 2024 demo) provides one inference API for Moment-DETR, QD-DETR, EaTR, CG-DETR, UVCOM, TR-DETR, TaskWeave and R2-Tuning. Code is Apache-2.0. Weights are on Google Drive with **no stated license**. Because of copyright, only feature files are distributed — [Lighthouse GitHub](https://github.com/line/lighthouse)
- Lighthouse API: `CGDETRPredictor.encode_video()` then `predict(query, video)` returns `pred_relevant_windows` (start, end, score) and per-clip `pred_saliency_scores`. CPU users should use `feature_name='clip'` because CLIP+SlowFast is "very slow without GPUs". Maximum video duration is 150 s. Feature options include CLIP, CLIP+SlowFast, and CLIP+SlowFast+PANNs audio — [Lighthouse GitHub](https://github.com/line/lighthouse)
- Lighthouse reproduced QVHighlights **val** scores (MR R1@0.5 / R1@0.7 / mAP; HD mAP / HIT@1), CLIP features: Moment-DETR 53.5/34.1/32.4; 35.3/54.0 · QD-DETR 59.7/42.3/37.5; 38.0/59.2 · TR-DETR 63.6/43.9/39.6; 40.1/63.2 · UVCOM 64.8/48.0/42.3; 38.7/62.2 · **CG-DETR 66.6/49.9/43.9; 39.9/64.3**. With CLIP+SlowFast, CG-DETR reaches HIT@1 67.0 and TR-DETR 66.2 — [Lighthouse paper v3](https://arxiv.org/html/2408.02901v3)
- On TVSum highlight detection, newer methods do not always win. Moment-DETR with CLIP leads at 89.1 avg mAP vs CG-DETR 88.1 (CLIP) and 88.8 (CLIP+SlowFast) — [Lighthouse paper v3](https://arxiv.org/html/2408.02901v3)
- TR-DETR (AAAI 2024) reports QVHighlights test R1@0.5 64.66 and HIT@1 63.42 vs QD-DETR 62.40/62.40 (the two equal QD-DETR values come from a search summary, so verify in the paper). TR-DETR needs joint MR+HD labels, so it cannot be trained for HD alone — [TR-DETR](https://arxiv.org/pdf/2401.02309); [Lighthouse paper](https://arxiv.org/pdf/2408.02901)
- An aggregator leaderboard lists 2025 QVHighlights HD tops at roughly 71 HIT@1 / 44–45 mAP: SG-DETR (w/ pretraining) 71.00/44.70, FlashVTG 71.01/44.09, VideoLights-B-pt 70.56/42.84, HL-CLIP 70.60/41.94. These are not verified against the papers — [HyperAI leaderboard](https://vercel.hyper.ai/cn/sota/tasks/highlight-detection/benchmark/highlight-detection-on-qvhighlights)
- QVHighlights annotations are **CC BY-NC-SA 4.0**, "for research purposes only". The moment_detr code is MIT. QD-DETR code is MIT but reuses Moment-DETR annotation files — [QVHighlights paper](https://arxiv.org/pdf/2107.09609); [QD-DETR repo](https://github.com/wjun0830/qd-detr)

**Training-free / zero-shot and LLM-based highlight methods (2025–2026)**
- REZE (arXiv 2608.04480) is training-free. It scores short clips against a query with a VLM, then aggregates the scores outside the model. Changing only the aggregation rule switches it between single-interval retrieval, multi-interval retrieval and highlight detection. It claims a new training-free SOTA on QVHighlights — [REZE](https://arxiv.org/pdf/2608.04480)
- TF-SELECTOR (SVHighlights, KDD 2026) is training-free for hour-long sports video. An LLM scores segments from captions, transcripts and **audio volume**. The authors say it beats VTG-tuned baselines on most metrics, and note that LLM methods "necessitate strict constraints on the number of input frames" — [SVHighlights](https://www.alphaxiv.org/abs/2606.06926)
- Zero-shot highlight detection without annotations: an LLM writes text descriptions of likely highlight events, a diffusion model renders them as images, and CLIP matches the images to frames. Evaluated on TVSum/SumMe — [Byra et al., arXiv 2609.14790](https://arxiv.org/pdf/2609.14790)
- One LLM-based HD model (Dec 2025) reaches 56.74 HIT@1 on QVHighlights from only 25 frames, which is below the DETRs above. It is trained, not training-free — [arXiv 2512.12246](https://papers.cool/arxiv/2512.12246)

**Unsupervised / personal / egocentric highlights**
- Unsupervised AV highlight detection (2024): cluster videos into pseudo-categories, score each clip by how much its audio and its visual features **recur** across other videos in the cluster, fuse the two into pseudo ground truth, then train a detector. It needs no labels. The abstract claims gains over prior work on three benchmarks, but gives no numbers in the abstract — [arXiv 2407.13933](https://arxiv.org/abs/2407.13933)
- Videogenic (Creativity & Cognition 2024) uses professional photographs of a domain as a prior for "highlight-worthy" frames. Its authors note growing egocentric highlight work "possibly due to a rise in popularity of personal action cameras" — [Videogenic](https://arxiv.org/html/2211.12493v2)
- Personal highlights remain hard (classic, 2021). HighlightMe gets mAP 0.64 on domain-specific DSH but only **0.20** on the personal-highlight dataset PHD2 — [HighlightMe](https://arxiv.org/html/2110.01774v2)
- GoPro Hero5+ embeds GPMF telemetry: **~400 Hz 3-axis gyro, ~200 Hz accelerometer**, plus GPS. `ffmpeg -map 0:3` extracts the gpmd stream (big-endian KLV). The Python `gpmf` package parses it, though the author says the GPS parsing is better tested than the rest. The Node `gopro-telemetry` is the mature parser — [Trekview](https://www.trekview.org/blog/gopro-telemetry-exporter-getting-started/); [gpmf on PyPI](https://pypi.org/project/gpmf); [gopro-telemetry](https://npmjs.com/package/gopro-telemetry)

**Summarization benchmarks (TVSum/SumMe)**
- The field has moved from F1 to rank correlation (Kendall τ, Spearman ρ) because F1 depends on how segments are split and favours short shots. Otani et al. are cited for this argument — [HierSum 2025](https://arxiv.org/pdf/2504.18689); [VideoSAGE 2024](https://arxiv.org/pdf/2404.10539)
- One 2026 table (search summary could not tell whether it is arXiv 2601.12243 or 2605.09507) gives human agreement τ/ρ 0.177/0.204 on TVSum and 0.205/0.213 on SumMe. On TVSum it lists CSTA 0.168/0.221, SummDiff 0.173/0.226 and VASTSum 0.179/0.270 (the authors' own model). Models are therefore at or above inter-human agreement on TVSum — [arXiv 2605.09507](https://arxiv.org/pdf/2605.09507); [arXiv 2601.12243](https://arxiv.org/pdf/2601.12243)
- VideoSAGE (2024) reports TVSum τ/ρ 0.30/0.42 on **new non-overlapping splits**, so its numbers are not comparable with the table above — [VideoSAGE](https://arxiv.org/pdf/2404.10539)

### Inferences
- **GPMF telemetry highlight channel (high value, low cost, no ML, no license issue).** For GoPro clips, compute: free-fall/airtime (|accel| near 0 g, then a landing spike), peak angular rate (flips, carves), and GPS speed peaks. Add these as a `telemetry_moment` term next to D1's `moment_curve`. This directly targets the D1 complaint ("not cutting at the right moment") with a sensor ground truth instead of pixel activity. Cost: about 1–2 days (KLV parser plus curves). Clips from non-GoPro cameras just skip it.
- **Recurrence pseudo-labels on the user's own library** (from arXiv 2407.13933). Within one project, clips whose SigLIP2 and PANNs embeddings recur across many other clips are "what this shoot is about". Rare one-off segments are either highlights or junk. The idea is complementary to B3 coverage: B3 rewards representing the footage; recurrence tells which parts are central. Cost: low, because the embeddings already exist. It needs a labelled check on `dev/montage_eval` (14 shots) before shipping.
- **Lighthouse CG-DETR saliency as an extra curve.** `pred_saliency_scores` with a generic query such as "exciting action moment" could be another input to window scoring. Blockers: the 150 s limit (chunk clips), unknown weight license, and NC QVHighlights training data. Keep it as an experiment only, unless the tool is strictly personal/non-commercial.
- REZE-style training-free scoring is essentially B2 (SigLIP2 scoring plus aggregation) already. The new idea worth taking is **changing the aggregation rule per task** (multi-interval for montages, single-interval for B-roll).
- TF-SELECTOR shows that cheap signals (transcript, **audio volume**) fed to an LLM work for long sports video. For event recaps, an audio-loudness/crowd-cheer curve (PANNs "cheering", "applause" already indexed in C2) is a nearly free highlight prior.
- For evaluation, report Kendall τ / Spearman ρ against hand labels rather than F1, and include an inter-annotator ceiling (see section 7).

### Gaps
- I found no 2023–2026 peer-reviewed paper or open model for action-camera / GoPro highlight detection with telemetry. The telemetry idea is my inference, not a cited result.
- The licenses of the CG-DETR, UniVTG and FlashVTG repos and their weights were not confirmed (search did not return the repos).
- Runtime of the Lighthouse models on an RTX 3070 was not reported; the paper used an A100.
- Numbers for the unsupervised recurrence method (2407.13933) were not retrieved.

---

## 2. Shot boundaries in long takes and phone clips, camera-motion classification, blur/shake, and video quality/aesthetics for shot choice

### Takeaway
TransNetV2 (2020, classic) is still the reference for shot boundaries. AutoShot (CVPRW 2023) is about +4 F1 better on short-form video, and PERSIST (Aug 2026 preprint) roughly halves false positives from flashes and text overlays. For choosing *good* shots, the commercially safe option is Google **UVQ 1.5 (Apache-2.0, per-frame scores, CPU)**. **DOVER and FAST-VQA-family weights are S-Lab non-commercial.** Quality MOS correlates only weakly with engagement, so use it as a gate (drop the worst), not as the ranking.

### Cited Findings
**Shot boundary detection**
- TransNetV2 authors' F1: ClipShots 77.9, BBC Planet Earth 96.2, RAI 93.9 — [TransNetV2 paper](https://arxiv.org/pdf/2008.04838)
- An independent re-evaluation (NVIDIA Cosmos "ShotBench", threshold 0.4 for both TransNetV2 and AutoShot) gives TransNetV2 BBC 0.967, RAI 0.919, ClipShots 0.726. Labelling conventions (transition = midpoint) and thresholds move F1 noticeably — [Cosmos paper](https://arxiv.org/pdf/2501.03575)
- AutoShot (CVPRW 2023, NAS-derived) is +4.2% F1 over TransNetV2 on its new short-video SHOT dataset, and +1.1/+0.9/+1.2 over previous SOTA on ClipShots/BBC/RAI — [AutoShot CVPRW](https://openaccess.thecvf.com/content/CVPR2023W/NAS/html/Zhu_AutoShot_A_Short_Video_Dataset_and_State-of-the-Art_Shot_Boundary_Detection_CVPRW_2023_paper.html)
- PERSIST (arXiv Aug 2026) counts a frame as a boundary only if the local change produces a *persistent* update of a latent temporal state (local change + transient impulse + return-to-trend cues). At matched recall vs TransNetV2 it roughly halves pseudo-event false positives on a 2,727-video diagnostic set and cuts ClipShots false positives by about 25%. It removes 33–80% of flash, text-overlay and archival false positives vs an identically trained cue detector. Not independently reproduced — [PERSIST](https://arxiv.org/pdf/2608.29287)

**Camera motion**
- CameraBench (NeurIPS 2025): about 3,000 internet videos, expert-labelled with a camera-motion taxonomy designed with cinematographers. SfM methods fail on *semantic* primitives such as "follow/tracking". VLMs fail on *geometric* primitives that need precise trajectories. Novices confuse zoom-in (intrinsics) with dolly-forward (extrinsics) — [CameraBench](https://arxiv.org/abs/2504.15376)
- SEA-RAFT (ECCV 2024, best-paper candidate) optical flow is BSD-3-Clause per aggregators — [ECCV page](https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/1065_ECCV_2024_paper.php); [license aggregator](https://oosmetrics.com/repo/princeton-vl/SEA-RAFT)
- CoTracker3 point tracking is **CC-BY-NC 4.0 (NC)** — [HF Space metadata](https://huggingface.co/spaces/facebook/cotracker/discussions/6/files)
- GoPro gyro at ~400 Hz is available in GPMF (see section 1) — [Trekview](https://www.trekview.org/blog/gopro-telemetry-exporter-getting-started/)

**Quality / aesthetics models**
- DOVER (ICCV 2023) outputs separate *aesthetic* and *technical* scores. PLCC: KoNViD-1k 0.883, LIVE-VQC 0.854, LSVQ_test 0.889, LSVQ_1080p 0.830, 3.6 s/video on CPU. DOVER-Mobile (ConvNeXt-V2-femto): 9.86 M params, 5.4× fewer GFLOPs, **<1.9 GB inference memory**, 1.4 s/video on CPU, PLCC 0.853/0.835/0.867/0.802. Has an ONNX export script — [DOVER GitHub](https://github.com/VQAssessment/DOVER)
- DOVER's S-Lab license allows redistribution and use only "for non-commercial purpose"; commercial use requires contacting the authors (**NC**) — [S-Lab-LICENSE](https://raw.githubusercontent.com/VQAssessment/DOVER/master/S-Lab-LICENSE)
- Q-Align / OneAlign is an LMM (adapted from mPLUG-Owl2) that unifies IQA, IAA and VQA. The repo ships `LICENSE` and `S-Lab-LICENSE` files (likely the same NC restriction; not confirmed). LoRA fine-tuning needs 2× RTX 3090. A successor, Q-ReAlign, claims the same performance with 50% fewer parameters — [Q-Align GitHub](https://github.com/Q-Future/Q-Align)
- A third-party mirror of the OneAlign tables lists KoNViD-1k SRCC/PLCC: Q-Align (VQA) 0.865/0.877, FAST-VQA 0.859/0.855. Not verified against the paper — [PromptLayer one-align](https://www.promptlayer.com/models/one-align)
- Google **UVQ: Apache-2.0**, PyTorch. UVQ 1.5 is the default ("better generalizability, robustness, and efficiency") and can emit `per_frame_scores`. UVQ 1.0 outputs compression / content / distortion sub-scores. Speed: a 20 s 1080p clip at 1 fps took 13.8 s on 8 CPU cores. It has a `--transpose` flag for portrait video — [google/uvq](https://github.com/google/uvq); [Google Research blog](https://research.google/blog/uvq-measuring-youtubes-perceptual-video-quality/)
- On Snapchat Spotlight short videos, MOS from earlier VQA datasets does **not strongly correlate** with engagement — [SnapUGC paper](https://arxiv.org/html/2410.00289v1)
- Aesthetic Predictor V2.5 is SigLIP-based and scores 1–10 across wider domains. The LAION V2 predictor is a linear head on CLIP ViT-L/14. Licenses were not visible in search results — [aesthetic-predictor-v2-5](https://github.com/discus0434/aesthetic-predictor-v2-5); [LAION blog](https://laion.ai/blog/laion-aesthetics/)

### Inferences
- **Shot boundaries matter less than "sub-shot" segmentation here.** Phone and GoPro clips rarely contain hard cuts, but they contain camera-on/off, whip pans and flashes. Recommended: run TransNetV2 or AutoShot once at probe time for clips over 60 s (the cuts give D1/A1 hard walls a window must not straddle), and treat flash and overlay false positives with a PERSIST-style "return-to-trend" check. That check is cheap to approximate on the existing stability v7 / brightness v8 curves.
- **Camera-motion classes from telemetry plus homography.** The tool already has 4 Hz pan direction (A3). Adding a homography decomposition of the flow field (scale → zoom; translation → pan/tilt; rotation → roll) gives zoom vs pan vs static labels. GoPro gyro gives rotation directly, which separates "pan" from "subject moving across a static frame" — the semantic/geometric confusion CameraBench documents. Use SEA-RAFT (BSD) if a learned flow is needed; avoid CoTracker3 (NC).
- **Quality gate with UVQ 1.5 per-frame scores.** Add `uvq_curve` (1 fps) to the window scorer as a *penalty floor*: windows in the bottom decile are excluded, but there is no reward above median. This matches the SnapUGC finding that MOS is not engagement. Cost: about 1 day; CPU-only is fine during probe.
- DOVER's aesthetic/technical split is the best-documented signal, but it is **NC**. Use it only for internal offline benchmarking, and only if the user accepts the S-Lab terms.
- Blur and shake: B5 already uses the Laplacian. Extending the same per-frame Laplacian variance plus gyro jerk into a `shake_curve` for windows is cheap and arguably as useful as a deep VQA model for GoPro footage.

### Gaps
- The TransNetV2 and AutoShot repo licenses were not confirmed (TransNetV2 is commonly reported MIT, but I did not see the LICENSE file).
- I found no RTX 3070 runtimes for TransNetV2, AutoShot or UVQ GPU.
- CameraBench per-class pan/tilt/zoom accuracy numbers and its fine-tuned model license were not retrieved.
- The Aesthetic Predictor V2.5 and LAION predictor licenses are unverified.
- I found no study showing that VQA-based shot selection improves perceived edit quality.

---

## 3. Music-driven editing: beats/downbeats, structure and energy, cut-on-beat evidence, music–video matching, ducking, loudness

### Takeaway
**Beat This! (ISMIR 2024, MIT code and weights, CPU-capable, beats and downbeats without DBN)** is the practical upgrade for beat tracking, at about 89 F1 on GTZAN beats. **all-in-one (`allin1`, MIT)** adds section labels (intro/verse/chorus/bridge/inst/solo/outro) with beats and downbeats, but needs NATTEN built from source on Windows. For the mix: duck music so speech sits **≥10 LU above it** (Torcoli et al., JAES 2019), and normalize the master to about **−14 LUFS integrated, −1 dBTP** for YouTube. I found no controlled study comparing cut-on-beat with cut-on-downbeat.

### Cited Findings
**Beat / downbeat / structure**
- Beat This! (Foscarin, Schlüter, Widmer, ISMIR 2024): alternating conv/transformer, a shift-tolerant loss, **no DBN post-processing**. The authors claim it beats prior SOTA F1 but is worse on continuity metrics. It is weaker on hard and under-represented genres — [arXiv 2407.21658](https://arxiv.org/abs/2407.21658)
- Beat This! repo: **MIT** for code and weights. Models: `final` ~78 MB, `small` ~8.1 MB. GPU by default, CPU fallback, `--float16`, optional `--dbn` via madmom. Outputs beats and downbeats. Some training audio is copyrighted or restrictively CC-licensed, so users must assess the impact — [CPJKU/beat_this](https://github.com/CPJKU/beat_this)
- GTZAN beat/downbeat F1: Beat This 88.9/75.5, Beat Transformer 88.5/71.4, TCN 88.5/67.2. BeatFM (2025, music foundation model) 89.5/79.6 — [BeatFM](https://arxiv.org/pdf/2508.09790). Another source gives Beat This 89.1 ± 0.3 without DBN — [masked-diffusion beat tracking 2026](https://arxiv.org/pdf/2608.04624)
- A 2026 failure-mode analysis of SOTA trackers (Beat This, Beat Transformer, madmom TCN) on the hard SMC dataset compares native peak-picking with DBN — [SMC Blind Spot](https://arxiv.org/pdf/2605.12287)
- all-in-one (Kim & Nam 2023): one model for beats, downbeats, functional segmentation and labelling, on **Demucs-separated stems** with dilated neighbourhood attention. Claims SOTA on all four tasks on the Harmonix Set, with multi-task ablation gains — [arXiv 2307.16425](https://arxiv.org/abs/2307.16425)
- `allin1` repo: **MIT** (weights license not separately stated). Labels: start, end, intro, outro, break, bridge, inst, solo, verse, chorus. Speed: 10 songs (33 min) in 73 s on RTX 4090. NATTEN is required, and **on Windows must be built from source** — [mir-aidj/all-in-one](https://github.com/mir-aidj/all-in-one)

**Music–video matching (choosing or trimming the track)**
- MGSV-EC (ICCV 2025): **53k short videos ↔ 35k music *moments* from 4k tracks**. The task is retrieving the track *and* the moment within it that fits a video. MaDe is the baseline — [arXiv 2408.16990](https://arxiv.org/abs/2408.16990v3)
- VMCML (CVPRW 2024): shared video–music embedding with a CosFace loss. MSV dataset of 390 tracks × 150k videos where the music is not the original sound — [CVPRW 2024](https://openaccess.thecvf.com/content/CVPR2024W/MULA/html/Lee_VMCML_Video_and_Music_Matching_via_Cross-Modality_Lifting_CVPRW_2024_paper.html)
- MVBind (2024): self-supervised music recommendation for short videos — [arXiv 2405.09286](https://export.arxiv.org/pdf/2405.09286)
- VeM (arXiv 2511.09585): a frame-level **transition–beat aligner**, plus a dataset from e-commerce ads and video platforms with strict transition–beat synchronization — [VeM](https://arxiv.org/pdf/2511.09585v3)
- VMAs (WACV 2025): DISCO-MV dataset and an MV-Align metric, compared with MVPt on video-to-music retrieval — [VMAs supplemental](https://openaccess.thecvf.com/content/WACV2025/supplemental/Lin_VMAs_Video-to-Music_Generation_WACV_2025_supplemental.pdf)
- MVAA (2025) re-times video to the music by inserting beat-aligned keyframes and generating in-between frames with CogVideoX-5B-I2V, adapting in about 10 min per video on an RTX 4090 — [arXiv 2506.18881](https://arxiv.org/abs/2506.18881)

**Cut-on-beat perception evidence (sparse)**
- People judge dance–music synchrony when extremities reach an endpoint or lowest point *on* the beat. Movement onsets never matched beats, and observers rely on predicting the motion — [Takehana et al. 2019, PLOS One](https://pmc.ncbi.nlm.nih.gov/articles/PMC6711538/)
- Classic (Dixon & Spitz 1980, cited in the same source): asynchrony goes undetected from about **75 ms audio-lead to 188 ms audio-lag**, and action is more sensitive than speech — [PMC6711538](https://pmc.ncbi.nlm.nih.gov/articles/PMC6711538/)

**Ducking and loudness**
- Torcoli et al., JAES 67(12) 2019, 22 listeners: recommended loudness difference (LD) **≥10 LU commentary-over-music** and **≥15 LU over ambience**. Non-experts preferred LDs **4 LU higher** than experts, with large individual variation. A BiDWGP intelligibility model predicted the median preferred LD with MAE 1.97 LU — [FAU CRIS](https://cris.fau.de/publications/234640600)
- Downward dynamic range expansion (DDRE) of the background improves speech intelligibility significantly while keeping the background's narrative intent, unlike linear ducking — [Salford data record](https://salford-repository.worktribe.com/output/3626626/downward-dynamic-range-expansion-versus-ducking-test-of-speech-intelligibility-data)
- YouTube moved its playback reference to **−14 LUFS** (2019, observed, not officially documented). YouTube only turns loud content *down* and never boosts quiet content. "Content loudness" in Stats for Nerds shows the offset — [Production Advice](https://productionadvice.co.uk/stats-for-nerds/); [MeterPlugs](https://meterplugs.com/blog/2019/09/18/youtube-changes-loudness-reference-to-14-lufs.html)
- A −1 dBTP true-peak ceiling is a common rule of thumb alongside −14 LUFS — [Youlean](https://youlean.co/?p=30813)
- `pyloudnorm` (MIT) implements ITU-R BS.1770-4 with a normalization helper. ffmpeg `loudnorm` does EBU R128 two-pass (measure, then apply with `linear=true` to keep dynamics). `ffmpeg-normalize` wraps both passes. pyloudnorm and ffmpeg readings can differ slightly — [pyloudnorm](https://pypi.org/project/pyloudnorm/); [loudnorm guide](https://dev.to/javidjamae/ffmpeg-loudnorm-filter-ebu-r128-loudness-normalization-guide-15d4); [ffmpeg-normalize](https://pypi.org/project/ffmpeg-normalize/)

### Inferences
- **Swap in Beat This! for beats *and* downbeats** (if the current tracker is librosa or madmom-DBN). Use the `small` model on CPU during probe. Downbeats make a hierarchy possible: section changes and big moves on downbeats (bar 1), ordinary cuts on beats, and 1/f variation inside phrases (A5 shows the current plans already get 1/f from section structure). Risk: no DBN means worse continuity on rubato or live music. Keep `--dbn` as a fallback when the inter-beat-interval CV is high. Cost: about 1 day.
- **Section-aware pacing with `allin1`** (MIT): chorus/drop gets a shorter beats-per-shot and higher-activity windows; intro/outro/break get longer, steadier shots. It provides the Main/Drop/Break/Outro labels that D2 already uses, but learned instead of heuristic. Cost: about 2–3 days, mostly the NATTEN Windows build. A fallback is a Foote novelty segmentation on Beat This downbeats (classic, no new dependency).
- **Cut timing inside the tolerance window.** The asymmetric audiovisual tolerance (audio lagging video is tolerated much longer than audio leading) suggests snapping a cut to the frame *at or just before* the beat, not after. This contradicts common vendor advice; it is untested for cuts and needs an A/B with users. Separately, the dance finding argues for aligning the *end point* of a motion (landing, peak of a jump) to the beat. A2 visual beats already targets motion peaks, so this mainly checks which phase of the motion A2 aligns.
- **Music moment selection (MGSV-EC idea).** When a montage is shorter than the song, choose the start offset that maximizes fit (energy curve vs planned activity curve, chorus inside the montage) instead of always starting at 0. This can be done without a model using `allin1` sections plus RMS energy.
- **Auto-ducking rule**: compute short-term loudness of dialogue and music. Under speech, set the music gain so that LD ≥ 10 LU (offer 14 LU as a "clear speech" preset for non-expert viewers, per the +4 LU finding). Use 150–300 ms attack/release around Silero/WeSpeaker speech regions. Consider DDRE instead of flat ducking for ambience beds. Final pass: two-pass loudnorm to −14 LUFS integrated, −1 dBTP, `linear=true`. Cost: about 1–2 days. These are Premiere keyframes or a rendered mix; the integration route is out of scope here.
- MVAA-style re-timing by diffusion is not feasible on 8 GB.

### Gaps
- I found no controlled study comparing cuts on beats vs downbeats vs off-beat for viewer preference or retention. The Dixon & Spitz window comes from a hammer action, not editing cuts.
- Weights licenses for `allin1`, BeatFM, MGSV-EC/MaDe and VMCML were not confirmed.
- "V2M" and "AutoMatch" datasets named in the brief could not be identified as specific music-to-video *editing* benchmarks. V2M-Zero (arXiv 2603.11042) is a generation paper.
- Beat This! RTX 3070/CPU runtime was not published.

---

## 4. Talking-head editing: jump-cut smoothing, punch-in, retakes, disfluencies beyond silence, speech enhancement, caption emphasis

### Takeaway
Research-grade jump-cut *synthesis* exists (Wang & Park 2024), but it is heavy and has no confirmed code. Alternating punch-in remains the practical fix. On disfluencies, a 2024 paper validates the A4 "gap in forced alignment = disfluency" idea: Whisper misses about 44% of word-level disfluencies. Repetitions and false starts remain the hard, unsolved class. For audio cleanup, **DeepFilterNet3 (MIT/Apache, RTF 0.19 on one CPU thread)**, **HTDemucs (MIT)** and **Resemble Enhance (MIT, per secondary sources)** are all permissive. Word-emphasis detection for captions has to rely on energy and duration more than pitch in Vietnamese, because pitch carries lexical tone (my inference).

### Cited Findings
- Jump Cut Smoothing for Talking Heads (Wang & Park, arXiv Jan 2024): interpolates DensePose keypoints and face landmarks across the cut, synthesizes pixels with an image-translation network, and uses cross-modal attention to choose a source per keypoint. Claims stronger results than a strong video-interpolation baseline, including large head rotation. Code release is not stated. Paper is CC BY-NC-SA — [arXiv 2401.04718](https://arxiv.org/abs/2401.04718)
- Inference-only disfluency detection for any ASR (2024): a modified CTC forced alignment gives word timestamps, and *gaps* are classified as disfluencies. **Whisper captures only ~56% of disfluencies** at the word level on conversational data. Code is on GitHub — [arXiv 2409.10177](https://arxiv.org/html/2409.10177v2)
- FluencyBank Timestamped (JSLHR 2024): timestamped labels for filled pauses, repetitions, revisions and partial words. Whisper/BERT performance **drops sharply on repetitions** — [ASHA JSLHR](https://pubs.asha.org/doi/10.1044/2024_JSLHR-24-00070)
- Token-based dysfluency detection as Whisper-style seq2seq (2024), phoneme-oriented, with simulators and a benchmark released — [arXiv 2409.13582](https://arxiv.org/abs/2409.13582v1); hierarchical word-level dysfluency modelling (template matching) — [arXiv 2401.10015](https://arxiv.org/pdf/2401.10015)
- Classic: prosody-only decision trees (Shriberg et al. 1997) detect false starts; adding a language model helps — [SRI](https://www.sri.com/publication/speech-natural-language-pubs/a-prosody-only-decision-tree-model-for-disfluency-detection/)
- DeepFilterNet3 (Interspeech 2023): matches SOTA enhancement benchmarks at **RTF 0.19 on a single-threaded notebook CPU**. 48 kHz, 10 ms frames, about 40 ms total latency in streaming ports. Dual **Apache-2.0 / MIT**. Needs the full DSP pipeline (STFT, normalization), not only the network — [ISCA archive](https://www.isca-archive.org/interspeech_2023/schroter23b_interspeech.html); [arXiv 2305.08227](https://arxiv.org/pdf/2305.08227); [CoreML port license note](https://huggingface.co/iky1e/DeepFilterNet3-Streaming-CoreML)
- Resemble Enhance has a denoiser plus an enhancer (restores distortions, extends bandwidth). It is listed as **MIT** on HF and third-party pages; the official LICENSE was not seen — [HF ResembleAI](https://huggingface.co/ResembleAI/resemble-enhance); [PromptLayer](https://promptlayer.com/models/resemble-enhance)
- HTDemucs: **MIT** for code and weights. Paper SDR 9.00 dB on MUSDB-HQ; per-source fine-tuned `htdemucs_ft` about 9.20 dB — [HTDemucs-ft model card](https://huggingface.co/StemSplitio/htdemucs-ft-pytorch)
- Word-level prominence: wav2vec2 fine-tuned for conversational Austrian German reaches **85.53%** prominence accuracy when the word sequence is recognized correctly, without hurting ASR — [arXiv 2509.10116](https://web3.arxiv.org/abs/2509.10116). FastSpeech2 duration/energy/pitch embeddings for prominence (Dec 2024, preliminary) — [arXiv 2412.08283](https://arxiv.org/abs/2412.08283v1). Classic emphatic-word detection from acoustic and lexical features: 87.8% — [Brenier, Cer, Jurafsky](https://web.stanford.edu/~jurafsky/p1034.pdf). Speech LMs tested on EmphAssess (same sentence, emphasis moved) — [ProsodyLM](https://arxiv.org/pdf/2507.20091)

### Inferences
- **A4 is in line with current research.** The 2024 inference-only paper does the same "alignment gap" trick. The next step is **repetition / false-start / restart detection**: n-gram and phonetic overlap between consecutive phrases in the wav2vec2-aligned transcript (e.g., "mình đi… mình đi tới"), with a prosodic cue (pitch reset, cut-off word, short gap). Use FluencyBank Timestamped (English) to sanity-check the method and a small Vietnamese held-out set for acceptance, as A4 did.
- **Retake detection (choose the best take).** No academic benchmark was found. Proposed design: segment the transcript into sentences, cluster near-duplicate sentences inside a sliding window (character n-gram plus multilingual sentence-embedding similarity ≥ threshold), then score each take by: disfluency count (A4 plus repetitions), wav2vec2 alignment confidence, speech rate stability, DeepFilterNet SNR estimate, and face-on-camera/eye-contact if a face detector is present. Default to keeping the **last complete** take; creators usually restart until satisfied, which is a prior to verify on user data. Hook into B1 so user overrides train the take-selection weights. Cost: about 3–5 days.
- **Jump-cut smoothing**: the Wang & Park synthesis approach is NC-licensed on paper, with no code and heavy models, so it is not recommended. Use the cheap, proven alternative: alternate scale (100% ↔ 110–115%) and slight re-centring on the face at each jump cut. C1 already proposes a 112% punch-in from retention; this generalizes it to every jump cut. Also avoid cuts where head-pose delta across the cut is small but non-zero; those read as glitches. Gate punch-in by face size so faces that are already close-up are not cropped.
- **Speech cleanup chain**: (1) if the source has background music, run HTDemucs to split vocals; (2) DeepFilterNet3 denoise on CPU, faster than real time; (3) optional Resemble Enhance for phone-mic bandwidth extension (GPU, slower, can hallucinate, so make it opt-in); (4) loudness pass (section 3). All are permissive licenses.
- **Caption emphasis for Vietnamese**: Vietnamese is tonal, so F0 movement is mostly lexical. Base the emphasis score on **relative energy plus lengthened duration plus a preceding pause** (all available from the wav2vec2 alignment), plus lexical cues (numbers, negations, named entities, superlatives). Use pitch only as a residual after a per-tone normalisation. This is my inference; validate against hand-labelled emphasis on 50–100 sentences.

### Gaps
- I found no peer-reviewed study on retake detection or "best take" selection, and no public dataset.
- I found no empirical study on whether word-level caption emphasis improves retention or comprehension.
- Jump Cut Smoothing code and weights availability is unknown.
- I found no Vietnamese disfluency or prominence dataset.
- Resemble Enhance and DeepFilterNet LICENSE files were confirmed only via secondary sources.

---

## 5. Automatic reframing 16:9 → 9:16, and B-roll matching from script text

### Takeaway
The AutoFlip recipe (2020, classic) is still the template: per-shot buffering, weighted salient detections, a per-shot choice of **stationary / panning / tracking** mode, then a smooth polynomial camera path, with a blurred-pad fallback. The main 2024–2026 change is the **license landscape**: Ultralytics YOLO is AGPL-3.0, even for models you train yourself. Use Apache/MIT detectors and trackers instead (RTMDet, RF-DETR, YOLOX + ByteTrack). For B-roll, the main academic reference is still B-Script (CHI 2019); I found no 2023–2026 peer-reviewed B-roll *placement* paper.

### Cited Findings
- AutoFlip pipeline: colour-histogram shot detection, then buffering the whole scene before deciding. Face and object detectors run on CPU (TFLite), each with a weight. Mode is chosen per shot: **stationary** (fixed viewport), **panning** (constant velocity) or **tracking** (follows objects). The camera path is a smooth low-degree polynomial fitted to the boxes by Euclidean-norm optimisation. Falls back to letterbox with solid or **blurred-frame padding** when required regions don't fit. Known limitation: overlays (text/logos) at the edges get cropped. Planned: speaker detection — [Google Research blog](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/)
- Ultralytics licensing: AGPL-3.0 or a paid Enterprise license. The AGPL applies to code, models, architectures and "trained/fine-tuned models … even if you train your own model from scratch" (**copyleft flag**) — [Ultralytics license](https://ultralytics.com/license)
- ByteTrack is MIT and detector-agnostic — [ByteTrack](https://arxiv.org/abs/2110.06864v2). Roboflow `trackers` packages SORT / ByteTrack / OC-SORT under Apache-2.0 — [trackers](https://trackers.roboflow.com/latest)
- RTMDet (MMDetection) is Apache-2.0, 52.8% COCO AP at over 300 FPS on an RTX 3090 (2023 source) — [Roboflow RTMDet](https://roboflow.com/blog/how-to-train-rtmdet-on-a-custom-dataset). RF-DETR, YOLOX, Detectron2 and MMDetection are listed as Apache-2.0 in a 2026 comparison; check the license of the specific RF-DETR variant — [Lightly 2026](https://lightly.ai/blog/best-ultralytics-alternatives-in-2026)
- B-Script (CHI 2019, classic): transcript-based B-roll editor that recommends **positions and keyphrases**, trained on data from 115 expert editors. In a 110-participant study, users were faster with the transcript UI and made **more engaging videos when recommendations were shown** — [arXiv 1902.11216](https://arxiv.org/abs/1902.11216); [Adobe Research](https://research.adobe.com/publication/b-script-transcript-based-b-roll-video-editing-with-recommendations/)

### Inferences
- **Reframing implementation (about 4–6 days):** per shot, collect weighted boxes from faces (any Apache face detector), persons (RTMDet or YOLOX), and the **active speaker** (C3 WeSpeaker label → which face's mouth region has motion energy). Decide the mode from box-centre variance (stationary if the crop window covers about 90% of centres; panning if the centre trend is linear; otherwise tracking). Fit a smoothed path (polynomial or L1 trend filtering), with acceleration limits so the crop does not jitter. Fall back to a blurred pad when two speakers are more than one crop-width apart, or split-screen them. Reuse A3 pan data: if the original camera already pans, prefer the "stationary relative to camera" crop to avoid double motion.
- **Protect overlays**: run a text-region detector (or OCR boxes) and either include burned-in captions in the crop or drop them and re-caption. This is the known AutoFlip failure mode.
- Avoid Ultralytics weights in a distributed tool unless the user accepts AGPL. RTMDet-tiny or YOLOX-s with ByteTrack fit easily in 8 GB.
- **B-roll placement (beyond retrieval)**: B-Script's main lesson is *where* to place B-roll, not only *what*. Candidate positions: noun-phrase or entity mentions, the start of list items, numbers, and long talking stretches (C1 already says b-roll ≤ 4 s). Train placement weights from the user's keep/discard actions (B1 mechanism). For retrieval quality, apply the per-task aggregation idea from REZE (section 1) to score the *best sub-window* of a stock clip, which D1's `_steadier` partly does.

### Gaps
- I found no 2023–2026 peer-reviewed reframing or retargeting paper with metrics. Academic work may sit under "video retargeting" or "saliency-aware cropping", which I did not cover.
- I found no 2023–2026 peer-reviewed B-roll placement or retrieval paper (search returned only B-Script and commercial pages).
- The MediaPipe/AutoFlip license was not stated on the blog page (MediaPipe is generally Apache-2.0; unverified here).
- I found no quantitative comparison of reframing quality metrics such as subject-in-frame rate or path jerk.

---

## 6. Narrative and structure: hooks, pacing from retention, ordering clips into a story arc, transition selection

### Takeaway
Evidence here is thin. There is no peer-reviewed study of "hook" design for the first seconds. Engagement prediction (SnapUGC / VQualA 2025) shows that **audio matters** and that quality MOS ≠ engagement. Shot ordering has a benchmark (AVE-Order) where the best model (35.3% top-1 on 3-shot permutations) is still below humans (39.9%). **Shot-type labels (size, angle, motion) help ordering more than genre.** Transition recommendation has AutoTransition (ECCV 2022) and Edit3K (2024) as references.

### Cited Findings
- No peer-reviewed study of opening-second hooks was found. A single-channel analytics example shows retention falling from about 95% to about 65% by 30 s, then flattening — [vidIQ](https://vidiq.com/blog/post/increase-audience-retention-youtube/)
- VQualA 2025 (ICCV workshop) EVQA-SnapUGC: engagement prediction of Snapchat Spotlight short videos from visual, audio and creator metadata. 97 participants, 15 valid final submissions — [ICCVW 2025 report](https://openaccess.thecvf.com/content/ICCV2025W/VQualA/html/Li_VQualA_2025_Challenge_on_Engagement_Prediction_for_Short_Videos_Methods_ICCVW_2025_paper.html). The winner, an ensemble of 3 VideoLLaMA2 models and 1 Qwen2.5-VL, scored 0.714. VideoLLaMA2 consistently beat Qwen2.5-VL, which the authors attribute to **audio features** — [arXiv 2508.02516](https://arxiv.org/pdf/2508.02516)
- AVE (ECCV 2022, classic): 196,176 annotated shots from 5,591 movie scenes, with benchmarks for organizing footage and learning editing patterns (e.g., next-shot selection with 4 context shots and 5 candidates, scored by accuracy) — [AVE](https://arxiv.org/pdf/2207.09812); [protocol as used by arXiv 2505.12237](https://arxiv.org/pdf/2505.12237)
- Shot Sequence Ordering (2025): AVE-Order (5,537 scenes, 63,185 3-shot sequences) and ActivityNet-Order (4,052 scenes, 23,673 sequences). Kendall-tau distance is proposed as the main metric. Top-1 on AVE-Order: random 16.6, **human 39.9**, MViT 32.04, theirs 35.28 (KT distance 1.251). On ActivityNet-Order: 45.56 → 51.41. Shot labels (size, angle, motion, type) as tokens contribute more than genre. Code: ShotSeqBench (license not stated) — [arXiv 2503.17975](https://arxiv.org/html/2503.17975v2)
- AutoTransition (ECCV 2022): transition recommendation for each adjacent shot pair from video plus audio, using a multimodal transformer over learned transition embeddings. A user study found it comparable to professional editors while making editing about **300× faster**. The dataset was built from editing-software templates — [arXiv 2207.13479](https://arxiv.org/abs/2207.13479)
- Edit3K (2024): universal embeddings of editing components (transitions, effects, …). It notes that AutoTransition uses only 30 of its 104 transitions. Edit3K embeddings give SOTA on AutoTransition's recommendation task — [arXiv 2403.16048](https://arxiv.org/html/2403.16048v1)

### Inferences
- **Hook builder (needs a measured test)**: for vlogs and recaps, generate a 3–6 s cold open from the top-k highlight windows (section 1 signals plus C2 audio peaks), placed before the title. Validate with C1 retention: compare drop-off in the first 30 s for videos with and without a cold open. Lacking literature, the tool's own retention data (C1) is the only credible evaluator. Make it opt-in and log outcomes.
- **Engagement ≠ quality**: weight audio-driven signals (laughter, cheers, music drops, speech energy) above pixel quality when ranking moments for hooks. The SnapUGC winner's audio advantage supports this.
- **Story ordering for event recaps**: default to capture time (EXIF/GPMF timestamps), with B3 coverage. Within a scene, enforce shot-size progression (wide → medium → close, or establishing shot first), since shot-type labels are the strongest ordering cue in SSO. Shot size can be estimated from face/person box area relative to the frame. Cost: about 1–2 days, using the detectors from section 5.
- **Transitions**: given AutoTransition's finding that learned recommendations reach editor-level choices, but that only about 30 transition classes matter, the low-cost version is a small rule set keyed on (pan direction continuity from A3, beat strength/downbeat, section boundary from `allin1`). Hard cut by default; whip/zoom on downbeats when both shots move in the same direction; dissolve only at section changes or time jumps. Training an AutoTransition model would need Edit3K data; check its license first.

### Gaps
- I found no peer-reviewed evidence on optimal hook length or content for YouTube long-form or Shorts.
- I found no open model for retention-curve prediction from content (C1 uses the user's own analytics).
- Licenses for the AutoTransition, Edit3K, AVE and ShotSeqBench datasets were not confirmed.
- "From Shots to Stories" (arXiv 2505.12237, LLM-assisted editing) was seen only by title and protocol mention; its results were not retrieved.

---

## 7. How the tool should evaluate itself to avoid regressions

### Takeaway
Combine (a) **per-component benchmarks with public metrics** (beat/downbeat F1 on a few owned tracks, SBD F1 at a fixed threshold, Kendall τ / Spearman ρ of highlight curves against hand labels with an inter-annotator ceiling, loudness and LD compliance) with (b) **pairwise human preference on whole edits** (Bradley–Terry, which B1 already implements), and (c) **real retention (C1)**. Public datasets (AVE-Order, AutoTransition, Edit3K, MGSV-EC, SnapUGC, QVHighlights, TVSum/SumMe, FluencyBank Timestamped, CameraBench) suit sanity checks. Several are NC or research-only, so use them for evaluation only, never for training shipped weights.

### Cited Findings
- Summarization should use rank correlation against human scores, reporting the human τ/ρ ceiling (TVSum 0.177/0.204), because F1 depends on segmentation — [HierSum](https://arxiv.org/pdf/2504.18689); [arXiv 2605.09507](https://arxiv.org/pdf/2605.09507)
- SBD F1 depends on threshold calibration and labelling convention. TransNetV2 ClipShots F1 is 77.9 (authors) vs 72.6 (independent, threshold 0.4) — [Cosmos](https://arxiv.org/pdf/2501.03575); [PERSIST](https://arxiv.org/pdf/2608.29287)
- Shot ordering: Kendall-tau distance plus a human ceiling (AVE-Order human 39.9 top-1) — [SSO](https://arxiv.org/html/2503.17975v2)
- Transition recommendation evaluated by retrieval metrics and a user study vs professional editors — [AutoTransition](https://arxiv.org/abs/2207.13479)
- Engagement: SnapUGC provides real engagement labels; VQA MOS does not strongly correlate with them — [SnapUGC](https://arxiv.org/html/2410.00289v1)
- Disfluency: FluencyBank Timestamped provides word-level timestamps for filled pauses, repetitions, revisions and partial words — [JSLHR 2024](https://pubs.asha.org/doi/10.1044/2024_JSLHR-24-00070)
- Ducking acceptability can be predicted (BiDWGP, MAE 1.97 LU vs median preference); the target LD is ≥10 LU — [Torcoli et al.](https://cris.fau.de/publications/234640600)
- Camera motion: CameraBench, with expert labels and a documented novice confusion between zoom and dolly — [CameraBench](https://arxiv.org/abs/2504.15376)
- Music–video moment matching: MGSV-EC, 53k videos / 35k music moments — [arXiv 2408.16990](https://arxiv.org/abs/2408.16990v3)
- License flags for eval-only use: QVHighlights annotations CC BY-NC-SA research-only — [QVHighlights](https://arxiv.org/pdf/2107.09609); DOVER S-Lab NC — [S-Lab-LICENSE](https://raw.githubusercontent.com/VQAssessment/DOVER/master/S-Lab-LICENSE)

### Inferences
**Proposed regression suite, extending `scratchpad/eval_montage.py` and `dev/montage_eval/`**

| Component | Metric | Data | Pass rule |
|---|---|---|---|
| Beat/downbeat | F1 (beats, downbeats) on 10 owned tracks hand-tapped once | owned music incl. INTRO 1 | no drop > 1 pt |
| Cut timing | median and 90th-percentile cut–beat offset (ms), share of cuts within −40…+20 ms | all montage plans | no widening |
| Section pacing | mean shot length per `allin1` section label | 41 plans (A5 set) | chorus < verse < intro |
| Moment choice | % of hand-labelled action retained (D1 metric), plus Kendall τ of the window score vs hand ranks | 14-shot set; grow to ≥ 50 | τ not lower; D1 errors ≤ 2/14 |
| Highlight curve | Spearman ρ vs 2 annotators; report the annotator–annotator ρ as ceiling | 20 GoPro clips | ρ ≥ previous |
| Shot quality gate | % of selected windows in bottom UVQ decile | 340-clip set | ≤ 2% |
| Shot boundaries | F1 at a fixed threshold on 30 clips with hand-marked cuts/flashes | phone + GoPro | FP/clip not up |
| Talking head | repetition/false-start recall and FP on Vietnamese held-out set | held-out recordings (like A4) | recall ↑, FP ≤ A4 |
| Retake choice | agreement with the user's chosen take | 20 multi-take recordings | ≥ 80% |
| Audio | integrated LUFS (−14 ± 1), true peak ≤ −1 dBTP, speech–music LD ≥ 10 LU for ≥ 95% of speech frames | every export | hard fail |
| Reframing | subject-in-safe-area rate, crop path jerk (px/frame²), number of mode switches per shot | 20 horizontal clips | rate ≥ 95%, jerk not up |
| Whole edit | blind pairwise A/B (old vs new build) on 5 projects × ≥ 5 raters, Bradley–Terry with CI | own footage | new ≥ old (CI) |
| Field | C1 retention: 30 s retention and drop events per minute | user channel | trend only |

- Follow the user's own rule for LLM/VLM-based components: **≥ 6 seeded runs, report mean and range** (memory `feedback_do_llm_nhieu_lan`). Apply it to any highlight or hook ranking that uses Qwen3-VL.
- Always report a human ceiling (inter-annotator agreement) next to model scores. Highlight/summarization literature shows models can exceed inter-human τ on TVSum, so a higher τ after that point is not a meaningful gain.

#### Cross-cutting priority shortlist (my ranking: value for this tool ÷ cost; all locally runnable on RTX 3070 8 GB or CPU)
1. **Loudness and ducking pass** — pyloudnorm/ffmpeg loudnorm (MIT/LGPL-ffmpeg), LD ≥ 10 LU rule. Cost about 1–2 days; ships an audible quality gain on every export.
2. **Beat This! beats plus downbeats** — MIT, CPU-capable small model; enables downbeat-level cut hierarchy. About 1 day.
3. **GoPro GPMF telemetry curves** (airtime/landing/rotation/speed and shake) — no ML, no license issue; aimed directly at the D1 "wrong moment" problem for action montages. About 1–2 days.
4. **DeepFilterNet3 (+ optional HTDemucs) speech cleanup** — MIT/Apache, faster than real time on CPU. About 1–2 days.
5. **Repetition / false-start / retake detection** extending A4 — no new model needed. About 3–5 days.
6. **UVQ 1.5 quality floor** — Apache-2.0, per-frame. About 1 day.
7. **Section-aware pacing with `allin1`** (MIT; NATTEN Windows build risk) or a novelty-segmentation fallback. About 2–3 days.
8. **AutoFlip-style 9:16 reframing** with Apache/MIT detector and tracker plus active-speaker selection from C3. About 4–6 days.
9. **Shot-size-aware ordering and rule-based transitions** — 1–2 days each.
10. **Experimental only (license-gated):** Lighthouse CG-DETR saliency (QVHighlights NC), DOVER aesthetic/technical split (S-Lab NC), CoTracker3 (NC).

### Gaps
- "V2M" and "AutoMatch" as named evaluation datasets could not be identified. MGSV-EC and VMCML/MSV are the closest music–video matching benchmarks found.
- I found no standard published metric for "cut-on-beat quality" in editing (as opposed to generation papers' beat-alignment scores). The ms-offset metric above is a proposal.
- I found no published human-preference protocol specific to automatic montage editing. The pairwise Bradley–Terry A/B design is my inference, based on the tool's existing B1 machinery.

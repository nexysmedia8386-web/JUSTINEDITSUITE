# Pipeline speed and resource use: compute and infrastructure for Justin EDIT Suite (Windows 11, RTX 3070 8 GB)

Scope: wall-clock time and VRAM/RAM of the local analysis → timeline pipeline. This file does not cover editing-quality algorithms, model choice for quality, Premiere integration or competitors.

**What the tool already does.** Checked in `engine/` and in the dev-machine memory notes, so these are not re-recommended:
- PyNvVideoCodec NVDEC decode in-process (`nxcore/ai/frames.py`).
- `-skip_frame nokey` keyframe grabs for contact sheets, SigLIP and segments.
- Stability with sparse KLT at 15 fps / 480 px, already hidden behind NVDEC decode (2.2 ms/frame).
- QC as one ffmpeg pass at 10 fps gray.
- faster-whisper `large-v3-turbo` with `BatchedInferencePipeline`, CUDA float16 → int8_float16 fallback, and the EraX turbo CT2 option.
- Ollama `keep_alive: 0` before Whisper, plus auto-reload when prompt eval drops below 300 tok/s.
- A long-lived AI worker that keeps SigLIP2 resident, with `free()` calling `torch.cuda.empty_cache()`.
- h264_nvenc proxies at preset p4.
- Caches keyed by size+mtime, and audio decode overlapped with GPU work.

**Local measurements in this file.** Rows marked **[Local test]** were measured in this research session (2026-10-11) on the dev machine itself:
- Hardware and software: RTX 3070 8 GB, driver 617.42, i7-13700K (24 threads), ffmpeg 9.0.1 (gyan.dev essentials build).
- Test clip: `G:/PQ/SOURCE/video Gopro/GX019681.MP4`, a GoPro 4K 59.94p HEVC Main 8-bit file at about 99.5 Mb/s, 20.1 s, 1,206 frames.
- Clip structure (from ffprobe): `has_b_frames=0`, and a keyframe every 1.001 s (GOP of 60 frames).
- Each timing is a single run with the file cached in RAM.

---

## 1. Fastest ways to decode and sample frames from 4K HEVC/H.264 in Python on Windows + NVIDIA

### Takeaway
On this card, 4K60 HEVC decode is capped by the NVDEC engine at about 390–420 fps. Sampling at 4 Hz, running more NVDEC sessions, or switching decode libraries cannot beat that for IPPP GoPro/DJI files. The real wins come from three places:
- Decoding each source **once** and sharing the frames: today QC and stability each do a full decode.
- Adding a **parallel CPU-decode lane**: measured +33% total throughput.
- Using **keyframe-only** sampling wherever 1 Hz is enough: measured about 7.6× cheaper than a full decode.

A further rule: keep frames on the GPU until after downscaling. Copying full 4K frames to RAM costs about 39%.

### Cited Findings

**Local tests on the GoPro clip**

All rows decode the same 20.1 s, 1,206-frame clip and scale the output to 640×360 unless noted.

| Variant | Time | Throughput |
|---|---|---|
| NVDEC, frames kept on GPU, `scale_cuda` | 3.01 s | ≈400 fps (≈6.7× realtime) |
| `hevc_cuvid -resize 640x360` (NVDEC built-in scaler) | 2.89 s | ≈418 fps |
| NVDEC with full 4K frames copied to RAM, then software `scale` | 4.91 s | ≈246 fps (−39% vs. GPU-resident frames) |
| CPU (libavcodec, 24 threads) decode + software scale | 6.75 s | ≈179 fps |

— [Local test]

- **`fps=4` before `scale_cuda` (frames on GPU): 2.95 s, the same as a full decode.** All P-frames must still be decoded, so frame-rate sampling saves nothing on IPPP streams. — [Local test]
- **Keyframes only (`-skip_frame nokey`, 21 frames):**
  - NVDEC with GPU frames: 0.40 s for 20.1 s of video (≈51× realtime, ≈7.6× cheaper than a full NVDEC decode).
  - CPU decode: 1.68 s.

  — [Local test]
- **Concurrency on three GoPro clips (3,685 frames):**

  | Setup | Time | Throughput | vs. sequential |
  |---|---|---|---|
  | 3 clips, NVDEC, sequential | 9.54 s | ≈386 fps | baseline |
  | 3 NVDEC sessions in parallel | 9.17 s | ≈402 fps | +4%: NVDEC saturated |
  | 2 NVDEC sessions + 1 CPU-decode process in parallel | 7.20 s | ≈512 fps | +33% |
  | 2 CPU decodes in parallel (2 clips, 2,429 frames) | 11.05 s | ≈220 fps | — |

  — [Local test]
- **Earlier measurement in the project memory.** Stability/QC analysis is NVDEC-bound at about 260 4K frames/s for DJI 10-bit and about 390 for GoPro 60p. DJI and GoPro HEVC are IPPP, so `-skip_frame noref` skips nothing. — (project memory note `reference_ollama_rtx3070.md`, measured earlier on this machine)
- **The two analysis lanes each decode the full clip.**
  - `nxcore/qc.py`: "One decode per clip (downscaled 10 fps gray)".
  - `nxcore/stability.py`: "decoded once at ≈15 fps … 480 px wide, grayscale, CUDA decode".

  `nxcore/analyze.py` runs "QC FFmpeg → stability/composition" in sequence, and the optional 360p proxy is a third decode. — (source code headers in `G:/JustinEditSuite/engine/nxcore/`)
- **NVIDIA's official Ampere NVDEC figure.** HEVC 1080p 4:2:0 runs at 1,419 fps (Main10: 1,323) on an RTX 3090 in SDK 12.1/12.2. SDK 13.x lists 1,415 / 1,299. Throughput "scales (almost) linearly with the clock speeds". 1080p ÷ 4 ≈ 350 fps at 4K, which matches the local ≈400 fps. — [NVDEC Application Note 12.2](https://docs.nvidia.com/video-technologies/video-codec-sdk/12.2/nvdec-application-note/index.html); [NVDEC Application Note 13.1](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvdec-application-note/index.html)
- **PyNvVideoCodec benchmarks.** Measured on an L40G (3 NVDECs), 1080p30 H.264:
  - **Sequential decode:** 886 fps on 1 thread, 2,615 fps on 3 threads.
  - **GOP size:** matters for random and uniform sampling. Random sampling runs at 37.3 fps with GOP 250 vs. 78.4 fps with GOP 30. Direct sampling beats decode-then-sample by only 1.02–1.05× at GOP 250, and by 1.42–2.14× at GOP 30.
  - **Decoder caching (reusing one decoder across clips):** 7.2× faster for 2 s clips at 360p, but only 1.2× at 4K for 2 s clips and 1.02× at 4K for 30 s clips.

  — [PyNvVideoCodec Performance](https://docs.nvidia.com/video-technologies/pynvvideocodec/pynvc-api-prog-guide/application-note.html)
- **PyNvVideoCodec 2.0 features.** It adds sequential, random, periodic, batched, sliced and target-frame-rate retrieval, decoder caching, threaded decoding, low-latency decode for streams without B-frames, SEI and keyframe-index extraction, and segment-based transcoding. It is "lightweight, MIT-licensed" and installs with pip. — [NVIDIA blog: What's new in PyNvVideoCodec 2.0](https://developer.nvidia.com/blog/whats-new-in-pynvvideocodec-2-0-for-python-gpu-accelerated-video-processing)
- **PyNvVideoCodec segmented transcoding.** Its transcoding of 1080p H.264 segments took 2.31 s (1,072 fps), against 6.36 s for FFmpeg without map: about 2.8×, because it keeps a persistent decoder and encoder context. — [PyNvVideoCodec Performance](https://docs.nvidia.com/video-technologies/pynvvideocodec/pynvc-api-prog-guide/application-note.html)
- **Built-in decoder scaling in ffmpeg's cuvid decoders.** They expose `-resize WxH` and `-crop TxBxLxR`, confirmed on the local ffmpeg 9.0.1 with `ffmpeg -h decoder=hevc_cuvid`. The NVDEC scaler does not let you choose the scaling algorithm. — [Local test]; [torchaudio NVDEC tutorial](https://docs.pytorch.org/audio/2.7.0/tutorials/nvdec_tutorial.html)
- **TorchCodec release history.** Release dates are shown without years; the order runs from about Dec 2025 to Sep 2026.
  - **v0.10:** decoder transforms `Resize`, `CenterCrop` and `RandomCrop`.
  - **v0.11:** `set_nvdec_cache_capacity()`; rotation metadata applied automatically on CPU and on the CUDA backend.
  - **v0.12:** the faster NVDEC-direct CUDA backend (formerly "beta") became the default. CUDA 12.8 dropped and CUDA 13.2 added; needs torch ≥ 2.11.
  - **v0.13:** Windows CUDA wheels shipped as **beta**.
  - **v0.17 (30 Sep):** HEVC 4:4:4 and 10-bit AV1 decode natively on the GPU; free-threaded CPython no longer re-enables the GIL on import.

  — [torchcodec releases](https://github.com/meta-pytorch/torchcodec/releases)
- **TorchCodec guidance on when CUDA decode helps.** It helps most for high-resolution video and large batches that saturate the CPU. It may not pay off for small videos where PCIe transfer latency dominates. — [TorchCodec CUDA example (docs)](https://docs.pytorch.org/torchcodec/0.4/generated_examples/basic_cuda_example.html)
- **GoPro LRV proxies.** No official GoPro spec for LRV resolution turned up. A third-party format site claims "240p … 29.97 fps", but this is unverified and conflicts with other community claims of 480p. The local GoPro folder has no LRV files, so they were likely discarded on import. — [fileformat.com LRV](https://docs.fileformat.com/video/lrv); [Trek View on LRV/THM](https://www.trekview.org/blog/gopro-what-are-lrv-thm-files)

### Inferences
- **Biggest win: decode once and fan out.** QC (10 fps gray), stability (15 fps 480 px gray) and the optional proxy are fed from one NVDEC decode per clip, either with an ffmpeg `split` graph or with PyNvVideoCodec delivering frames to both consumers in Python.
  - Scale: 2.3 h of 4K60 is ≈ 496k frames, which takes ≈ 21 min per full NVDEC pass at 400 fps. Removing one pass saves ≈ 20 min on an all-4K60 library; mixed footage saves less.
  - Verify: run `dev/panel_harness` and check that QC/stability outputs stay identical.
- **Add a CPU-decode lane.** Send about 1 of every 3 clips to libavcodec while NVDEC handles the other 2, but only while the CPU is not needed by KLT, Whisper preprocessing and similar work. Measured gain: +33%.
  - Schedule by measured throughput (NVDEC ≈ 400 fps vs. CPU ≈ 180–220 fps for 4K60 HEVC). Do not use a fixed ratio.
- **Fix the frame count before decoding.** Stages that only need about 1 Hz should use keyframe-only NVDEC; for GoPro the GOP is 1.001 s, so keyframes land at ≈1 Hz. Stages that need 4 Hz pay the full decode regardless of library, so share that decode (previous point).
- **Avoid the "plain `-hwaccel cuda`" fallback in hot paths.** It downloads full 4K frames and costs about 39%. If it is used as the default for some codecs, check which clips fall into it, e.g. HEVC 4:2:2 or 10-bit DJI.
- **TorchCodec is not worth switching to for decode speed.** The local NVDEC ceiling is hardware. Two of its features are attractive: automatic rotation handling, and decoder-side Resize/CenterCrop for SigLIP crops. Against that:
  - Its Windows CUDA wheels are still beta.
  - It requires torch ≥ 2.11 built for CUDA 13.x, while the tool ships PyTorch cu128.
  - Decord is not on the shortlist either: no release since 2021 (no source fetched this session; see Gaps).
- **Use LRV files where they exist.** For thumbnails, SigLIP frames and coarse motion curves, a low-res H.264 LRV would decode many times faster than 4K HEVC. It needs a timing-alignment check (frame rate and duration vs. the master) and is only usable when the user keeps the LRV files.

### Gaps
- No official GoPro LRV resolution or frame rate per model. Run ffprobe on an LRV from HERO 11/12/13 before building on it.
- Number of NVDEC/NVENC engines on GA104 (RTX 3070) not verified from NVIDIA's support matrix this session. The local concurrency test behaves like a single saturated decoder.
- No independent PyNvVideoCodec vs. TorchCodec vs. Decord benchmark on Windows/Ampere was found. Decord's maintenance status and license (Apache-2.0) and TorchCodec's license (BSD-style) were not re-verified this session.
- DJI 10-bit and phone H.264 clips were not timed in this session. Only GoPro 8-bit HEVC was timed.

---

## 2. GPU optical flow and motion estimation vs. CPU Farneback/DIS

### Takeaway
For this pipeline, motion estimation is **not** the bottleneck: KLT at 15 fps / 480 px costs 2.2 ms/frame and is fully hidden behind NVDEC. Moving to NVIDIA's hardware optical flow (NVOFA), RAFT/NeuFlow or motion-vector export would not reduce wall-clock time unless decode is also reduced. The useful facts:
- **NVOFA** is a separate hardware block that does not use CUDA cores (≈600 fps at 1080p, 4×4 grid, fast preset, on Ampere).
- **DIS on CPU** runs at hundreds of Hz at low resolution.
- **ffmpeg motion vectors are unavailable for HEVC.**

### Cited Findings
- **NVOFA throughput.** Indicative fps at 1080p (RTX 3090, Optical Flow SDK 5.0, Windows 11):

  | Grid | Slow | Medium | Fast |
  |---|---|---|---|
  | 4×4 | 200 | 405 | 613 |
  | 2×2 | 94 | 154 | 261 |
  | 1×1 | 29 | 45 | 85 |

  Ada (RTX 4090) is roughly 2.2–2.7× faster. The accelerator "works independently of graphics/NVIDIA CUDA cores" and is supported on "all Turing GPUs (except TU117) and above". — [NVIDIA Optical Flow SDK 5.0 application note](https://docs.nvidia.com/video-technologies/optical-flow-sdk/nvofa-application-note)
- **NVOFA quality.** Fl-all on KITTI 2015 (non-occluded, Ampere): 18.65% (4×4 slow) to 25.44% (4×4 fast). — [same](https://docs.nvidia.com/video-technologies/optical-flow-sdk/nvofa-application-note)
- **Older NVIDIA comparison (Turing era).** Hardware optical flow ≈ 2–3 ms/frame, against Farneback ≈ 8 ms (lower accuracy), Lucas–Kanade > 20 ms and TV-L1 > 300 ms. — [NVIDIA dev blog (older, Turing-era)](https://devblogs.nvidia.com/?p=16021)
- **OpenCV exposes NVOFA** as `cv::cuda::NvidiaOpticalFlow_2_0` with Slow/Medium/Fast presets. This needs an OpenCV build with CUDA contrib modules; Python binding availability was not confirmed. — [OpenCV 5.0 docs](https://docs.opencv.org/5.0/extra_modules/classcv_1_1cuda_1_1NvidiaOpticalFlow__2__0.html); [Emgu presets enum](https://emgu.com/wiki/files/4.5.3/document/html/1bc5d6d4-0e6b-55fb-d570-4f555970b100.htm)
- **DIS (Kroeger et al., ECCV 2016).** 300–600 Hz on one CPU core at 1024×436 for the core algorithm, or 42/46 Hz including preprocessing (disk I/O, rescaling, gradients). Available as OpenCV `DISOpticalFlow` with 3 presets. — [arXiv 1603.03590](https://arxiv.org/abs/1603.03590); [ECCV listing](https://mlanthology.org/eccv/2016/kroeger2016eccv-fast)
- **NeuFlow v2.** Sintel inference time: 0.015 s on an RTX 2080 vs. 0.125 s for RAFT (32 iterations), about 8× faster, with similar EPE (Sintel clean/final 1.24/2.67 vs. RAFT 1.43/2.71). Over 20 fps at 512×384 on a Jetson Orin Nano. — [NeuFlow v2, arXiv 2408.10161](https://arxiv.org/abs/2408.10161)
- **ffmpeg `-flags2 +export_mvs` on HEVC: no motion-vector side data.** On the GoPro HEVC P-frames ffmpeg emits none, but it does on an H.264 transcode of the same clip. It also requires software decode. — [Local test]
- **The current stability pass is KLT + RANSAC**, at 2.2 ms/frame, "fully hidden behind decode". — (project memory note `reference_ollama_rtx3070.md`; `nxcore/stability.py`)

### Inferences
- **Keep the CPU KLT path.** GPU flow only matters if a future feature needs dense flow, e.g. subject-motion maps for "moment" scoring at more than 4 Hz.
  - First choice for that case: **DIS ultrafast on the existing 480 px gray frames**, since it adds no dependency and stays well under the decode budget.
  - Second choice: **NVOFA 4×4 fast**, which leaves CUDA cores free for Whisper or SigLIP running in parallel. It needs an OpenCV CUDA build (the pip wheels do not include CUDA modules; not verified this session) and carries NVIDIA SDK licence terms.
- **Motion-vector export is not useful here.** It is H.264-only, so it does not help the main 4K HEVC sources, and it forces CPU decode (≈179 fps vs. ≈400 fps on NVDEC). It could serve as a "free" motion signal for H.264 phone clips that are CPU-decoded anyway, which fits with the hybrid CPU lane from section 1.
- **RAFT/NeuFlow** would compete for the 8 GB VRAM and CUDA cores for no wall-clock gain in a decode-bound pipeline.

### Gaps
- No Ampere 4K NVOFA figure. The SDK page's "up to 150 fps at 4K" is Turing-era and clock-dependent, so 4K would need a local test.
- Python bindings and licence for the Optical Flow SDK were not verified, and neither were the NeuFlow v2 code and weights licences.

---

## 3. Speeding up Whisper-family ASR on 8 GB GPUs (2025–2026)

### Takeaway
The tool already uses the fastest standard configuration: faster-whisper with `large-v3-turbo` and `BatchedInferencePipeline`. The remaining levers are mostly VRAM-side:
- **`int8_float16`** cuts VRAM by about 35% at about the same speed.
- **`unload_model()`/`load_model()`** releases and restores VRAM without destroying the pipeline.
- **Batch size can be raised** as long as the run stays under the free-VRAM budget (batch 8 cut large-v2 time about 3.7×).
- **VAD pre-segmentation** is already on by default in batched mode.

Distil-Whisper is English-only, so it is not an option for Vietnamese (see Gaps).

### Cited Findings
- **faster-whisper README benchmark on an RTX 3070 Ti 8 GB** (CUDA 12.4, large-v2, 13 min of audio, beam 5):

  | Configuration | Time | VRAM |
  |---|---|---|
  | fp16 | 1m03s | 4,525 MB |
  | fp16, batch_size=8 | 17 s | 6,090 MB |
  | int8 | 59 s | 2,926 MB |
  | int8, batch_size=8 | 16 s | 4,500 MB |
  | openai/whisper fp16 (reference) | 2m23s | 4,708 MB |

  — [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
- **faster-whisper details.**
  - VAD (Silero) is "enabled by default for batched transcription".
  - The default VAD only removes silences longer than 2 s; `vad_parameters` such as `min_silence_duration_ms=500` can tighten that.
  - The latest ctranslate2 supports only CUDA 12 + cuDNN 9.
  - Licence: MIT.

  — [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
- **OpenAI turbo.** 809M parameters, ~6 GB VRAM in the reference implementation, "~8x" relative speed vs. large (A100, English). Not trained for translation. MIT code and weights. — [openai/whisper README](https://github.com/openai/whisper)
- **Secondary benchmarks** (methodology unclear):
  - Turbo int8 on a 6 GB RTX 3060 Laptop: 46.1 s sequential → 18.7 s at batch 10, about 1.5 GB VRAM.
  - RTX 4090 (one vendor): about 80× realtime single-stream → 175× batched (batch 16).

  — [whispernotes.app](https://whispernotes.app/blog/introducing-whisper-large-v3-turbo); [gigagpu.com](https://gigagpu.com/rtx-4090-24gb-whisper-benchmark/)
- **CTranslate2 4.8.2 `Whisper` API.**
  - The constructor takes `flash_attention: bool = False` ("flash attention 2 for self-attention layer"), plus `inter_threads` and `max_queued_batches`.
  - `unload_model(to_cpu=False)` frees the model but "keep[s] enough runtime context to quickly resume". `to_cpu=True` parks it in RAM instead, and `load_model(keep_cache=…)` restores it.

  — [CTranslate2 Whisper docs](https://opennmt.net/CTranslate2/python/ctranslate2.models.Whisper.html)
- **Unload/VRAM pitfalls in practice.** Deleting the Python object did not release GPU memory for one user. The Faster-Whisper-XXL notes list fixes for "model didn't free VRAM" and for an `unload_model` error with batched inference. — [OpenNMT forum](https://forum.opennmt.net/t/unload-whisper-model-from-gpu-in-python/5601); [Purfview Faster-Whisper-XXL release](https://github.com/Purfview/whisper-standalone-win/releases/tag/Faster-Whisper-XXL)
- **WhisperX.**
  - The paper reports VAD "Cut & Merge" into ≈30 s chunks plus batched inference: a **12× speedup** without WER degradation.
  - The repo claims about 70× realtime for large-v2 at under 8 GB (beam 5). That is a repo claim, not peer-reviewed.

  — [WhisperX, arXiv 2303.00747](https://arxiv.org/abs/2303.00747); [WhisperX summary](https://www.sourcepulse.org/projects/1473319)

### Inferences
- **When Ollama or SigLIP is resident, try `int8_float16` first** rather than `float16` (the current code tries float16 first). On an RTX 3070 Ti, large-v2 int8 used about 35% less VRAM (2.9 vs. 4.5 GB) at the same speed. Pick the compute type from free VRAM measured with NVML/`nvidia-smi`, not by try/except, and measure the effect on Vietnamese WER with the existing bộ thử (test set).
- **Raise `batch_size` while VRAM allows.** The batch-8 gain was about 3.7× on large-v2. Turbo's decoder has 4 layers instead of 32, so its absolute VRAM is lower. Treat the batch size as a VRAM-budget parameter rather than a constant.
- **Unload between stages instead of deleting.** Use `model.model.unload_model()` (the CT2 object inside faster-whisper's `WhisperModel`) when the GPU is handed to wav2vec2, SigLIP or Ollama, then `load_model()` to resume. This avoids a cold re-init (disk read plus CUDA context). Verify with `nvidia-smi` that memory is really released; the XXL notes show bugs with batched pipelines.
- **CT2 `flash_attention=True`** is a cheap experiment on Ampere. No published Whisper speedup was found, so measure tokens/s on the bộ thử (test set).
- **wav2vec2 forced alignment** (`dragonSwing/wav2vec2-base-vietnamese`, ~95M params) is small. Batching its windows and running it in fp16 matters more than the model.

### Gaps
- No published Vietnamese-specific speed benchmark for PhoWhisper or EraX-WoW-Turbo vs. stock turbo was found. Both are expected to match their base architectures (PhoWhisper = full large-v2/v3 size, so ~8× slower than turbo per OpenAI's table). Not verified.
- Distil-Whisper being English-only was not re-fetched this session. It comes from the model cards and should be confirmed before closing the option.
- No source quantifies the speed effect of CT2 `flash_attention` for Whisper on Ampere.

---

## 4. Running ONNX models fast on Windows (CUDA / TensorRT / DirectML EPs, fp16/int8, batching, sharing)

### Takeaway
On Windows + RTX in 2026:
- **DirectML** is in "sustained engineering": supported, but new work has moved to Windows ML.
- **TensorRT-RTX EP** is NVIDIA's client-focused provider, shipped through Windows ML and Windows Update.
- **ONNX Runtime CUDA EP 1.30** is CUDA 13 by default.

For the small models here (WeSpeaker 26 MB on CPU, SigLIP2-base already in torch fp16, PANNs), the cheap wins are runtime settings, not new runtimes:
- Cap the arena with `gpu_mem_limit`.
- Use `arena_extend_strategy=kSameAsRequested`.
- Avoid exhaustive cuDNN search for variable shapes.
- Use IOBinding, and reuse one session per process.

TensorRT fp16 gains for ViT encoders are modest: about 1.3× for ViT-H in one paper, larger for small CNNs.

### Cited Findings
- **ORT CUDA EP.**
  - Latest is 1.30.x, with CUDA 13.0 + cuDNN 9. The CUDA 12.x builds (1.27–1.30) need CUDA ≥ 12.8. GPU packages are built with CUDA 13.0 by default from 1.27.
  - Since 1.21, `onnxruntime.preload_dlls()` loads CUDA/cuDNN/MSVC DLLs, searching PyTorch's `lib` directory first. `pip install onnxruntime-gpu[cuda,cudnn]` installs the runtime DLLs.

  — [ORT CUDA EP docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
- **ORT CUDA EP defaults:**

  | Option | Default |
  |---|---|
  | `gpu_mem_limit` | unlimited |
  | `arena_extend_strategy` | kNextPowerOfTwo |
  | `cudnn_conv_algo_search` | EXHAUSTIVE |
  | `do_copy_in_default_stream` | true |
  | `enable_cuda_graph` | 0 |
  | `use_tf32` | 1 |

  — [ORT CUDA EP docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
- **ORT guidance on IOBinding and CUDA graphs.**
  - IOBinding "should be utilized to avoid overhead resulting from copies on inputs and outputs".
  - CUDA graphs cut kernel-launch overhead but require fixed shapes and addresses, IOBinding, every node on the CUDA EP, no control flow, and single-threaded `Run()`.

  — [ORT CUDA EP docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
- **DirectML status.** "DirectML is in sustained engineering … new feature development has moved to WinML". — [ORT DirectML EP docs](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
- **TensorRT-RTX EP.** It is an ORT / Windows ML EP "designed specifically to accelerate ONNX model inference on NVIDIA RTX GPUs" and is delivered via Windows Update (KB5089168, version 2.2604.1.0, April 2026). NVIDIA claims "50% faster throughput" vs. DirectML on RTX; this is a vendor claim. — [Microsoft KB5089168](https://support.microsoft.com/uk-UA/servicing/os/windows/ai-components/2026/04/kb5089168-nvidia-tensorrt-rtx-execution-provider-update-version-2-2604-1-0); [edge-ai-vision / NVIDIA article](https://www.edge-ai-vision.com/2025/10/deploy-high-performance-ai-models-in-windows-applications-on-nvidia-rtx-ai-pcs/)
- **CLIP encoder exports (Chinese-CLIP paper).** Exported to ONNX/TensorRT fp16 on a T4 at batch 1, TensorRT was 1.3× (ViT-H/14) to 9.5× (RN50) faster than PyTorch, with gains largest for small models. — [Chinese CLIP, arXiv 2211.01335](https://arxiv.org/abs/2211.01335)
- **TensorRT precision pitfall.** TensorRT only obeys the ONNX precision when the engine is built `--stronglyTyped`; otherwise the expected fp16 speedup can be missing. — [NVIDIA dev forum thread](https://forums.developer.nvidia.com/t/tensorrt-inference-api-that-open-clip-vit-l-14-is-slowing-down/309551)
- **Current tool state.**
  - WeSpeaker runs through `ort.InferenceSession(..., providers=["CPUExecutionProvider"])`: 8 s for 450 windows.
  - SigLIP2 runs in torch fp16 inside a long-lived worker. Loading transformers + SigLIP2 cost "~8 s per process", which is why the worker was introduced.

  — (`engine/nxcore/ai/speakers.py`, `engine/nxcore/ai/embed.py`, `engine/nxcore/ai/worker.py`; project roadmap C3)

### Inferences
- **Do not move WeSpeaker to the GPU.** At 8 s per 450 windows on CPU it overlaps with GPU stages, and a CUDA EP session would add about 300–500 MB of CUDA context plus arena to an already tight card (my estimate; not measured).
- **If any ORT GPU session is added,** set `gpu_mem_limit` (for example 512 MB for small models), `arena_extend_strategy=kSameAsRequested` and `cudnn_conv_algo_search=HEURISTIC` (the default EXHAUSTIVE costs a slow first run per new input shape). Also call `onnxruntime.preload_dlls()` so ORT reuses PyTorch's CUDA DLLs instead of shipping a second copy.
- **SigLIP2-base and TensorRT.** A TensorRT fp16 engine, via the TensorRT-RTX EP or plain TensorRT, might give about 1.3–2× on embedding throughput. Embedding runs on keyframes, so decode and model load dominate. Priority: low.
  - The existing worker already removes the 8 s load.
  - A safer quick win is larger batches (32–64 crops) with `torch.inference_mode()` and pinned host buffers.
- **Avoid DirectML for new work**, since it is in sustained engineering. If a vendor-neutral path is needed for non-NVIDIA users, Windows ML is the route, at the cost of a Windows-App-SDK dependency.
- **int8 quantisation of SigLIP2 vision** is not worth the accuracy risk for a 92M-parameter base model whose stage is not the bottleneck. No measured source.

### Gaps
- No published ORT-CUDA-fp16 vs. TensorRT-fp16 vs. torch-fp16 benchmark for SigLIP/SigLIP2 on Ampere was found.
- TensorRT-RTX EP availability for a pip-installed Python ORT (outside Windows ML) and its licence were not verified.

---

## 5. VRAM scheduling when several models and an Ollama LLM share 8 GB

### Takeaway
Two facts explain the "×25 slowdown":
- Windows drivers since 536.40 silently spill CUDA allocations into shared system memory instead of raising out-of-memory errors.
- On this machine about 2 GB of the 8 GB was already in use by other apps before the engine started.

The countermeasures, in order of impact:
1. Set NVIDIA's **"CUDA – Sysmem Fallback Policy: Prefer No Sysmem Fallback"** per-application for `ollama.exe` and the engine's `python.exe`, so spills become visible errors.
2. Run a **single GPU lease/queue** in the engine: one heavy model resident at a time, with a free-VRAM check before each load.
3. Pin Ollama at `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`. The q8_0 KV cache is half-size with "usually no noticeable impact" but is global, and the FAQ warns that high-GQA models such as the Qwen family may be more affected.
4. Keep `num_ctx` minimal per call.

### Cited Findings
- **Sysmem Fallback Policy.** Since Windows driver 536.40 (June 2023), the driver no longer throws out-of-memory when a CUDA app overfills VRAM; it spills into "shared GPU memory", which causes severe slowdowns. The fix: NVIDIA Control Panel → Manage 3D Settings → "CUDA – Sysmem Fallback Policy" → "Prefer No Sysmem Fallback", either globally or per program. A per-app override is safer, and the app must be restarted. — [runaihome guide (2026)](https://runaihome.com/blog/shared-gpu-memory-slow-local-ai-sysmem-fallback-fix-2026/); [Borncity](https://borncity.com/blog/2023/11/26/windows-10-11-hardware-beschleunigte-gpu-planung-und-nvidia-cuda-policy/); [Cognex docs: Disable Shared GPU Memory](https://docs.cognex.com/deep-learning_400/web/EN/deep-learning/Content/Topics/optimization/gpu-disable-shared.htm)
- **Caveats on the fallback setting.**
  - One user reports it does not work with two GPUs in the machine.
  - An Adobe Camera Raw user preferred to *keep* fallback so the PC stayed functional.

  These are user reports, not NVIDIA statements. — [SideFX forum](https://www.sidefx.com/forum/topic/95347); [Adobe community](https://community.adobe.com/t5/photoshop-ecosystem-discussions/huge-vram-usage-camera-raw/td-p/13945236)
- **Measured on this machine.**
  - The Ollama runner loaded into crowded VRAM read prompts at 92 vs. 2,350 tok/s while `ollama ps` still showed "100% GPU".
  - Chrome often holds about 40% of GPU SM.
  - At the time of this session's tests, `nvidia-smi` showed **2,071 MiB of 8,192 MiB already used** with no engine model loaded.

  — (project memory note; [Local test])
- **Ollama FAQ: defaults.**
  - Context window: 4,096 tokens by default; override with `OLLAMA_CONTEXT_LENGTH` or per-request `num_ctx`.
  - `OLLAMA_KEEP_ALIVE`: 5 min by default. Per-request `keep_alive` overrides it: `0` unloads immediately and negative values keep the model forever.
  - `OLLAMA_MAX_LOADED_MODELS`: default "3 * the number of GPUs".
  - `OLLAMA_NUM_PARALLEL`: default 1. RAM scales with `NUM_PARALLEL × CONTEXT_LENGTH`, so 4 parallel × 2K becomes 8K.

  — [Ollama FAQ](https://docs.ollama.com/faq)
- **Ollama FAQ: flash attention and KV cache.**
  - Flash attention: `OLLAMA_FLASH_ATTENTION=1` forces it on. Otherwise it is automatic where supported, and it "reduces memory use as context grows".
  - KV cache: `OLLAMA_KV_CACHE_TYPE` applies only with flash attention and is **global for all models**.

    | Type | Memory vs. f16 | Precision loss |
    |---|---|---|
    | `f16` | 100% | none |
    | `q8_0` | ≈½ | "usually has no noticeable impact" |
    | `q4_0` | ≈¼ | "small-medium", more noticeable at larger contexts |

    The FAQ warns that models with high GQA counts, such as Qwen2, may be affected more.

  — [Ollama FAQ](https://docs.ollama.com/faq)
- **Ollama FAQ: Windows.** Environment variables are read at server start: quit Ollama from the tray, set the variables, then restart. `ollama ps` shows the CPU/GPU split. — [Ollama FAQ](https://docs.ollama.com/faq)
- **KV quantisation trade-offs (secondary guides).** It saves memory, not time, and f16 is faster when it fits. Some guides say Ollama panics when flash attention is unsupported, others that it falls back to f16 silently, so verify real VRAM use. — [smcleod.net (author of the Ollama KV-quant PR)](https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/); [SSD Nodes](https://www.ssdnodes.com/learn/ollama-kv-cache-quantization)
- **Ollama's new scheduler (Sept 2025).** The new engine "measure[s] the exact amount of memory required" instead of estimating it, which means fewer OOMs and `nvidia-smi` matching `ollama ps`. Initially this covered only new-engine models (qwen3 and gemma3 among them). The source is a mirror of Ollama's blog post. `OLLAMA_GPU_OVERHEAD` reserves VRAM per GPU, but sources disagree on its default (0 vs. 512 MiB). — [mirror of Ollama blog "New model scheduling"](https://mail233685.mx4414.netcup.net/blog/new-model-scheduling); [virtualizationhowto](https://www.virtualizationhowto.com/community/artificial-intelligence-ai/ollama-adds-new-memory-management-and-scheduling-improvements-to-the-latest-release/); [hostkey docs](https://hostkey.com/documentation/technical/gpu/ollama/)
- **`num_gpu` = number of layers offloaded to the GPU,** not the number of GPUs. Lowering it trades speed for avoiding overflow. The KV cache is allocated for the full `num_ctx` up front. — [eastondev guide](https://eastondev.com/blog/en/posts/ai/ollama-gpu-scheduling/); [popularai](https://www.popularai.org/p/fix-ollama-cpu-offloading-slow-inference)
- **PyTorch allocator.**
  - `torch.cuda.empty_cache()` releases *unused* cached blocks "so that those can be used by other GPU applications". Cached memory still shows as used in `nvidia-smi`.
  - `PYTORCH_ALLOC_CONF` (`PYTORCH_CUDA_ALLOC_CONF` is an alias) options:
    - `expandable_segments:True` is experimental and reduces fragmentation when allocation sizes vary.
    - `garbage_collection_threshold:0.8` reclaims unused blocks early.
    - `per_process_memory_fraction` caps the process; going beyond it raises OOM.
    - `backend:cudaMallocAsync` is available.

  — [PyTorch CUDA semantics (2.14)](https://docs.pytorch.org/docs/2.14/notes/cuda.html)
- **CTranslate2** `unload_model()` / `load_model()` let Whisper release VRAM and resume quickly. — [CTranslate2 docs](https://opennmt.net/CTranslate2/python/ctranslate2.models.Whisper.html)

### Inferences
- **Concrete Ollama service configuration for this machine.** Set at the user level, then restart Ollama:
  - `OLLAMA_MAX_LOADED_MODELS=1`, so a second model can never co-reside.
  - `OLLAMA_NUM_PARALLEL=1`.
  - `OLLAMA_FLASH_ATTENTION=1`.
  - `OLLAMA_KV_CACHE_TYPE=q8_0`. Re-run the B2 nDCG and B4 chapter bộ thử (test sets) with ≥ 6 seeded runs because of the Qwen/GQA warning.
  - Optionally `OLLAMA_GPU_OVERHEAD` of about 1–1.5 GB, so the new scheduler leaves room for Chrome/Premiere/DWM and offloads a few layers to CPU instead of spilling into shared memory. Partial CPU offload degrades gracefully; shared-memory spill degraded about 25× here.
- **Rough KV estimate.** If Qwen3-VL-8B has ≈36 layers × 8 KV heads × 128 head dim (assumed, not verified), the f16 KV cache costs ≈ 144 KB/token. That is ≈ 0.56 GiB at `num_ctx` 4096, ≈ 0.28 GiB at q8_0, and ≈ 0.28 GiB at f16 with `num_ctx` 2048. With the weights at roughly 6 GB, those few hundred MB are exactly the margin that decides whether the model spills.
- **Per-app "Prefer No Sysmem Fallback" for `ollama.exe` and the engine's `python.exe`** turns silent ×25 slowdowns into immediate OOMs. Ollama's measuring scheduler should then place fewer layers on the GPU. This needs testing: confirm Ollama falls back to partial CPU offload rather than failing. Keep the existing "<300 tok/s → reload" detector as a second line of defence.
- **Engine-side GPU lease.** One mutex or queue in the long-lived worker/analyzer, with these states: Whisper (CT2) → wav2vec2 → SigLIP2/PANNs (torch) → Ollama VLM. Before each acquire:
  - query free VRAM with NVML/`nvidia-smi`;
  - unload the previous holder (`unload_model()` for CT2, `free()` + `empty_cache()` for torch, `keep_alive:0` for Ollama);
  - require `free ≥ need + margin`.

  Also set a `per_process_memory_fraction` for the torch process (e.g. 0.35) so a SigLIP batch can never push Ollama into spill.
- **Detect spills without trusting `ollama ps`,** which showed 100% GPU while spilled. Options: tokens/s probes (already implemented), or the Windows performance counter `\GPU Process Memory(*)\Shared Usage` per PID (not verified this session).

### Gaps
- NVIDIA's own support article on the fallback policy (nvidia.custhelp.com a_id 5490) returned HTTP 401, so the details come from third-party guides.
- The current `OLLAMA_GPU_OVERHEAD` default and whether qwen3-vl runs on the new measuring engine in the installed Ollama build were not verified. The project memory says "Ollama 0.40 llamacpp runner", which suggests it may *not* use the new engine for this model.
- No measured data was found on q8_0 KV quality for Qwen3-VL specifically.
- Behaviour of Ollama under "Prefer No Sysmem Fallback" (graceful re-plan vs. crash) is undocumented.

---

## 6. Caching and incremental processing, CPU/GPU overlap, threads vs. processes, free-threaded Python

### Takeaway
- **Caching:** the current size+mtime+path keys are fast but break when footage folders are moved or copied, which is common. Pair the stat key with a cheap content fingerprint, such as XXH3 of the head, tail and a few interior blocks plus the file size. XXH3 runs at 31–59 GB/s vs. 0.6 GB/s for MD5, so even full hashing is disk-bound. Add per-stage version stamps.
- **Concurrency:** what matters on Windows is a small number of long-lived processes (spawn re-imports torch), threads for orchestrating ffmpeg/NVDEC (native code releases the GIL), and explicit lanes that overlap NVDEC, CPU decode, CPU analysis and the GPU model.
- **Free-threaded Python 3.14** is officially supported but brings little here and carries ecosystem risk.

### Cited Findings
- **xxHash throughput** (i7-9700K):

  | Hash | Throughput |
  |---|---|
  | XXH3 | 31.5 GB/s (SSE2), 59.4 GB/s (AVX2) |
  | XXH128 | 29.6 / 57.9 GB/s |
  | MD5 | 0.6 GB/s |
  | SHA1 | 0.8 GB/s |
  | RAM memcpy (reference) | 28 GB/s |

  The library is BSD-2-Clause; the `xxhsum` CLI is GPLv2. — [xxHash README](https://github.com/Cyan4973/xxHash)
- **Current cache keys.**
  - Montage cache: `md5(abspath|size|int(mtime))`.
  - QC/stability: `"size:mtime"` keys in `_nexys_work/*.json`. Child tools re-run "only new/changed clips".

  — (`engine/nxcore/montage.py`, `nxcore/qc.py`, `nxcore/analyze.py`)
- **Audio decode and overlap.** Decoding 16 kHz audio from the 4K files is cheap (60 clips / 15.8 GB in 2.8 s). Overlapping decode with GPU work (`montage.decode_16k_many`) took the sound index from 28 s to 15 s. — (project memory note)
- **Free-threaded Python.** PEP 779 made it officially supported in 3.14, with a ≤ 15% single-thread regression ceiling and ≤ 20% extra memory. Write-ups report a 5–10% gap in 3.14, mainly because the specialising interpreter is re-enabled in the free-threaded build. — [pydevtools: What is PEP 779](https://pydevtools.com/handbook/explanation/what-is-pep-779/); [danilchenko.dev](https://www.danilchenko.dev/posts/python-314-free-threading)
- **TorchCodec 0.17** stops re-enabling the GIL on import under free-threaded CPython: an example of ecosystem support arriving package by package. — [torchcodec releases](https://github.com/meta-pytorch/torchcodec/releases)
- **Python on Windows uses the `spawn` start method by default.** Each worker starts a fresh interpreter and re-imports modules. — [Python docs: multiprocessing start methods](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods)
- **Decoder reuse across short clips** gives 1.2× at 4K/2 s, and up to 7.2× at 360p, from avoiding re-initialisation. Project memory also measured PyNvVideoCodec decoder init on 4K HEVC at ≈ 2 s. — [PyNvVideoCodec Performance](https://docs.nvidia.com/video-technologies/pynvvideocodec/pynvc-api-prog-guide/application-note.html); (project memory note)

### Inferences
- **Two-level cache key.**
  - **Fast path:** the size + mtime stat key, as today.
  - **Identity:** size + XXH3-64 over the first 4 MB, the last 4 MB and 8 evenly spaced 1 MB blocks, which reads about 16 MB per clip regardless of length.

  Store results under the content fingerprint, so a moved or copied folder of 340 clips re-uses every analysis instead of re-decoding hours of 4K, and keep path → fingerprint as an index. Add a `stage_version` (like the existing `SND_V`, `v7`, `v8`) per result so only the changed stage recomputes. Python binding: `xxhash` on PyPI (BSD-2; binding licence not verified).
- **Make the PyNvVideoCodec ≈ 2 s per-clip decoder init (project memory) amortisable.** Keep one decoder per worker and reconfigure it per clip ("decoder caching"). Over 340 clips, 2 s × 340 ≈ 11 min if it is paid serially. Check whether the current code re-creates decoders per clip.
- **Lanes, not pools:**
  - (a) one NVDEC lane: decode once → QC, stability and proxy fan-out (section 1);
  - (b) one CPU-decode lane for every third clip or for H.264 phone clips;
  - (c) one GPU-model lane under the VRAM lease (section 5);
  - (d) audio lane (already present).

  Use threads to drive these, since ffmpeg/NVDEC/OpenCV/numpy release the GIL. Reserve processes for model hosting, which the long-lived worker already does.
- **Free-threaded 3.14: do not adopt for now.** The pipeline is bound by NVDEC, GPU and disk, and the GIL is not the measured limit. Free-threaded wheels for ctranslate2, onnxruntime, opencv and PyTorch on Windows are not confirmed.

### Gaps
- Free-threaded-compatible Windows wheels for ctranslate2, onnxruntime-gpu, opencv-python and PyNvVideoCodec were not checked.
- The exact Python docs wording on `spawn` was not re-fetched this session; the URL is the standard docs location.
- No source for the disk throughput of the user's footage drives, which decides whether full-file hashing is viable.

---

## 7. Faster final steps: NVENC proxies, alpha overlays, smart rendering

### Takeaway
Proxy creation from 4K60 HEVC is **decode-bound**. h264_nvenc presets p1, p4 and p7 all take about 3.1 s for the 20 s clip, and running 2 proxies concurrently gives no gain. Faster proxies therefore come from skipping the decode (fuse proxies into the analysis decode, or use LRV), not from NVENC tuning.

For alpha overlays, NVENC cannot help: ProRes, CineForm, QT Animation and PNG are CPU encoders. Measured locally on 1080p30 alpha frames:
- `prores_ks` 4444 with a fixed `-qscale:v` was about 3× faster than its default rate control.
- PNG-in-MOV was fastest on flat synthetic graphics.

### Cited Findings
- **NVENC proxy presets** (960×540 h264_nvenc from the 4K60 HEVC test clip, frames kept on the GPU):

  | Preset | Time | Size |
  |---|---|---|
  | p1 | 3.14 s | 8.1 MB |
  | p4 (current) | 3.08 s | 7.2 MB |
  | p7 | 3.12 s | 7.3 MB |

  Two p4 proxies running concurrently took 6.23 s, the same as sequential, so the run is NVDEC-bound. — [Local test]
- **NVENC 1080p throughput** (Ampere, RTX 3090, HEVC): p1 is 875 fps CBR low-latency (865 fps VBR high-quality), and p5 falls to 282 fps. — [NVENC Application Note 12.1](https://docs.nvidia.com/video-technologies/video-codec-sdk/12.1/nvenc-application-note/index.html)
- **Consumer GeForce cards are limited to 8 concurrent NVENC sessions per system** from SDK 12.2, which needs Windows driver ≥ 551.76; earlier SDKs allowed 3, then 5. — [NVENC Application Note 13.0](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-application-note/index.html); [NVIDIA dev forum](https://forums.developer.nvidia.com/t/how-to-increase-number-of-nvenc-concurrent-sessions/169367)
- **Alpha-capable encoders in local ffmpeg 9.0.1:**

  | Encoder | Pixel formats | Threading |
  |---|---|---|
  | `prores_ks` | yuva444p10le | frame + slice |
  | `cfhd` (CineForm) | yuv422p10le, gbrp12le, **gbrap12le** | frame |
  | `qtrle` | argb | none |

  — [Local test: `ffmpeg -h encoder=…`]
- **Local alpha encode test** (1080p30, 300 frames, synthetic `testsrc2` with the top 70% fully transparent):

  | Encoder | Time | Throughput | Size |
  |---|---|---|---|
  | `prores_ks -profile:v 4444` (default rate control) | 3.33 s | 90 fps | 179 MB |
  | `prores_ks 4444 -qscale:v 9` | 1.11 s | 270 fps | 106 MB |
  | `cfhd gbrap12le` | 3.54 s | — | 268 MB |
  | `qtrle argb` | 3.91 s | — | 112 MB |
  | `png` in MOV | 0.78 s | — | 55 MB |

  Synthetic content, so absolute sizes and the PNG advantage are not representative of real titles. — [Local test]
- **Older reports on `prores_ks`.** It was described as "single threaded" and slower than Apple's encoder. These are old forum posts; the local ffmpeg 9.0.1 reports frame + slice threading, so the claim is outdated. — [VideoHelp forum](https://forum.videohelp.com/showthread.php?p=2395145); [Local test]
- **PyNvVideoCodec segment transcoding** with a persistent context ran 2.8× faster than FFmpeg for 1080p H.264 segment extraction. — [PyNvVideoCodec Performance](https://docs.nvidia.com/video-technologies/pynvvideocodec/pynvc-api-prog-guide/application-note.html)

### Inferences
- **Proxies:** emit the proxy from the same NVDEC decode as the analysis (ffmpeg `split` → `scale_cuda` → h264_nvenc, plus a gray 480 px branch piped to Python). The proxy then becomes nearly free when analysis runs anyway. Leave the preset at p4, since it is not the bottleneck. If proxies are made without analysis, the CPU-decode lane from section 1 still adds about 1.3× throughput.
- **Overlays:** for ProRes 4444 overlays, pass a fixed quantiser (`-qscale:v` around 4–9, tuned by eye) instead of the default rate control; measured about 3× faster. For flat motion-graphics overlays, PNG-in-MOV or QT Animation can be faster and smaller. Premiere's decode cost and compatibility for each format must be checked separately; that is out of scope here.
  - Render overlays at the sequence resolution and only for their active duration (trim fully transparent head and tail).
  - Run several overlay encodes in parallel. These are CPU encoders, so they scale with the 24 threads, unlike NVENC/NVDEC.
- **"Smart rendering" for ffmpeg-produced cuts:** stream-copy (`-c copy`) cuts on GoPro sources land on 1.001 s keyframes, so frame-accurate cuts need re-encoding of only the head GOP. Premiere-side smart rendering is out of scope.

### Gaps
- No published `cfhd` vs. `prores_ks` speed comparison was found, and the local test used synthetic content.
- Premiere's import and decode performance for PNG-MOV, QT Animation and CineForm-alpha overlays was not researched (out of scope).
- HEVC-with-alpha NVENC encoding is not exposed by ffmpeg's `hevc_nvenc` (local `-h` showed no alpha option), and Premiere support for it on Windows is unverified.

---

### Licences mentioned (verified this session unless noted)
- **Verified:** PyNvVideoCodec MIT ([NVIDIA blog](https://developer.nvidia.com/blog/whats-new-in-pynvvideocodec-2-0-for-python-gpu-accelerated-video-processing)); faster-whisper MIT ([README](https://github.com/SYSTRAN/faster-whisper)); OpenAI Whisper code and weights MIT ([README](https://github.com/openai/whisper)); xxHash library BSD-2-Clause, `xxhsum` GPLv2 ([README](https://github.com/Cyan4973/xxHash)).
- **Not re-verified this session:**
  - Open source: ONNX Runtime (MIT), CTranslate2 (MIT), Ollama (MIT), TorchCodec (BSD-3), Decord (Apache-2.0), WhisperX (BSD-2).
  - NVIDIA SDKs: NVIDIA Optical Flow SDK and TensorRT / TensorRT-RTX are under proprietary NVIDIA licences.
  - Unknown: NeuFlow v2 code and weights.

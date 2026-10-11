# Local AI models and usage techniques for Justin EDIT Suite (RTX 3070 8 GB, Vietnamese, offline). Status as of Oct 2026

Scope reminder: models, prompting and decoding only. Things already built (per docs/LO_TRINH_THUAT_TOAN.md) are not re-recommended: SigLIP2 → Qwen3-VL two-stage re-rank (B2, nDCG@6 0.877 → 0.915, 2×2 grid worse at 0.841), LLM chaptering (B4, mean 4.5/8, range 3–6), filler recovery from wav2vec2 gaps (A4), PANNs Cnn14 sound search (C2), WeSpeaker + NME-SC diarization (C3), auto-reload of the Ollama runner on VRAM spill.

## Q1. Best small (≤ 8–9B) vision-language models for video/frame understanding on 8 GB

### Takeaway
Two families lead at this size in 2026, both Apache-2.0: **Qwen3.5 small** (0.8B/2B/4B/9B, released 2 Mar 2026, natively multimodal) and **Qwen3-VL** (2B/4B/8B, what the tool uses now). On an independent benchmark (Video-MME-v2) in **non-thinking** mode, Qwen3-VL-8B and Qwen3.5-9B are about equal, and Qwen3-VL-4B beats Qwen3.5-4B. Qwen3.5 pulls ahead only with thinking on, which costs latency. Gemma 4 (Apache-2.0, Apr 2026) is the main non-Qwen alternative, with selectable image-token budgets, but its Ollama E4B build is 6.6–9.5 GB. InternVL3.5 and MiniCPM-V 4.5 are competitive but have weaker Ollama support. Phi-4-multimodal does not support Vietnamese.

### Cited Findings
**Qwen3-VL (current model)**
- The family has dense 2B/4B/8B/32B and MoE 30B-A3B/235B-A22B models on Qwen3 backbones. It natively supports 256K interleaved context. — [Qwen3-VL Technical Report, arXiv 2511.21631](https://arxiv.org/pdf/2511.21631v1)
- Video input interleaves text timestamps: each temporal patch is prefixed with a timestamp such as "<3.0 seconds>", in both seconds and HMS formats. The report's evaluation used 2 fps (4 fps for Charades-STA), at most 640 tokens per frame (768 for VideoMMMU/MMVU), at most 2,048 frames and at most 224K video tokens. — [arXiv 2511.21631 (HTML)](https://arxiv.org/html/2511.21631v1)
- OCR covers 39 languages, up from 10 in Qwen2.5-VL, with accuracy above 70% on 32 of them. Vietnamese is not named in the text; the per-language list is only in a figure. — [arXiv 2511.21631 (HTML)](https://arxiv.org/html/2511.21631v1)
- Text benchmarks for the small sizes (Instruct / Thinking):

  | Benchmark | 2B | 4B | 8B |
  |---|---|---|---|
  | MMLU-ProX | 38.8 / 55.1 | 59.4 / 65.0 | 65.4 / 70.7 |
  | INCLUDE (multilingual) | 45.8 / 53.3 | 61.4 / 64.6 | 67.0 / 69.5 |

  The 2B model is much weaker at multilingual text. — [arXiv 2511.21631 (HTML)](https://arxiv.org/html/2511.21631v1)
- Qwen3-VL-8B-Instruct reports Video-MME 71.4 and MVBench 68.7 (third-party aggregator). Another aggregator lists Video-MME 65.1 without subtitles, so the numbers conflict and probably reflect different settings. — [Codesota](https://www.codesota.com/model/qwen3-vl-8b-instruct); [Wizwand](https://www.wizwand.com/task/video-understanding)
- The official recommended sampling for Qwen3-VL-8B-Instruct on vision tasks is temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5. The license is Apache-2.0. — [HF model card Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)

**Qwen3.5 small (new since the tool was built)**
- Four small dense models (0.8B, 2B, 4B, 9B) were released on 2 Mar 2026 under Apache-2.0. They are natively multimodal (text, image, video). — [Gigazine](https://gigazine.net/gsc_news/en/20260303-qwen-3-5-small); [Qudata](https://qudata.com/en/news/exploring-qwen35-family/)
- Model card for Qwen3.5-9B:
  - Architecture is a hybrid of Gated DeltaNet and gated attention, laid out as 8 × (3 DeltaNet blocks + 1 attention block).
  - Native context is 262,144 tokens. The model supports 201 languages and dialects.
  - **Thinking is on by default.** It is disabled with `enable_thinking: False`; the `/think` and `/nothink` soft switches are not officially supported.
  - Instruct sampling: temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5.
  
  — [HF Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)
- Model-card benchmarks, probably in the default thinking mode:

  | Benchmark | Qwen3.5-4B | Qwen3.5-9B | Comparison |
  |---|---|---|---|
  | Video-MME (without subtitles) | 76.9 | 78.4 | GPT-5-Nano 66.2, Gemini-2.5-Flash-Lite 72.7 |
  | Video-MME (with subtitles) | 83.5 | 84.5 | |
  | MVBench | 71.2 | 74.4 | |
  | MLVU | 82.8 | 84.4 | |
  | OCRBench | 85.0 | 89.2 | |
  | MMMLU | 76.1 | 81.2 | |
  | IFEval | 89.8 | 91.5 | |
  | BFCL-V4 | 50.3 | 66.1 | |

  — [HF Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B); [HF Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)
- Ollama tags: `qwen3.5:0.8b` 1.2–1.3 GB, `2b` 2.7–3.1 GB, `4b` 3.3–4.0 GB, `9b` 6.6–7.6 GB. All accept Text and Image, carry 256K context and are tagged "thinking". — [Ollama library qwen3.5](https://ollama.com/library/qwen3.5)

**Independent check: Video-MME-v2 (Apr 2026, harder, non-linear score)**
- Instruct models at 64 frames (Non-Lin score with / without subtitles):

  | Model | Non-Lin score |
  |---|---|
  | Qwen3.5-9B | 18.3 / 10.3 |
  | Qwen3-VL-8B | 18.2 / 12.4 |
  | Qwen3-VL-4B | 17.6 / 11.6 |
  | InternVL3.5-8B | 16.4 / 11.4 |
  | Qwen3.5-4B | 15.4 / 9.5 |
  | Qwen2.5-VL-7B | 14.4 / 10.3 |
  | Keye-VL-1.5-8B | 14.1 / 8.9 |
  | LLaVA-Video-7B | 9.7 / 7.2 |

  — [Video-MME-v2, arXiv 2604.05015](https://arxiv.org/html/2604.05015)
- Thinking variants: Qwen3.5-9B-Think scores 23.2 / 13.7 at 64 frames and 26.2 / 19.5 at 512 frames. Qwen3-VL-8B-Think scores 19.7 / 11.8 at 64 frames. The gain from thinking depends on the model: Qwen3.5-9B gains +4.9 / +3.4, while Qwen3-VL-8B gains +1.5 with subtitles and loses 0.6 without them. Every model scores higher with subtitles or audio text. — [arXiv 2604.05015](https://arxiv.org/html/2604.05015)

**Other candidates**
- **InternVL3.5-4B:** Video-MME 65.4 / 68.6 (best of 16–64 frames) and MVBench 71.2 (16 frames). — [InternVL3.5 paper, arXiv 2508.18265](https://arxiv.org/pdf/2508.18265)
  - The license is Apache-2.0, and its Qwen3 component is also Apache-2.0.
  - The Visual Resolution Router (Flash variant) compresses a patch to 64 tokens instead of 256, cutting visual tokens by 50% with "nearly 100%" of the performance.
  - Recommended deployment is LMDeploy or vLLM. GGUF builds are community-only.
  
  — [HF InternVL3_5-8B](https://huggingface.co/OpenGVLab/InternVL3_5-8B)
- **MiniCPM-V 4.5 (8B):** built on Qwen3-8B and SigLIP2-400M. Video-MME 73.5. Its 3D-Resampler packs 6 frames into 64 tokens, a 96× compression. — [HF MiniCPM-V-4_5](https://huggingface.co/openbmb/MiniCPM-V-4_5)
  - The HF card says weights and code are Apache-2.0, which conflicts with Voxel51's listing of a "MiniCPM Model License" with commercial thresholds. — [Voxel51](https://docs.voxel51.com/plugins/plugins_ecosystem/minicpm_v.html)
  - It covers 30+ languages, but Vietnamese is not named. llama.cpp support landed in release b6282; Ollama support was still "Merging" at the time of the card. — [HF MiniCPM-V-4_5](https://huggingface.co/openbmb/MiniCPM-V-4_5)
- **Gemma 4:** released 2 Apr 2026 under Apache-2.0 in sizes E2B, E4B, 12B, 26B-A4B and 31B. — [The Decoder](https://the-decoder.com/googles-gemma-4-is-now-available-with-apache-2-0-licensing-for-the-first-time/); [Ollama gemma4](https://ollama.com/library/gemma4)
  - E4B has 4.5B effective parameters (8B with embeddings).
  - Image token budget is selectable: 70, 140, 280, 560 or 1120 tokens. Low budgets are recommended for captioning and video, high ones for OCR.
  - Video is handled as frames, up to 60 s at 1 fps. Audio input is up to 30 s.
  - E4B vs E2B: MMMU-Pro 52.6 vs 44.2; MMMLU 76.6 vs 67.4.
  - Pretrained on 140+ languages, with out-of-the-box support for 35+. Recommended sampling: temperature 1.0, top_p 0.95, top_k 64.
  
  — [HF gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it)
  - Ollama sizes: `gemma4:e2b` 4.6–7.5 GB, `e4b` 6.6–9.5 GB, `12b` 7.7–8.0 GB. Ollama lists only Text and Image input, not audio. — [Ollama gemma4](https://ollama.com/library/gemma4)
- **Phi-4-multimodal (5.6B, MIT):** vision is English-only, and Vietnamese is not supported in any modality. Video-MME 55.0 at 16 frames. — [HF Phi-4-multimodal-instruct](https://huggingface.co/microsoft/Phi-4-multimodal-instruct)

**Vietnamese ability**
- VMLU scores (self-reported by the Unicorn team): Qwen3-VL-8B-Thinking 74.10, Qwen3-4B-Thinking-2507 70.00, Qwen3-8B 69.00. Their Vietnamese fine-tune Unicorn-VL-R3 (on Qwen3-VL-8B) scores 74.87; no license is stated. — [HF unicorn-team/Unicorn-4B-R3](https://huggingface.co/unicorn-team/Unicorn-4B-R3); [Featherless Unicorn-VL-R3](https://featherless.ai/models/unicorn-team/Unicorn-VL-R3)
- On the official VMLU leaderboard, older models score far lower: gemma-2-9b-it 59.04, Qwen2.5-7B-Instruct 57.51, Vintern-3B-beta 54.81. — [VMLU leaderboard](https://vmlu.ai/leaderboard)
- Vintern-3B-beta (Qwen2.5-3B + InternViT-300M, trained on more than 10M Vietnamese QA pairs) claims to beat GPT-4o on the Vietnamese part of MTVQA. This is self-reported. — [HF Vintern-3B-beta](https://huggingface.co/5CD-AI/Vintern-3B-beta/blob/5e0006cc1ef37664b7c11a69a3f6a316c6ae6e92/README.md)

### Inferences
- **Qwen3.5 small models.**
  - The headline gains are mostly thinking-mode numbers. In non-thinking mode, which is what an interactive tool uses, `qwen3.5:9b` ≈ `qwen3-vl:8b` and `qwen3.5:4b` < `qwen3-vl:4b` on Video-MME-v2.
  - So swapping `qwen3-vl:8b` for `qwen3.5:9b` is not a clear win per VRAM. Run an A/B on the existing B2 (nDCG@6) and B4 (chapters, 6 seeded runs) test sets before switching.
  - Qwen3.5's hybrid DeltaNet layout should make the KV cache per token about 4× smaller than an all-attention 9B. Only 1 in 4 blocks keeps a growing KV cache; this is inferred from the 3:1 layout. Long transcripts (chaptering) would then fit with less spill, but the 6.6–7.6 GB of weights is already tight on 8 GB.
- **Smaller tier for VRAM headroom.**
  - `qwen3-vl:4b` is the strongest small model in non-thinking video QA. At roughly half the weights it would leave room for SigLIP2 and WeSpeaker, avoiding the measured 25× spill slowdown.
  - Its INCLUDE score (61.4 vs 67.0 for 8B) suggests some loss on Vietnamese text tasks, so keep the 8B for chapters and edit commands.
- **Avoid the 2B sizes for Vietnamese-language output.** Qwen3-VL-2B scores INCLUDE 45.8 and MMLU-ProX 38.8.
- **Gemma 4 E4B.** Its fixed token-budget knob is attractive for frame re-ranking (70–280 tokens per frame). But its Ollama footprint is about the same as qwen3-vl:8b, and its multilingual score (MMMLU 76.6) is similar to Qwen3.5-4B's. Worth testing only if Qwen's per-frame cost cannot be reduced by resizing (see Q4).
- **Timestamp grounding.** Qwen3-VL's timestamp interleaving is designed for that, so asking it for start/end times inside a short candidate clip (video input at 1–2 fps) could refine the B2 two-stage search beyond per-frame scoring. This needs to be measured.

### Gaps
- No official per-size Video-MME, MVBench or LongVideoBench numbers for Qwen3-VL-2B/4B/8B were extractable; the tech report tables did not parse. InternVL3.5-8B video numbers were also not found.
- No benchmark of any VLM on Vietnamese video or captioning was found. Vietnamese OCR coverage of Qwen3-VL/Qwen3.5 is not stated in text.
- Not verified: whether Qwen3.5's Model-card numbers are thinking or non-thinking; whether Ollama's qwen3.5 build supports video input (the tag only lists Image); and the true `qwen3-vl:8b` Ollama size.
- No independent Vietnamese evaluation of Qwen3.5 or Gemma 4 was found. The SEA-HELM leaderboard (18 Sep 2026) renders scores only as charts.

## Q2. Best small text LLMs for Vietnamese instruction following and JSON output

### Takeaway
Vietnamese-specific 7B models from 2024 (Vistral, SeaLLM, PhoGPT, vinallama) are now far behind general multilingual models on Vietnamese benchmarks. For text tasks (chapters, edit commands → JSON), the best options in 8 GB are the Qwen3 / Qwen3.5 family (Apache-2.0, Vietnamese explicitly supported in Qwen3) and Gemma 4 E4B (Apache-2.0). Because Qwen3.5 is natively multimodal, a single loaded model can serve both text and vision calls.

### Cited Findings
- Qwen3 dense models come in 0.6B, 1.7B, 4B, 8B, 14B and 32B under Apache-2.0. They support 119 languages and dialects, with Vietnamese listed under Austroasiatic. Thinking is controlled with `enable_thinking` or `/think` and `/no_think`. — [Qwen3 blog](https://qwenlm.github.io/blog/qwen3/)
- VMLU (Vietnamese multitask exam benchmark, Zalo AI + JAIST; ACL 2025 paper; the paper notes prompt sensitivity). — [ACL Anthology 2025.acl-long.563](https://aclanthology.org/2025.acl-long.563); [VMLU leaderboard](https://vmlu.ai/leaderboard)

  | Model | VMLU | Source |
  |---|---|---|
  | Qwen3-VL-8B-Thinking | 74.10 | Unicorn (self-reported) |
  | Qwen3-4B-Thinking-2507 | 70.00 | Unicorn (self-reported) |
  | Qwen3-8B | 69.00 | Unicorn (self-reported) |
  | gemma-2-9b-it | 59.04 | VMLU leaderboard |
  | Qwen2.5-7B-Instruct | 57.51 | VMLU leaderboard |
  | SeaLLM-7B-v2.5 | 53.30 | VMLU leaderboard |
  | Vistral-7B-Chat | 50.07 | VMLU leaderboard |
  | PhoGPT-7B5-Instruct | 24.01 | VMLU leaderboard |

  — [Unicorn-4B-R3 card](https://huggingface.co/unicorn-team/Unicorn-4B-R3); [VMLU leaderboard](https://vmlu.ai/leaderboard)
- The Unicorn team chose Qwen3-8B over Qwen3-VL-8B as their base because it handled instruction following and function calling better. On their 12-task Vietnamese LLM-as-judge set, Qwen3-8B went from 52 to 72 after a fine-tune on about 4.8k synthetic + 5k public samples; Gemini-2.5-Flash scored 84. — [Unicorn-4B-R3 card](https://huggingface.co/unicorn-team/Unicorn-4B-R3)
- Vietnamese instruction-following on SEA-HELM (older generation, from the SEA-LION paper): Gemma-2-9B-IT 78.10, Llama-3.1-8B-Instruct 75.24, Qwen2.5-7B-Instruct 69.52, SeaLLMs-v3-7B-Chat 53.33, Sailor2-8B-Chat 45.71. — [SEA-LION paper, arXiv 2504.05747](https://arxiv.org/pdf/2504.05747)
- The current SEA-HELM leaderboard (18 Sep 2026) lists SEA-LION v4.5 built on Gemma E2B (5B-A2B) and Gemma 4 E2B among models under 10B. Scores are shown only as charts. — [SEA-HELM leaderboard](https://leaderboard.sea-lion.ai/)
- Qwen3.5-4B / 9B: IFEval 89.8 / 91.5; MMMLU 76.1 / 81.2; INCLUDE 71.0 / 75.6; WMT24++ (55 languages) 66.6 / 72.6; BFCL-V4 (tool calling) 50.3 / 66.1. — [HF Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- Gemma 4 E4B: MMLU-Pro 69.4, MMMLU 76.6. Thinking is enabled by putting `<|think|>` at the start of the system prompt. — [HF gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it)

### Inferences
- **No case for a Vietnamese-specific base model.** Every Vietnamese-tuned 7B listed scores 15–50 points below Qwen3-class models on VMLU.
- **Best text-quality option in about 8 GB is `qwen3.5:9b`** (IFEval 91.5, BFCL 66.1, MMMLU 81.2).
  - Its native image input means one resident model could serve edit commands, chapters and frame checks, avoiding model swaps that risk VRAM spill.
  - Its tool-calling score (BFCL-V4) is much higher than the 4B's (66.1 vs 50.3), which is relevant to JSON edit-op generation.
- **Expect thinking to matter for B4.** The Unicorn finding (text Qwen3-8B follows instructions better than Qwen3-VL-8B) suggests a text-tuned model may beat the VL model on chaptering and JSON ops, at an extra-model VRAM cost. Test `qwen3.5:9b` with thinking off vs on (thinking adds tokens but may reduce run-to-run boundary variance; unmeasured) on the 5-minute chapter test with ≥ 6 seeded runs, per the project's measurement rule.

### Gaps
- No public VMLU or ViLLM-Eval scores were found for Qwen3.5, Gemma 4 or Gemma 3. The SEA-HELM per-model Vietnamese scores were not machine-readable.
- No Vietnamese-specific JSON or function-calling benchmark was found.
- ViLLM-Eval results were not located.

## Q3. Reliable structured output, consistency and calibrated confidence with local models

### Takeaway
1. Constrain the output with Ollama's JSON-schema `format`, put the schema in the prompt, and place a short "reasoning" field before the answer fields. Properly prompted constrained decoding does not hurt accuracy and can help.
2. Run-to-run variance comes from sampling (and, on batched servers, from batch non-invariance). For segmentation-type tasks, the evidence-backed fix is per-gap voting / ensembling rather than a single sampled run.
3. For confidence, token probabilities beat verbalized confidence on small models. They are overconfident, but one temperature parameter fitted on a labelled set fixes calibration without changing the ranking.

### Cited Findings
**Structured output**
- Ollama takes a JSON schema in `format` (Pydantic `model_json_schema()` or Zod). The docs advise also including the schema in the prompt to "ground the response" and setting temperature 0 for determinism. This works with vision models (the example uses `gemma4` with an image) and through the OpenAI-compatible `response_format`. — [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- Ollama thinking control: the `think` field takes `true`/`false` or named levels such as "low"/"medium"/"high" where the model supports them. Reasoning is returned separately in `message.thinking`. The docs do not cover how this interacts with `format`. — [Ollama thinking](https://docs.ollama.com/capabilities/thinking)
- "Let Me Speak Freely?" found that JSON-mode constraints hurt reasoning but improve classification accuracy, and that looser formats reduce variance on reasoning tasks. — [arXiv 2408.02442](https://arxiv.org/pdf/2408.02442)
- The .txt rebuttal ("Say What You Mean") traced that result to mismatched and underspecified prompts plus an LLM-based answer parser. With identical prompts and structured generation on Llama-3-8B-Instruct:

  | Task | Unstructured | Structured |
  |---|---|---|
  | GSM8K | 0.77 | 0.78 |
  | Last Letter | 0.73 | 0.77 |
  | Shuffled Objects | 0.41 | 0.44 |

  Recommended practice: a reasoning field before the answer in the schema, the schema plus an example in the prompt, and deterministic (regex) parsing. — [dottxt blog](https://blog.dottxt.ai/say-what-you-mean.html)
- JSONSchemaBench (ICML 2025, 10K real schemas) evaluates Guidance, Outlines, llama.cpp, XGrammar, OpenAI and Gemini on coverage, efficiency and quality. — [arXiv 2501.10868](https://arxiv.org/html/2501.10868v2)
  - A secondary summary (not verified against the paper) reports schema coverage on GitHub-Hard schemas of llama.cpp 39%, Guidance 41%, XGrammar 28% and Outlines 3%, and a GSM8K gain of 80.1 → 83.8 with Guidance constraints. — [beancount.io summary](https://beancount.io/bean-labs/research-logs/2026/07/08/jsonschemabench-structured-outputs-language-models)

**Consistency**
- Thinking Machines measured 80 unique outputs from 1,000 runs at temperature 0 (Qwen3-235B on vLLM). Outputs diverged at token 103.
  - The cause is a lack of batch invariance (results change with server batch size), not floating-point concurrency alone.
  - Batch-invariant kernels made all 1,000 identical, at 1.6–2.1× slower inference.
  
  — [Thinking Machines, "Defeating Nondeterminism in LLM Inference"](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)
- Ollama runs 1 parallel request per model by default (`OLLAMA_NUM_PARALLEL`). Memory scales with NUM_PARALLEL × context. — [Ollama FAQ](https://docs.ollama.com/faq)
- LLM topic segmentation framed as a yes/no decision at each sentence gap (numbered sentences, the model returns boundary indices) over overlapping windows; the paper accepts boundaries from the first half of one window and the second half of the next.
  - LLMs had higher boundary recall; SentenceBERT-similarity baselines had higher precision and were "more reliable" when boundaries are clear or inputs noisy.
  - This framing makes per-gap majority voting straightforward.
  
  — [Topic Segmentation Using Generative LMs, arXiv 2601.03276](https://arxiv.org/html/2601.03276v1)
- Self-consistency variants for outputs that are not exact-match answers do exist: ranked voting over candidates, and latent self-consistency for long answers. None was tested on segmentation. — [Ranked Voting SC, arXiv 2505.10772](https://arxiv.org/pdf/2505.10772); [Latent SC, arXiv 2508.18395](https://arxiv.org/html/2508.18395v1)

**Calibration**
- A preprint on four small open models (1,600 items, four benchmarks) found that token probability beat verbalized confidence for error detection in all four models, with bootstrap CIs excluding zero.
  - Verbalized confidence was at chance level for the 1.2B model.
  - Risk-coverage curves built from verbalized confidence were flat or backwards for 3 of the 4 models.
  
  — [Research Square rs-11018466](https://www.researchsquare.com/article/rs-11018466)
- Contrary evidence: "Asking Is Not Enough" (May 2026) found verbalized and token-based confidence near parity for 7–8B instruct models under the default protocol, and showed the comparison depends heavily on measurement choices. — [arXiv 2605.27752](https://arxiv.org/abs/2605.27752)
- Temperature scaling on general-purpose LLMs (fitted on option logprobs):
  - Fitted temperatures were T = 2.7–9.0, so the models were strongly overconfident.
  - A single scalar cut logprob ECE 2.8–6.0× across 20 splits.
  - Because the transform is monotone, accuracy, ranking and coverage-risk curves are unchanged.
  
  — [Safety-Flag, arXiv 2609.19072](https://arxiv.org/pdf/2609.19072)
- Few-shot calibration sets can be biased. The proposed alternative fits T so the aligned model matches its base model's distribution (KL), which needs no labels. — [arXiv 2310.11732](https://arxiv.org/pdf/2310.11732)
- Thermometer (ICML 2024) trains an auxiliary model to predict T for a new task without labels. — [arXiv 2403.08819](https://ar5iv.labs.arxiv.org/html/2403.08819)

### Inferences
- **For edit commands → JSON ops:**
  - Use `format` with a schema whose first property is a short `reasoning` string (or `plan`), followed by an enum-typed `op` and typed arguments.
  - Repeat the schema and 1–2 Vietnamese examples in the system prompt. Use temperature 0 with a fixed seed.
  - Enum fields mean invalid op names cannot occur, so the measurable failure becomes wrong-but-valid ops; track that on the existing command tests.
- **For B4 chapters (3–6 of 8 across seeds):** reframe as per-gap decisions over the timeline's ≥ 12 s blocks, which already exist.
  - Option (a): run K = 5–7 passes that vary window offsets and/or seeds at the recommended temperature (0.7), and keep a gap as a boundary if ≥ ⌈K/2⌉ passes mark it within ±8 s.
  - Option (b): read the logprob of a "boundary: yes/no" token at each gap (one prompt per gap or a schema with one boolean per gap). Calibrate it with one temperature fitted on the 8-boundary labelled test plus other labelled videos, then pick boundaries by threshold plus a minimum chapter length.
  - Report mean and range over ≥ 6 seeded runs, per project rule.
  - A cheap non-LLM prior is also possible: cosine similarity between adjacent blocks using a multilingual text embedding (e.g. Qwen3-Embedding-0.6B, Apache-2.0, 100+ languages — [Qwen3-Embedding listing](https://huggingface.co/furiosa-ai/Qwen3-Embedding-0.6B)). It gives precise, deterministic boundary candidates; the LLM would then vote or name them, matching the precision/recall split reported in arXiv 2601.03276.
- **For B2 re-ranking with letter logprobs:** temperature scaling will not change nDCG (monotone), but it will make the accept/reject threshold (61/62 matched, 36/46 rejected) transferable across queries. Fit T on the 10 queries × 16 shots set, with cross-validation because the set is small.
- **Determinism:** with Ollama single-slot serving (NUM_PARALLEL=1), batch-induced nondeterminism should be minor. Most observed run-to-run variance therefore likely comes from temperature > 0 sampling and prompt or long-context sensitivity, not the server. Greedy decoding fixes reproducibility but not accuracy, so pair it with voting across prompt variants.

### Gaps
- The JSONSchemaBench quality numbers per framework (llama.cpp specifically) were not verified from the paper.
- Ollama does not document whether `format` uses llama.cpp GBNF grammars, or how `format` behaves when `think: true`.
- No study of thinking vs non-thinking effects on output variance was found.
- No paper applies self-consistency voting specifically to LLM topic segmentation; the per-gap voting design above is an untested inference.
- No calibration study specific to VLM yes/no relevance logprobs was found.

## Q4. Reducing VLM image-token cost (resolution, token merging, grids, video modes, caching)

### Takeaway
In Qwen3-VL each visual token covers 32×32 px, so token count is set entirely by the pixel size the caller sends. Downscaling frames client-side before sending them to Ollama is the main lever available. Model-side reductions (InternVL3.5 ViR, MiniCPM-V 3D-Resampler, Gemma 4 budgets, VisionZip-style pruning) exist but require switching model or runtime. Image KV caching across requests is not documented for Ollama; only a shared text prefix can be reused.

### Cited Findings
- **Qwen3-VL processor settings:**
  - Compression ratio is 32, so one token = 32×32 px (patch 16 + 2×2 merge); Qwen2.5-VL uses 28 px.
  - Example per-image budget: 256–1280 tokens via `size={"longest_edge":1280*32*32,"shortest_edge":256*32*32}`. `resized_height`/`resized_width` round to multiples of 32.
  - Video: 32× spatial + 2× temporal compression. Example budget is 256–16,384 tokens per video, with `total_pixels` recommended below 24,576×32×32. A per-frame example uses `max_pixels` 360×420 at fps 1.0; default fps is 2.
  
  — [Qwen3-VL GitHub](https://github.com/QwenLM/Qwen3-VL)
- Gemma 4 exposes per-image budgets of 70, 140, 280, 560 or 1120 tokens. — [HF gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it)
- InternVL3.5 ViR / Flash uses 64 instead of 256 tokens per 448 px patch, for −50% tokens at about 100% performance. — [HF InternVL3_5-8B](https://huggingface.co/OpenGVLab/InternVL3_5-8B)
- MiniCPM-V 4.5 packs 6 frames into 64 tokens (claimed 96× vs about 1,536 tokens). — [HF MiniCPM-V-4_5](https://huggingface.co/openbmb/MiniCPM-V-4_5)
- VisionZip (CVPR 2025) keeps about 95% of LLaVA-1.5 performance with about 10% of visual tokens, training-free, with 8× faster prefill. The authors found CLIP/SigLIP tokens highly redundant. — [VisionZip, CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/html/Yang_VisionZip_Longer_is_Better_but_Not_Necessary_in_Vision_Language_CVPR_2025_paper.html); [arXiv 2412.04467](https://arxiv.org/html/2412.04467v1)
- More frames improve video QA. Qwen3.5-9B-Think gains +3.0 (with subtitles) and +5.8 (without) from 64 → 512 frames, while Qwen3-VL-8B-Think gains only +0.5 / +2.6. — [Video-MME-v2](https://arxiv.org/html/2604.05015)
- **Caching:**
  - Typical context caches reuse only the KV of an identical initial prefix; if an earlier part changes, it is recomputed.
  - One image's KV cache can reach 1 GB.
  - Prefix matching in vLLM once wrongly reused image KV because image placeholder tokens look identical; the fix is to hash image content.
  
  — [MPIC, arXiv 2502.01960](https://arxiv.org/html/2502.01960v2); [LMCache blog](https://blog.lmcache.ai/2025-07-03-multimodal-models/)
- **llama.cpp multimodal:**
  - `--no-mmproj-offload` keeps the vision projector off the GPU; it is offloaded by default.
  - Supported models listed: Qwen2/2.5-VL, Gemma 3/4, InternVL 2.5/3.
  - The page documents no image-token-budget flag.
  
  — [llama.cpp docs/multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
- **Ollama memory knobs:**
  - Default `num_ctx` is 4096.
  - `OLLAMA_KV_CACHE_TYPE=q8_0` uses about half the KV memory of f16 with "very small precision loss" (requires flash attention, which is automatic where supported). `q4_0` uses about a quarter, with more noticeable loss at long context; models with high GQA, e.g. Qwen2, may lose more.
  - `ollama ps` shows the CPU/GPU split.
  
  — [Ollama FAQ](https://docs.ollama.com/faq)

### Inferences
- **Downscale frames before sending (Qwen3-VL token arithmetic, 32 px per token):**

  | Frame size sent | Visual tokens per frame |
  |---|---|
  | 1280×720 | ≈ 40×22–23 ≈ 880–920 |
  | 640×360 | ≈ 20×11 = 220 |
  | 448×256 | ≈ 14×8 = 112 |

  - The measured prompt-read speed is 2,350 tok/s when the GPU is free. The LLM prefill for even an 880-token frame should be only about 0.4 s, so most of the ~5 s per frame is probably vision-encoder time (attention over 4× as many pre-merge patches) plus per-request overhead.
  - Shrinking to about 448–640 px should cut both. Verify on the B2 set (nDCG@6 must stay ≥ 0.915) before adopting.
- **Keep the static instruction and query text first and the image last** in each per-candidate request, so the shared prefix is reusable across candidates. Image tokens cannot be shared, so the grid result (worse) and the single-frame result (better) already bound the options.
- **Enable `OLLAMA_KV_CACHE_TYPE=q8_0`** (global). This is a low-risk way to reduce the long-context VRAM that triggers spill during chaptering and edit commands, but it should be A/B tested because Qwen models are flagged as more GQA-sensitive.
- **Replace generative re-rank with a cross-encoder reranker.** Qwen3-VL-Reranker-2B outputs a relevance score directly from (text, image or video) pairs; see Q6.

### Gaps
- No Ollama documentation on whether its qwen3-vl/qwen3.5 implementation caps image pixels by default, or on any per-request `max_pixels` option.
- Not verified whether llama.cpp has `--image-min-tokens`/`--image-max-tokens` flags.
- No measurements of Qwen3-VL accuracy vs per-frame token budget for retrieval or re-ranking tasks were found.
- No evidence was found on whether Ollama caches vision-encoder outputs for repeated images.

## Q5. Vietnamese ASR options in 2025–2026 (WER, fillers, word timestamps)

### Takeaway
On the multi-dataset Vietnamese Open ASR leaderboard, small Vietnamese-trained models beat large general ones. Among commercially usable models, the best accuracy comes from PhoWhisper-medium/large (BSD-3) and Qwen3-ASR-1.7B (Apache-2.0). NVIDIA Parakeet-CTC-0.6B-Vietnamese (commercial-OK NVIDIA Open Model License) is the only strong option with built-in word and character timestamps and Vietnamese–English code-switching. The overall best on normalized WER (Zipformer-30M) is CC-BY-NC-ND, so not usable commercially. Whisper large-v3 and especially v3-turbo trail Vietnamese-tuned models. No permissive model specifically targets Vietnamese filler words.

### Cited Findings
- **Vietnamese Open ASR leaderboard, average WER %.** O-WER is apparently on original text and N-WER on normalized text; the page does not define them. — [Vietnamese Open ASR Leaderboard](https://vietaudio-team-vietnamese-asr-leaderboard.hf.space/)

  | Model | O-WER avg | N-WER avg | Note |
  |---|---|---|---|
  | Zipformer-30M-RNNT-6000h | 17.51 | 6.77 | |
  | PhoWhisper-medium | 16.73 | 7.61 | |
  | PhoWhisper-small | 17.73 | 8.77 | |
  | Qwen3-ASR-1.7B | 18.76 | 9.84 | best viVoice N-WER 5.58 |
  | Qwen3-ASR-0.6B | 20.92 | 12.44 | |
  | parakeet-ctc-0.6b-Vietnamese | 20.78 | 12.81 | strong VIVOS/CV, viVoice outlier 27.13 |
  | wav2vec2-base-vietnamese-250h | 24.26 | 14.89 | |

  Whisper large-v3 is not on the board.
- **Whisper large-v3 on Vietnamese:**
  - GigaSpeech2 17.94, Common Voice 13.74, FLEURS 8.59 WER. — [VietASR, arXiv 2505.21527](https://arxiv.org/pdf/2505.21527)
  - FLEURS-vi at Q8_0 (third-party): large-v3 8.74, v3-turbo 9.48, Qwen3-ASR-1.7B 6.15, Qwen3-ASR-0.6B 9.32. — [Handy model comparison (vi)](https://models.handy.computer/languages/vi)
  - Code-switched Vietnamese–English: PhoWhisper-base 14.05, Qwen3-ASR-0.6B 22.47, whisper-large-v3-turbo 45.23. — [TSPC, arXiv 2509.05983](https://arxiv.org/pdf/2509.05983)
- **PhoWhisper:** BSD-3-Clause, Whisper fine-tuned on 844 h with diverse accents. — [HF vinai/PhoWhisper-large](https://huggingface.co/vinai/PhoWhisper-large) Paper WER for large: CMV-Vi 8.14, VIVOS 4.67, VLSP T1 13.75, VLSP T2 26.68. — [PhoWhisper, arXiv 2406.02555](https://arxiv.org/pdf/2406.02555)
- **Qwen3-ASR-1.7B / 0.6B:** Apache-2.0; 30 languages including Vietnamese.
  - On an aggregate including Vietnamese: FLEURS 8-language 6.62 vs Whisper-large-v3 6.85; MLC-SLM 12.74 vs 15.68.
  - Streaming works only with the vLLM backend and without timestamps. Tech report arXiv 2601.21337, Jan 2026.
  - **Qwen3-ForcedAligner-0.6B covers 11 languages, and Vietnamese is not among them.**
  
  — [HF Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
  - int4 quantization of the 0.6B raises Vietnamese WER from 9.21 to 13.00. — [HF vrfai/Qwen3-ASR-0.6B-int4](https://huggingface.co/vrfai/Qwen3-ASR-0.6B-int4)
- **NVIDIA parakeet-ctc-0.6b-Vietnamese:** NVIDIA Open Model License ("ready for commercial/non-commercial use"), FastConformer-CTC, about 600M parameters, about 2,000 h from 10 public datasets.
  - Average WER 9.30 on the blind set and 9.73 in-domain.
  - Char, word and segment timestamps are enabled by default. Output includes punctuation and capitalization (training labels generated with Qwen3).
  - Unified Vietnamese–English code-switching.
  
  — [HF nvidia/parakeet-ctc-0.6b-Vietnamese](https://huggingface.co/nvidia/parakeet-ctc-0.6b-Vietnamese)
- **Zipformer-30M-RNNT-6000h (hynt):** **CC-BY-NC-ND-4.0** (non-commercial, no derivatives). About 6,000 h; VLSP2025 public 7.97, GigaSpeech2 7.56. Runs with k2/sherpa-onnx. — [HF hynt/Zipformer-30M-RNNT-6000h](https://huggingface.co/hynt/Zipformer-30M-RNNT-6000h) The same card cites VietASR-ZipFormer-68M (70k h) at GigaSpeech2 6.88; its license was not checked.
- **CrisperWhisper (verbatim, fillers):** **CC-BY-NC-4.0**, English and German only.
  - Segmentation F1 for timestamps is 0.79 vs 0.66 for Whisper large-v3 on AMI.
  - v1 is superseded by 2.0.
  
  — [HF nyrahealth/CrisperWhisper](https://huggingface.co/nyrahealth/CrisperWhisper)
- **Gemma 4 E2B/E4B** accept up to 30 s of audio. FLEURS (aggregate) 0.09 / 0.08. Ollama does not list audio input. — [HF gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it); [Ollama gemma4](https://ollama.com/library/gemma4)
- Phi-4-multimodal audio does not include Vietnamese. — [HF Phi-4-multimodal-instruct](https://huggingface.co/microsoft/Phi-4-multimodal-instruct)

### Inferences
- **Highest-value ASR swap to test: PhoWhisper-medium or large (BSD-3)** in place of Whisper large-v3/turbo, keeping wav2vec2 forced alignment. It wins O-WER on the leaderboard and is a drop-in Whisper architecture (same tooling).
- **Parakeet-CTC-0.6B-Vietnamese is attractive for word timing.**
  - CTC gives frame-synchronous word and char timestamps without a separate aligner, plus vi-en code-switching, which matters for YouTube content.
  - Its viVoice outlier (27% WER) suggests checking it on the user's own footage.
  - A CTC model transcribes acoustically and is not trained to "clean up" like Whisper, so it may keep more fillers (ờ, à, ừm). Test it on the A4 filler test set (4/4 + held-out 4/4).
- **Qwen3-ASR-1.7B** is competitive (best on viVoice and BUD500), but its aligner lacks Vietnamese and quantization hurts Vietnamese noticeably. Keep the existing wav2vec2 alignment if adopted.
- **Do not ship Zipformer-30M or CrisperWhisper** (non-commercial licenses).

### Gaps
- No same-test-set comparison of Whisper large-v3 vs PhoWhisper-large vs Parakeet-vi vs Qwen3-ASR was found; the leaderboard lacks Whisper large-v3 and PhoWhisper-large.
- No benchmark of Vietnamese filler or disfluency transcription, or of word-timestamp accuracy for Vietnamese, was found.
- Canary and Parakeet-TDT multilingual models were not checked for Vietnamese.
- ChunkFormer and VietASR-68M licenses were not verified.

## Q6. Better text→image/video retrieval and aesthetic scoring that fit 8 GB

### Takeaway
SigLIP2 (Apache-2.0) remains a sound frame-embedding backbone, but its multilingual retrieval is not better than the older mSigLIP. The strongest new permissive option is **Qwen3-VL-Embedding-2B + Qwen3-VL-Reranker-2B** (Apache-2.0, Jan 2026, 30+ languages, text/image/video). The reranker can replace the generative Ollama re-rank with a direct relevance score. PE-Core is Apache-2.0 but English-oriented. MetaCLIP 2 and jina-clip-v2 are non-commercial.

### Cited Findings
- **SigLIP 2, XM3600 multilingual retrieval (R@1, text→image / image→text):**

  | Model | R@1 T→I / I→T |
  |---|---|
  | SigLIP 2 So/16 @256 | 48.1 / 57.5 |
  | SigLIP 2 g/16 @384 | 48.6 / 57.9 |
  | mSigLIP So/16 @256 | 50.0 / 62.8 |
  | SigLIP 2 B/16 | 40.3 / 50.7 |

  - Training mix is 90% English / 10% non-English WebLI pairs. The text tower uses the Gemma tokenizer with lowercased text at 64-token length.
  - A NaFlex variant preserves native aspect ratio.
  - So400m/16 COCO text→image is 55.4 / 56.0 / 56.0 at 256 / 384 / 512 px.
  
  — [SigLIP 2 paper, arXiv 2502.14786](https://arxiv.org/html/2502.14786)
- **Qwen3-VL-Embedding / Reranker (Apache-2.0, paper arXiv 2601.04720, Jan 2026):**

  | Model | MMEB-v2 avg | Image | Video | MMTEB |
  |---|---|---|---|---|
  | Embedding-2B | 73.4 | 74.8 | 53.6 | 68.1 |
  | Reranker-2B | 75.1 | 73.8 | 52.1 | 70.0 |
  | Reranker-8B | 79.2 | 80.7 | 55.8 | |

  - The reranker is a cross-encoder that outputs a score (sigmoid → 0–1) and accepts text, image, video and mixed inputs.
  - 30+ languages, 32K context. Custom instructions give +1–5% (written in English).
  
  — [HF Qwen3-VL-Reranker-2B](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B); [Qwen3-VL-Embedding GitHub](https://github.com/QwenLM/Qwen3-VL-Embedding)
- **Perception Encoder PE-Core (Apache-2.0):**

  | Size | ImageNet-1k | COCO T2I | MSR-VTT T2V | Kinetics-400 |
  |---|---|---|---|---|
  | B/16 224 | 78.4 | 50.9 | 47.6 | — |
  | L/14 336 | 83.5 | 57.1 | 50.3 | 73.4 |
  | G/14 448 | 85.4 | — | 51.2 | 76.9 |

  - Text context is 32 tokens; language coverage is not stated.
  - The paper claims PE-Core beats SigLIP2 on zero-shot image tasks and InternVideo2 on most zero-shot video tasks.
  
  — [HF PE-Core-L14-336](https://huggingface.co/facebook/PE-Core-L14-336); [PE paper, arXiv 2504.13181](https://arxiv.org/pdf/2504.13181)
- **MetaCLIP 2 (worldwide, NeurIPS 2025):** ViT-H/14 reaches XM3600 image→text 64.3, CVQA 57.4, Babel-ImageNet 50.2. — [arXiv 2507.22062](https://arxiv.org/abs/2507.22062v3)
  - Distilled S/M/B variants exist.
  - **The repo is "majority CC-BY-NC", so it is non-commercial.**
  
  — [MetaCLIP GitHub](https://github.com/facebookresearch/MetaCLIP)
- **jina-clip-v2:** 89 languages, **CC BY-NC 4.0**; commercial use requires a Jina license. — [Replicate listing](https://replicate.com/zsxkib/jina-clip-v2); [Jina model page](https://jina.ai/models/jina-clip-v2/)
- **Aesthetic scoring on AVA (SRCC/PLCC, aggregator figures, unverified):** Q-Align 0.822 / 0.817, OneAlign 0.823 / 0.819, LAION-style predictor retrained on AVA 0.721 / 0.723. VisualQuality-R1 (NeurIPS 2025) targets distortion quality, not aesthetics. — [Q-Align, ICML 2024](https://proceedings.mlr.press/v235/wu24ah.html); [VisualQuality-R1, arXiv 2505.14460](https://arxiv.org/pdf/2505.14460)

### Inferences
- **Best fit for the B2 second stage: Qwen3-VL-Reranker-2B.** It is a ~2B model (about 4–5 GB in bf16, inferred) that returns a direct relevance score per (Vietnamese query, frame or short clip) pair.
  - This could cut the ~5 s per frame of Ollama generative scoring, and a sigmoid score is easier to threshold than letter logprobs.
  - Its MMEB video score (52.1) is not higher than the embedding model's, so gains may be largest on images and frames.
  - Must be measured against the 0.915 nDCG@6 baseline. It would run in Python/transformers, outside Ollama, so VRAM scheduling vs Ollama is needed.
- **Qwen3-VL-Embedding-2B as a candidate first-stage replacement for SigLIP2.** It is much heavier per frame, so a better fit is re-embedding only shortlisted shots, or clip-level (video) embeddings, rather than 1 fps over hours of footage.
- **SigLIP2 vs mSigLIP for Vietnamese queries.** XM3600 suggests the older mSigLIP So/16 retrieves slightly better multilingually than SigLIP2 So/16. A quick check on the 10-query Vietnamese set would show whether to keep SigLIP2 (better English and dense features) or add mSigLIP for the text side.
- **Aesthetics (B5).** No permissive, well-benchmarked small aesthetic model was confirmed. Q-Align-class models are 7B LMM-based (heavy). Keep SigLIP2 zero-shot plus heuristics unless a licensed aesthetic head is found.

### Gaps
- No Vietnamese-specific text→image retrieval benchmark was found for any model.
- Not checked: Qwen3-VL-Embedding/Reranker GGUF or ONNX availability and measured VRAM; InternVideo2 and VideoCLIP-XL licenses; PE-Core text language coverage.
- Licenses for Q-Align, OneAlign and the LAION aesthetic predictor were not confirmed.

## Q7. Speaker diarization and audio event / sound search alternatives with clear licenses

### Takeaway
New permissive diarization options exist:
- **pyannote community-1** (CC-BY-4.0, gated download but runs offline) improves on 3.1 on most datasets.
- **Nemotron-3-Diarization** (NVIDIA, 23 Sep 2026, OpenMDW-1.1, commercial OK, ~100M, up to 8 speakers) far outperforms Sortformer v2.1. Vietnamese is not among its named languages, and its runtime targets NeMo on Linux.

DiariZen weights are non-commercial. For audio tagging, CED models beat older CNN taggers at small size, but their code is GPL-3.0. GLAP (multilingual CLAP) is promising, but its license is unconfirmed.

### Cited Findings
- **pyannote speaker-diarization-community-1:** CC-BY-4.0, "will always remain freely accessible". Download is gated by sharing contact info; once the repo is cloned it loads from a local path offline. Uses a powerset segmentation model, WeSpeaker embeddings and VBx clustering; for pyannote.audio 4.x. — [HF pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
  - DER %, no collar, overlap included:

  | Dataset | 3.1 | community-1 | precision-2 (paid) |
  |---|---|---|---|
  | AMI-SDM | 22.7 | 19.9 | 15.6 |
  | DIHARD3 | 21.4 | 20.2 | 14.7 |
  | AliMeeting | 24.5 | 20.3 | 15.2 |
  | MSDWild | 25.4 | 22.8 | 17.3 |
  | VoxConverse | 11.2 | 11.2 | 8.5 |
  | CALLHOME | 28.5 | 26.7 | 16.6 |

- **NVIDIA streaming Sortformer 4spk-v2:** CC-BY-4.0, 117M, at most 4 speakers, trained primarily on English. DIHARD III 19.68 (≤ 4 speakers 14.63; ≥ 5 speakers 40.74), CALLHOME-p2 10.50 (9.54 with post-processing). — [HF diar_streaming_sortformer_4spk-v2](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2) A developer note says the offline v1 Sortformer is CC-BY-NC. — [haberwooki diarization notes](https://raw.githubusercontent.com/haberwooki/dia_live_captions/main/docs/diarization-research.md)
- **Nemotron-3-Diarization:** OpenMDW-1.1 ("ready for commercial or non-commercial use"), about 99M parameters, up to 8 speakers. — [HF nvidia/Nemotron-3-Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization)
  - Languages named: English, Mandarin, Hindi, Kannada, Telugu, Bengali, plus others in the multilingual sources.
  - DER vs Sortformer v2.1: DIHARD III 12.73 vs 19.09; AMI-SDM 11.14 vs 21.42; AliMeeting far 10.47 vs 13.69; CALLHOME-p2 9.10 vs 10.32.
  - Runtimes: NeMo, NeMo-Speech.cpp (GGUF) and HF Transformers (from source). Listed hardware is Linux NVIDIA GPUs.
  - The gated "-preview" checkpoint is evaluation-only. — [Runtimewire](https://runtimewire.com/article/nvidia-nemotron-diarization-eight-speakers-open-weights)
- **DiariZen:** code MIT, weights **CC-BY-NC-4.0**. DER AMI-SDM 13.9, DIHARD3 14.5, VoxConverse 9.1. Slowest in independent tests (RTF ~20×). — [Kili guide 2026](https://kili-technology.com/blog/speaker-diarization-models-guide-benchmarks-and-failure-modes-2026); [Benchmarking Diarization Models, arXiv 2509.26177](https://arxiv.org/html/2509.26177v1)
- **CED audio taggers (Xiaomi):** — [CED GitHub](https://github.com/RicherMans/CED); [CED paper, arXiv 2308.11957](https://arxiv.org/pdf/2308.11957)

  | Model | Params | AS-2M mAP |
  |---|---|---|
  | Tiny | 5.5M | 48.1 |
  | Mini | 9.6M | 49.0 |
  | Small | 22M | 49.6 |
  | Base | 86M | 50.0 |

  - Weights are on HF (`mispeech/ced-*`) with ONNX export.
  - **Repo code is GPL-3.0**; the weights' license is not stated there.
- **GLAP (Xiaomi, 2025):** contrastive audio–text model covering sound events, music and speech across 145 languages, with keyword spotting evaluated in 50 languages. — [GLAP, arXiv 2506.11350](https://arxiv.org/html/2506.11350v2) The license shows only an "MIT" badge on a third-party mirror, so it is unconfirmed. — [GLAP HF mirror](https://huggingface-gw-kz.just-ai.com/mispeech/GLAP)

### Inferences
- **Current C3 pipeline.** It already achieves 3/3, 2/2 and 1/1 correct speaker counts on the test set, so a swap is only worthwhile for overlapped speech or more than 3 speakers.
  - pyannote community-1 is the easiest permissive upgrade path: CC-BY-4.0 with attribution; the gate is a one-time download, then bundle offline, provided redistribution with attribution is acceptable under CC-BY-4.0.
  - Nemotron-3-Diarization has the best published DERs and a permissive license, but the Windows/NeMo deployment and lack of Vietnamese evaluation make it a test-first candidate. Its GGUF/NeMo-Speech.cpp path may avoid the Python NeMo dependency.
- **Sound search (C2).** CED-Mini or Small would likely improve tag accuracy over PANNs Cnn14 at lower compute. Cnn14 is commonly reported at about 0.431 mAP in the PANNs paper, not re-verified in this session. But CED's GPL-3.0 code means reimplementing inference from the ONNX weights, and the weights' license must be confirmed first.
- **GLAP** could replace the dictionary + SigLIP2 mapping of Vietnamese queries to AudioSet labels with direct multilingual text→audio search, if its MIT license is confirmed.

### Gaps
- No diarization benchmark on Vietnamese speech was found for any system.
- Not verified: OpenMDW-1.1 terms for redistribution inside a commercial app, and whether Nemotron-3-Diarization runs on Windows.
- Not confirmed: CED and GLAP weight licenses; BEATs weight license; M2D-CLAP; a permissive CLAP with clear training-data provenance.

"""Preset profiles for video genre optimization in Justin EDIT Suite.

Defines adaptive processing rules per genre:
  - skip_shake_roles: roles that skip camera shake / stability scan (e.g. A-roll on tripod)
  - skip_transcribe_roles: roles that skip Whisper transcription unless voice RMS > min_speech_rms_db
  - silence: gap, pad_in, pad_out, min_piece
  - punch_in & punch_in_scale
  - bgm_ducking_db & broll_nat_gain_db
  - seq settings & transitions
  - mode: "script" (default, nxcore.run) | "beat" / "vo" / "story" (nxcore.montage — no script needed)
"""
import json
import os

PRESETS = {
    "talking_head": {
        "id": "talking_head",
        "name": "Talking Head / Video Chuyên gia / Podcast",
        "description": "Tối ưu cho video chia sẻ một người, máy đặt tripod. Nhịp cắt dồn dập, tự punch-in 112%, ducking sâu -22dB.",
        "script_structure": {
            "hook": "0:00-0:10 (hook thẳng mặt, chặn ngón tay 3s đầu, không chào hỏi)",
            "positioning": "0:10-0:20 (định vị + hứa hẹn: cho ai, được gì, bao lâu)",
            "body": "0:20-75% (thân bài chia 2-4 luận điểm: ý -> giải thích -> ví dụ -> chốt)",
            "climax": "75%-90% (điểm nhấn cảm xúc mạnh nhất, chậm lại có chủ đích, cận mặt + dừng lời)",
            "outro": "90%-100% (chốt đọng lại + CTA rõ ràng kèm text trên màn hình)"
        },
        "silence": {
            "on": True,
            "gap": 0.25,
            "pad_in": 0.06,
            "pad_out": 0.08,
            "min_piece": 0.25
        },
        "punch_in": True,
        "punch_in_scale": 112.0,
        "bgm_ducking_db": -22.0,
        "broll_nat_gain_db": -20.0,
        "broll_dur": 3.0,
        "broll_dur_static": 2.5,
        "sfx_density": "light",
        "skip_shake_roles": ["a_roll", "screen"],
        "skip_transcribe_roles": ["b_roll", "junk"],
        "min_speech_rms_db": -32.0,
        "subs": {"on": True, "max_chars": 26, "highlight_style": "yellow"},
        "transitions": {"on": False}
    },
    "interview_casting": {
        "id": "interview_casting",
        "name": "Phỏng vấn / Casting / Reality Show",
        "description": "Tối ưu cho phỏng vấn 2 máy hoặc casting nhiều take. Bắt cặp hỏi-đáp, bỏ qua quét rung trên A-Roll tripod.",
        "script_structure": {
            "hook": "0:00-0:10 (trích câu thoại/cảm xúc kịch tính nhất từ diễn xuất)",
            "intro": "0:10-0:30 (giới thiệu ứng viên/nhân vật, vị trí ứng tuyển)",
            "body": "0:30-70% (bắt cặp Hỏi - Đáp liên tục, các câu hỏi chuyên môn/thách thức)",
            "climax": "70%-90% (thử thách diễn xuất/tình huống khó nhất, take diễn đỉnh nhất)",
            "outro": "90%-100% (nhận xét giám khảo, quyết định nhận/loại + CTA)"
        },
        "silence": {
            "on": True,
            "gap": 0.40,
            "pad_in": 0.10,
            "pad_out": 0.15,
            "min_piece": 0.35
        },
        "punch_in": False,
        "multicam_switch_s": 6.0,
        "bgm_ducking_db": -20.0,
        "broll_nat_gain_db": -15.0,
        "broll_dur": 3.0,
        "sfx_density": "minimal",
        "skip_shake_roles": ["a_roll", "screen"],
        "skip_transcribe_roles": ["b_roll", "junk"],
        "min_speech_rms_db": -32.0,
        "subs": {"on": True, "max_chars": 30, "highlight_style": "gold"},
        "transitions": {"on": False}
    },
    "vlog_travel": {
        "id": "vlog_travel",
        "name": "Vlog / Du lịch / Lifestyle",
        "description": "Tối ưu cho nhịp điệu trải nghiệm. B-roll theo nhịp 3-5s, tự slow-mo 50%, giữ âm thanh môi trường -9dB, quét rung đầy đủ cho B-roll.",
        "script_structure": {
            "hook": "0:00-0:15 (chặn ngón tay 3s đầu, trích câu kịch tính/khoảnh khắc gây sốc từ giữa video)",
            "intro": "0:15-0:40 (bối cảnh, mục tiêu hôm nay, lý do nên ở lại xem)",
            "body": "0:40-70% (3-5 chặng nhỏ có tiến triển: Mở -> Diễn biến -> Chốt)",
            "climax": "70%-90% (khoảnh khắc đắt giá nhất, dồn B-roll đẹp & nhạc cao trào, ít thoại)",
            "outro": "90%-100% (khép lại vòng tròn với hook, đúc kết bài học/kết quả + CTA)"
        },
        "silence": {
            "on": True,
            "gap": 0.45,
            "pad_in": 0.10,
            "pad_out": 0.12,
            "min_piece": 0.45
        },
        "punch_in": False,
        "auto_slowmo_60p": True,
        "slowmo_speed": 50,
        "bgm_ducking_db": -16.0,
        "broll_nat_gain_db": -9.0,
        "broll_dur": 3.5,
        "broll_dur_static": 3.0,
        "sfx_density": "medium",
        "skip_shake_roles": [],
        "skip_transcribe_roles": ["b_roll", "junk"],
        "min_speech_rms_db": -32.0,
        "subs": {"on": False},
        "transitions": {"on": True, "dur": 0.6, "broll_rate": 0.3}
    },
    "shortform_9x16": {
        "id": "shortform_9x16",
        "name": "Short-form (TikTok / Reels / Shorts 9:16)",
        "description": "Tối ưu khung dọc 9:16, nhịp siêu nhanh, cắt im lặng cực gắt 0.18s, punch-in 115%, SFX dồn dập, kinetic text.",
        "script_structure": {
            "hook": "0:00-0:03 (chặn ngón tay 3s đầu, hình + text + thoại gây tò mò/sốc ngay)",
            "retention": "0:03-60% (giữ chân: duy trì nợ chưa trả, hứa hẹn điều sắp tới, không lộ kết quả sớm)",
            "payoff": "60%-85% (trả nợ lời hứa: giao đúng thứ hook đã hứa - câu trả lời, cú twist, mẹo)",
            "loop": "85%-95% (chốt vòng lặp loop: câu/cảnh nối liền về đầu video để kích thích xem lại)",
            "cta": "95%-100% (CTA nhẹ: 1 lời mời duy nhất - lưu/follow/xem tiếp)"
        },
        "seq": {"w": 1080, "h": 1920, "fps": 29.97, "fit": "fill"},
        "silence": {
            "on": True,
            "gap": 0.18,
            "pad_in": 0.04,
            "pad_out": 0.06,
            "min_piece": 0.20
        },
        "punch_in": True,
        "punch_in_scale": 115.0,
        "bgm_ducking_db": -18.0,
        "broll_nat_gain_db": -18.0,
        "broll_dur": 1.8,
        "broll_dur_static": 1.5,
        "sfx_density": "heavy",
        "skip_shake_roles": ["screen"],
        "skip_transcribe_roles": ["b_roll", "junk"],
        "min_speech_rms_db": -32.0,
        "subs": {"on": True, "max_chars": 18, "highlight_style": "yellow"},
        "transitions": {"on": False}
    },
    "event_recap": {
        "id": "event_recap",
        "name": "Event / Recap Sự kiện / Lễ hội",
        "description": "Video sự kiện dựng theo nhạc: tắt Whisper (tiết kiệm GPU), montage cắt đúng beat theo trình tự giờ quay, giữ tiếng không khí -14dB, đoạn ổn định nhất. Chọn nhạc rồi Align; chưa có nhạc → lưới 120 BPM.",
        "script_structure": {
            "hook": "0:00-0:08 (cú đập thị giác, cảnh đắt nhất/đông nhất/cảm xúc nhất đưa lên đầu, không logo)",
            "positioning": "0:08-0:20 (định vị: cái gì - ở đâu - khi nào - quy mô bằng text ≤ 6 từ)",
            "body": "0:20-65% (thân bài theo thời gian thực: chuẩn bị -> khai mạc -> cao điểm hoạt động)",
            "climax": "65%-85% (đỉnh cảm xúc: pháo hoa/vỗ tay/trao giải, nhạc dâng, cắt nhanh 1-2s theo beat)",
            "outro": "85%-100% (hạ nhịp slow-mo, câu chốt cảm xúc/con số ấn tượng, logo BTC + CTA)"
        },
        "disable_transcribe": True,
        "music_driven": True,
        "mode": "beat", "target_dur": 90, "pace": 0.9, "min_stab": 0.45, "w_stab": 0.45, "w_quality": 0.25,
        "w_motion": 0.15, "w_speech": 0.3, "w_chrono": 0.25, "chrono": "auto", "speech_moments": 0,
        "nat_sound": True, "xfade_slow": False, "music_gain_db": -4.0, "skip_roles": ["junk", "audio_master", "screen"],
        "silence": {"on": False},
        "punch_in": False,
        "auto_slowmo_60p": True,
        "slowmo_speed": 50,
        "bgm_ducking_db": 0.0,
        "broll_nat_gain_db": -14.0,
        "broll_dur": 2.0,
        "broll_dur_static": 1.8,
        "sfx_density": "medium",
        "skip_shake_roles": [],
        "skip_transcribe_roles": ["a_roll", "b_roll", "junk"],
        "min_speech_rms_db": -32.0,
        "subs": {"on": False},
        "transitions": {"on": True, "dur": 0.5, "broll_rate": 0.4}
    },
    "beat_cut": {
        "id": "beat_cut",
        "name": "Cut on beat — chỉ nhạc (không thoại, không tiếng môi trường)",
        "description": "Montage cắt đúng beat: không thoại, tắt tiếng gốc, chỉ lấy đoạn ổn định nhất (tripod/gimbal). Intro/break shot dài, build tăng tốc, drop cắt 1–2 beat; cắt đổi đoạn nhạc luôn trùng đầu ô nhịp. Cần chọn nhạc.",
        "mode": "beat",
        "music_driven": True,
        "disable_transcribe": True,
        "structure": {
            "intro": "shot dài 4–8 beat, cảnh mở/toàn, có thể slow-mo",
            "build": "tăng tốc 4 → 2 beat",
            "drop": "1–2 beat, cảnh chuyển động/đẹp nhất",
            "break": "chậm lại, slow-mo",
            "outro": "shot dài, khép lại"
        },
        "target_dur": 0,
        "pace": 1.0,
        "min_shot": 0.4,
        "max_shot": 4.5,
        "min_stab": 0.6,
        "w_stab": 0.6,
        "w_quality": 0.2,
        "w_motion": 0.1,
        "w_speech": 0.6,
        "w_chrono": 0.0,
        "chrono": "auto",
        "speech_moments": 0,
        "nat_sound": False,
        "music_gain_db": -3.0,
        "music_fade_out": 1.5,
        "xfade_slow": False,
        "auto_slowmo_60p": True,
        "slowmo_speed": 50,
        "silence": {
            "on": False
        },
        "punch_in": False,
        "sfx_density": "none",
        "skip_shake_roles": [],
        "skip_transcribe_roles": [
            "a_roll",
            "b_roll",
            "junk"
        ],
        "skip_roles": [
            "junk",
            "audio_master",
            "screen"
        ],
        "min_speech_rms_db": -32.0,
        "subs": {
            "on": False
        },
        "transitions": {
            "on": True,
            "dur": 0.4,
            "scene": False,
            "broll_rate": 0,
            "fade_in": 0.3,
            "fade_out": 1.2,
            "audio": False
        }
    },
    "wedding_highlight": {
        "id": "wedding_highlight",
        "name": "Wedding Highlight 3–5 phút (theo nhạc + lời thề)",
        "description": "Highlight cưới theo nhạc: shot chậm có slow-mo ở intro/break, nhanh ở cao trào, đi theo giờ quay (chuẩn bị → lễ → tiệc). Tự chọn 2–4 câu nói cảm xúc (lời thề, phát biểu) đặt lên nhạc, nhạc tự duck, shot đầu là người nói khớp tiếng.",
        "mode": "beat",
        "music_driven": True,
        "script_structure": {
            "cold_open": "0:00-0:20 (câu lời thề/phát biểu cảm xúc nhất trên nhạc nhẹ, chi tiết nhẫn/váy/tay, slow-mo)",
            "getting_ready": "0:20-25% (chuẩn bị: cô dâu make-up, chú rể, chi tiết, gia đình — nhịp chậm)",
            "first_look_ceremony": "25%-55% (first look, rước dâu, nghi lễ, trao nhẫn — câu thề thứ 2 trên nhạc)",
            "celebration": "55%-85% (tiệc, khách, nhảy, nâng ly — nhạc cao trào, cắt nhanh theo beat)",
            "ending": "85%-100% (câu nói chốt, cảnh hai người, hoàng hôn/pháo, slow-mo, fade)"
        },
        "target_dur": 240,
        "pace": 1.35,
        "min_shot": 0.8,
        "max_shot": 6.0,
        "min_stab": 0.5,
        "w_stab": 0.45,
        "w_quality": 0.3,
        "w_motion": 0.08,
        "w_speech": 0.3,
        "w_chrono": 0.35,
        "chrono": "auto",
        "speech_moments": 3,
        "speaker_shot_s": 3.5,
        "duck_db": -14.0,
        "music_gain_db": -4.0,
        "music_fade_out": 2.5,
        "xfade_slow": True,
        "nat_sound": False,
        "auto_slowmo_60p": True,
        "slowmo_speed": 50,
        "silence": {
            "on": False
        },
        "punch_in": False,
        "sfx_density": "none",
        "skip_shake_roles": [],
        "skip_transcribe_roles": [
            "b_roll",
            "junk"
        ],
        "skip_roles": [
            "junk",
            "audio_master",
            "screen"
        ],
        "min_speech_rms_db": -32.0,
        "subs": {
            "on": False,
            "max_chars": 34
        },
        "transitions": {
            "on": True,
            "dur": 0.8,
            "scene": False,
            "broll_rate": 0,
            "fade_in": 1.0,
            "fade_out": 2.0,
            "audio": True
        }
    },
    "wedding_teaser": {
        "id": "wedding_teaser",
        "name": "Wedding Teaser 60s dọc 9:16 (theo nhạc)",
        "description": "Teaser cưới 60s khung dọc: những khoảnh khắc đẹp nhất, nhịp nhanh hơn highlight, 1 câu nói mở đầu trên nhạc, cắt đúng beat, kết slow-mo.",
        "mode": "beat",
        "music_driven": True,
        "script_structure": {
            "hook": "0:00-0:05 (khoảnh khắc đắt nhất + 1 câu nói)",
            "build": "0:05-0:35 (chi tiết, lễ, cảm xúc — cắt theo beat)",
            "peak": "0:35-0:52 (cao trào: nụ hôn, tung hoa, nhảy)",
            "end": "0:52-1:00 (hai người, slow-mo, tên + ngày)"
        },
        "seq": {
            "w": 1080,
            "h": 1920,
            "fps": 29.97,
            "fit": "fill"
        },
        "target_dur": 60,
        "pace": 0.85,
        "min_shot": 0.5,
        "max_shot": 3.0,
        "min_stab": 0.5,
        "w_stab": 0.45,
        "w_quality": 0.35,
        "w_motion": 0.1,
        "w_speech": 0.3,
        "w_chrono": 0.1,
        "chrono": "auto",
        "speech_moments": 1,
        "speaker_shot_s": 2.5,
        "duck_db": -14.0,
        "music_gain_db": -3.0,
        "music_fade_out": 1.5,
        "xfade_slow": False,
        "nat_sound": False,
        "auto_slowmo_60p": True,
        "slowmo_speed": 50,
        "silence": {
            "on": False
        },
        "punch_in": False,
        "sfx_density": "light",
        "skip_shake_roles": [],
        "skip_transcribe_roles": [
            "b_roll",
            "junk"
        ],
        "skip_roles": [
            "junk",
            "audio_master",
            "screen"
        ],
        "min_speech_rms_db": -32.0,
        "subs": {
            "on": False,
            "max_chars": 20
        },
        "transitions": {
            "on": True,
            "dur": 0.4,
            "scene": False,
            "broll_rate": 0,
            "fade_in": 0.3,
            "fade_out": 1.2,
            "audio": True
        }
    },
    "wedding_full": {
        "id": "wedding_full",
        "name": "Wedding Full Film 15–40 phút (theo giờ quay)",
        "description": "Phim cưới đầy đủ theo trình tự giờ quay: giữ trọn lời thề, phát biểu, nghi lễ (bỏ trùng khi quay 2 máy), cầu nối B-roll ổn định nhất giữa các đoạn nói, cắt im lặng nhẹ, dissolve khi đổi cảnh. Mặc định 25 phút.",
        "mode": "story",
        "script_structure": {
            "prologue": "0:00-2:00 (montage mở đầu + câu nói hay nhất)",
            "getting_ready": "chuẩn bị hai bên gia đình",
            "ceremony": "rước dâu, lễ gia tiên, lễ đường — giữ trọn lời thề, trao nhẫn",
            "reception": "tiệc: phát biểu, nâng ly, nhảy",
            "epilogue": "kết: hai người + cảm ơn, credit"
        },
        "target_dur": 1500,
        "pace": 1.0,
        "story_shot_s": 3.5,
        "story_speech_share": 0.65,
        "story_scene_gap_min": 20.0,
        "story_bridge_shots": 3,
        "min_stab": 0.45,
        "w_stab": 0.45,
        "w_quality": 0.3,
        "w_motion": 0.05,
        "w_speech": 0.2,
        "chrono": "auto",
        "nat_sound": True,
        "broll_nat_gain_db": -12.0,
        "auto_slowmo_60p": True,
        "slowmo_speed": 50,
        "silence": {
            "on": True,
            "gap": 0.8,
            "pad_in": 0.15,
            "pad_out": 0.25,
            "min_piece": 0.8
        },
        "punch_in": False,
        "bgm_ducking_db": -20.0,
        "sfx_density": "none",
        "skip_shake_roles": [],
        "skip_transcribe_roles": [
            "junk"
        ],
        "skip_roles": [
            "junk",
            "audio_master",
            "screen"
        ],
        "min_speech_rms_db": -34.0,
        "subs": {
            "on": False,
            "max_chars": 36
        },
        "transitions": {
            "on": True,
            "dur": 1.0,
            "scene": True,
            "broll_rate": 0.25,
            "fade_in": 1.5,
            "fade_out": 2.5,
            "audio": True
        }
    },
    "explainer_vo": {
        "id": "explainer_vo",
        "name": "Explainer — VO + B-roll",
        "description": "Video giải thích: voice-over thu sẵn (hoặc VoiceStudio tạo) → tự transcribe, chia câu, mỗi câu một B-roll khớp nội dung (AI mô tả × độ ổn định), cắt đúng đầu câu, phụ đề từ VO, nhạc nền tự duck dưới giọng.",
        "mode": "vo",
        "script_structure": {
            "hook": "0:00-0:08 (câu hỏi/vấn đề người xem đang gặp)",
            "promise": "0:08-0:20 (video này giải thích gì, trong bao lâu)",
            "explain": "0:20-80% (2-4 ý: khái niệm → ví dụ hình ảnh → hệ quả; mỗi câu VO = 1 hình)",
            "recap": "80%-92% (tóm tắt 3 ý bằng text)",
            "cta": "92%-100% (hành động tiếp theo)"
        },
        "vo_shot_max": 4.5,
        "vo_shot_min": 1.5,
        "vo_lead": 0.5,
        "vo_tail": 1.2,
        "vo_music_db": -22.0,
        "music_gain_db": -6.0,
        "min_stab": 0.4,
        "w_stab": 0.4,
        "w_quality": 0.3,
        "w_motion": 0.05,
        "w_speech": 0.5,
        "nat_sound": False,
        "auto_slowmo_60p": True,
        "slowmo_speed": 50,
        "broll_min_score": 0.12,
        "silence": {
            "on": False
        },
        "punch_in": False,
        "bgm_ducking_db": -22.0,
        "sfx_density": "light",
        "skip_shake_roles": [
            "screen"
        ],
        "skip_transcribe_roles": [
            "b_roll",
            "junk"
        ],
        "skip_roles": [
            "junk",
            "audio_master"
        ],
        "min_speech_rms_db": -32.0,
        "subs": {
            "on": True,
            "max_chars": 32,
            "highlight_style": "yellow"
        },
        "transitions": {
            "on": True,
            "dur": 0.4,
            "scene": False,
            "broll_rate": 0,
            "fade_in": 0.4,
            "fade_out": 1.0,
            "audio": True
        }
    },
    "youtube_long": {
        "id": "youtube_long",
        "name": "YouTube dài 8–30 phút (chương + giữ chân)",
        "description": "YouTube dài talking head + demo: hook ≤ 25s, chương có tag 'n/N' và thẻ chương + whoosh, nói trước máy ≤ 9 shot liền rồi chen hình minh hoạ (~40% thời lượng), cắt sát hơi thở (0.28s), punch-in luân phiên, caption từ khoá, nhạc nền -18…-20 dB dưới giọng, tự xuất chương YouTube.",
        "mode": "script",
        "chapters": True,
        "script_structure": {
            "cold_open": "0:00-0:25 (câu tín nhiệm có con số + 3-5 shot minh hoạ nhanh 1-2s + lời hứa 'xem hết được gì'; không chào hỏi)",
            "intro": "0:25-0:45 (khung N chương/cấp độ, nói rõ người xem đang ở đâu và sẽ lên tới đâu)",
            "chapters": "0:45-90% (3-7 chương cùng một khuôn: thẻ chương → định nghĩa → 2 pro tip / ví dụ → demo màn hình → câu nối sang chương sau; CTA giữa video ở ~25-30%)",
            "climax": "90%-95% (chương cao nhất: kết quả lớn nhất + quan điểm riêng, có 1 khoảng lặng nhạc trước câu chốt)",
            "outro": "95%-100% (câu hỏi cam kết để comment + CTA + end screen 20s)"
        },
        "silence": {
            "on": True,
            "gap": 0.28,
            "pad_in": 0.06,
            "pad_out": 0.1,
            "min_piece": 0.3
        },
        "punch_in": True,
        "punch_in_scale": 112.0,
        "bgm_ducking_db": -20.0,
        "broll_nat_gain_db": -16.0,
        "broll_dur": 4.0,
        "broll_dur_static": 3.5,
        "broll_dur_max": 6.0,
        "sfx_density": "medium",
        "skip_shake_roles": [
            "a_roll",
            "screen"
        ],
        "skip_transcribe_roles": [
            "b_roll",
            "junk"
        ],
        "min_speech_rms_db": -32.0,
        "subs": {
            "on": True,
            "max_chars": 42,
            "highlight_style": "yellow"
        },
        "transitions": {
            "on": True,
            "dur": 0.4,
            "scene": False,
            "broll_rate": 0.05,
            "fade_in": 0,
            "fade_out": 1.0,
            "audio": True
        },
        "style_guide": {
            "pacing": "ASL 4-5s (trung vị ~4s), 12-16 cắt/phút đều suốt video, không shot nào > 20s; mật độ cắt cao nhất ở hook.",
            "a_roll": "2 góc máy (trung cảnh chính diện + góc nghiêng rộng), đổi góc mỗi ý mới để che jump cut; tối đa ~9 shot nói trước máy liền nhau (~30-40s) rồi chen hình minh hoạ.",
            "broll": "Hình minh hoạ ~40-45% thời lượng: screen record trong khung tối bo góc (không full màn hình), PiP người nói khi cần thấy mặt, B-roll đời thực làm bằng chứng, B-roll AI chỉ cho ẩn dụ; mỗi hình 3-6s, giọng nói chạy liên tục bên dưới.",
            "graphics": "Tag chương cố định góc trên trái 'Chương n/N · Tên'; thẻ chương full khung 2,5-3s; thẻ bước có số ở góc dưới; nhãn PRO TIP; một bảng màu + font cho toàn kênh.",
            "captions": "Caption theo TỪ KHOÁ 2-5 chữ (không phụ đề cả câu trên hình chính), sans đậm trắng; con số / từ khoá dùng serif italic màu nhấn; ≤ 1 lần mỗi 8-10s, chỉ câu đắt.",
            "sound": "Giọng ~-14 LUFS, đỉnh ≤ -1 dBTP; nhạc nền chậm 70-90 BPM thấp hơn giọng 18-20 dB, duck tự động, không cắt theo beat; SFX: hit cuối hook, whoosh/riser 1,5-2s ở thẻ chương, click/gõ phím khi demo, pop nhẹ khi thẻ bước hiện; không SFX cho mọi caption.",
            "cta": "CTA mềm giữa video (~25-30%, tài liệu/quà) + cuối video (câu hỏi cam kết để comment + video tiếp theo)."
        },
        "benchmarks": [
            {
                "ref": "Dan Martell — 1000 hours of Learning Claude in 15 Minutes (phân tích Reverse 08/10/2026)",
                "dur_s": 897,
                "shots": 205,
                "asl_s": 4.4,
                "cuts_per_min": "12-16",
                "talking_head_pct": 56,
                "illustration_pct": 44,
                "max_talking_run": 9,
                "speech_pct": 96,
                "music_bpm": 72,
                "music_under_voice_db": -19
            }
        ]
    },
    "documentary": {
        "id": "documentary",
        "name": "Phóng sự / Documentary",
        "description": "Phóng sự theo kịch bản: VO dẫn + phỏng vấn nhân vật + B-roll quan sát giữ tiếng hiện trường (-12dB), J/L-cut dài cho thoại, không slow-mo tự động, cắt im lặng nhẹ giữ nhịp thở, dissolve khi đổi cảnh.",
        "mode": "script",
        "script_structure": {
            "cold_open": "0:00-0:30 (cảnh hiện trường có tiếng thật, chưa VO — kéo người xem vào bối cảnh)",
            "context": "0:30-15% (VO dẫn: ai, ở đâu, chuyện gì, vì sao quan trọng)",
            "characters": "15%-60% (nhân vật kể — phỏng vấn xen B-roll quan sát, mỗi ý 1 cảnh minh hoạ)",
            "turning_point": "60%-80% (mâu thuẫn/khó khăn/bước ngoặt, nhịp chậm lại)",
            "resolution": "80%-95% (kết quả, câu nói đọng lại của nhân vật)",
            "closing": "95%-100% (hình ảnh khép lại + thông tin/CTA)"
        },
        "silence": {
            "on": True,
            "gap": 0.5,
            "pad_in": 0.12,
            "pad_out": 0.2,
            "min_piece": 0.5
        },
        "punch_in": False,
        "auto_slowmo_60p": False,
        "bgm_ducking_db": -24.0,
        "broll_nat_gain_db": -12.0,
        "broll_dur": 4.5,
        "broll_dur_static": 4.0,
        "broll_dur_max": 7.0,
        "sfx_density": "minimal",
        "j_l_cut": True,
        "l_cut_s": 0.6,
        "j_cut_s": 0.5,
        "chrono": "auto",
        "skip_shake_roles": [
            "a_roll"
        ],
        "skip_transcribe_roles": [
            "junk"
        ],
        "min_speech_rms_db": -34.0,
        "subs": {
            "on": True,
            "max_chars": 38,
            "highlight_style": "gold"
        },
        "transitions": {
            "on": True,
            "dur": 0.8,
            "scene": True,
            "broll_rate": 0.15,
            "fade_in": 1.0,
            "fade_out": 1.5,
            "audio": True
        }
    }
}


def presets_dir():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(here, "presets")
    os.makedirs(p, exist_ok=True)
    return p


def export_presets_json():
    """Ensure all presets exist as individual editable JSON files."""
    d = presets_dir()
    for pid, data in PRESETS.items():
        fp = os.path.join(d, f"{pid}.json")
        if not os.path.exists(fp):
            json.dump(data, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def get_preset(name_or_id):
    """Retrieve preset by ID or load from presets/<name>.json."""
    if not name_or_id:
        return PRESETS["talking_head"]
    key = str(name_or_id).lower().strip()
    if key in PRESETS:
        # Check if user customized the JSON file in presets/
        fp = os.path.join(presets_dir(), f"{key}.json")
        if os.path.exists(fp):
            try:
                return json.load(open(fp, encoding="utf-8"))
            except Exception:
                pass
        return PRESETS[key]

    # Try custom file path
    if os.path.exists(name_or_id):
        try:
            return json.load(open(name_or_id, encoding="utf-8"))
        except Exception:
            pass

    return PRESETS.get(key, PRESETS["talking_head"])


# Auto-export standard JSON files on import
export_presets_json()

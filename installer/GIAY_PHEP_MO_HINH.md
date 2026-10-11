# Mô hình AI dùng trong Justin EDIT Suite — giấy phép & ghi công

Các mô hình được tải về máy khi cài gói AI / bấm "Tải trước model"; tool không gửi footage lên máy chủ nào.

| Dùng cho | Mô hình | Giấy phép | Ghi công |
|---|---|---|---|
| Tìm footage theo âm thanh | PANNs Cnn14_16k (Zenodo 3987831) | Trọng số **CC-BY-4.0**, code MIT | Kong, Cao, Iqbal, Wang, Wang, Plumbley — *PANNs: Large-Scale Pretrained Audio Neural Networks for Audio Pattern Recognition*, IEEE/ACM TASLP 2020 |
| Tên 527 loại âm thanh | AudioSet ontology (Google) | **CC-BY-SA-4.0** | Gemmeke et al. — *Audio Set: An ontology and human-labeled dataset for audio events*, ICASSP 2017 |
| Nhận diện người nói | WeSpeaker ResNet34-LM (VoxCeleb) | **CC-BY-4.0** | Wang et al. — *WeSpeaker: A Research and Production oriented Speaker Embedding Learning Toolkit*, ICASSP 2023; dữ liệu VoxCeleb (Nagrani, Chung, Zisserman) |
| Tìm footage theo hình, chấm đẹp / nét | SigLIP2 base patch16-224 (Google) | Apache-2.0 | Tschannen et al. — *SigLIP 2*, 2025 |
| Căn thời gian từng từ | wav2vec2-base-vietnamese (dragonSwing) | Apache-2.0 | |
| Nhận dạng giọng nói | Whisper large-v3-turbo (OpenAI) · EraX-WoW-Turbo | MIT | |
| Bắt beat | beat_this (CPJKU) | MIT | Foscarin, Schlüter, Widmer — *Beat this!*, ISMIR 2024 |
| Tách cảnh video đã dựng | TransNetV2 | MIT | Souček, Lokoč — *TransNet V2*, 2020 |
| Mô tả cảnh, xem kỹ, chia chương | Qwen3-VL 8B (qua Ollama, chạy trên máy) | Apache-2.0 | Qwen Team, Alibaba |

Không dùng (giấy phép không phù hợp cho dùng thương mại): DOVER (S-Lab non-commercial), LAION-CLAP và
Microsoft CLAP (trọng số không ghi giấy phép; dữ liệu huấn luyện có phần chỉ cho nghiên cứu), wav2vec2 của
nguyenvulebinh (CC-BY-NC), pyannote (phải đăng ký tài khoản để tải — thay bằng WeSpeaker).

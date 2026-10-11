# Justin EDIT Suite 2.1 — Cài đặt

## Windows
1. Giải nén file zip ra một thư mục bất kỳ.
2. Bấm đúp **`CAI_DAT.bat`**.
3. Chờ chạy xong. Mở Premiere Pro → **Window → Extensions → Justin EDIT Suite**.

Bộ cài **tự tải mọi thứ còn thiếu**, không cần quyền admin:

| Thành phần | Khi nào được cài |
|---|---|
| Python 3.12 | máy chưa có Python 3.10+ (qua winget, nếu không được thì tải từ python.org) |
| Thư viện Python (numpy, OpenCV, Pillow, faster-whisper, python-docx) | luôn kiểm tra |
| FFmpeg | chưa có → tải bản portable (~100 MB) |
| CUDA cho Whisper (cuBLAS, cuDNN) | máy có card NVIDIA |
| Gói AI (PyTorch, SigLIP2, beat_this, TransNetV2, căn chỉnh từ) | máy có NVIDIA / Apple Silicon và còn ≥ 8 GB ổ đĩa |
| Model Whisper + AI | tải trước để lần dùng đầu không phải chờ |
| Ollama + qwen3-vl (mô tả clip bằng AI local) | NVIDIA ≥ 8 GB VRAM và còn ≥ 15 GB ổ đĩa |

Muốn bản nhẹ (không gói AI, không Ollama): bấm **`CAI_DAT_KHONG_AI.bat`**. Gói AI cài thêm sau được trong panel.

**Chạy lại bao nhiêu lần cũng được:** bộ cài chỉ cài phần còn thiếu. Lỗi mạng giữa chừng thì cứ chạy lại.

## macOS
Bấm đúp **`CAI_DAT_MAC.command`** (lần đầu: chuột phải → Open). Python được cài qua Homebrew nếu có, nếu không thì cài từ python.org (máy sẽ hỏi mật khẩu).

## Trong panel
**Cài đặt → AI tăng tốc & tự sửa**
- Mỗi lần mở panel, tool tự kiểm tra. Thiếu thành phần bắt buộc thì **tự cài**, không cần bấm gì.
- **Tự sửa**: cài lại mọi thứ bắt buộc còn thiếu.
- **Cài gói AI**: cài PyTorch + các mô hình AI (~3 GB).
- **Tải trước model**: tải sẵn Whisper / SigLIP2 / wav2vec2 / beat_this.

## Gỡ cài đặt
Windows: `GO_CAI_DAT.bat` · macOS: `bash uninstall_mac.sh`. Bản gỡ **giữ lại** cài đặt cá nhân và cache phân tích trong các thư mục source.

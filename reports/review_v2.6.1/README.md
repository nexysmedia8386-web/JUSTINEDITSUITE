# Review mã nguồn Justin EDIT Suite v2.6.1

*Ngày 2026-10-11 · commit `4a6dc90` (Initial import v2.6.1) · phạm vi: `engine/` (Python, ~21.7k dòng), panel CEP (`dev/panel_harness/_site`, ~13.6k dòng JS/JSX), `installer/`, cấu trúc repo.*

## Tóm tắt

Phần lõi của engine đã chắc. Toán timeline dùng frame nguyên, NTSC timebase và ticks đúng, cache theo `size:mtime`, QC và stability dùng chung một lượt decode, panel spawn engine bằng mảng args (an toàn với đường dẫn tiếng Việt). Lỗi thật nằm ở **các đường biên**, và đa số rơi vào **4 kiểu lặp lại**:

| # | Kiểu lỗi | Ví dụ tiêu biểu |
|---|---|---|
| 1 | **Lỗi bị cache vĩnh viễn thành "thành công"** | VAD decode lỗi → "không có giọng nói" mãi mãi (ING-3); Ollama lỗi → "đã kiểm" (ING-6); beat_this lỗi → phân tích lại nhạc mọi lần (MON-5) |
| 2 | **Ghép lắp sai thứ tự / sai làn trong `cmd_build`** | VO bị đẩy lùi *sau khi* clip A4 đã ghi (BLD-2); J-cut lệch 1 frame (BLD-3); 2 text chồng trên V4 (BLD-7); JSX hỏng vì `.replace()` lồng nhau (BLD-4) |
| 3 | **Unicode và đường dẫn** | Clip trùng tên ở 2 thẻ nhớ trỏ nhầm file, folder có `&` bị copy toàn bộ footage (BLD-1); NFD/NFC/BOM làm mất cảnh, pin (BLD-5, ING-9); `cv2.imwrite` với đường dẫn tiếng Việt (AI-7) |
| 4 | **Đầu vào không tin cậy đi thẳng vào hành động** | LLM trả `speed 0.5` → shot 3 s thành 10 phút, `remove` trùng xoá nhầm shot (AI-2); xuống dòng trong spec LLM chèn được dòng `Nguồn:` (INT-8); agent xoá sequence không hỏi (PNL-7) |

Về hiệu năng, có 3 đòn bẩy rẻ nhất, và cả 3 cho **output giống hệt**:

- **`-threads 1` + `-fps_mode passthrough`** cho mọi lần grab 1 khung. Lane contact sheet đang chiếm ~18 GB RAM vì mỗi ffmpeg dựng 16 frame-thread 4K (ING-1).
- **Memo `best_sub`** trong montage: 93% lượt gọi là lặp, memo cho nhanh 2.1×, plan trùng từng bit (MON-1).
- **Index project một lần** trong `host.jsx::_itemByPath`: hiện là O(N×M), tức ~69k–342k lần `getMediaPath` khi label 340 clip (PNL-1).

Riêng **quy trình repo** cần xử lý trước mọi tối ưu khác: panel CEP (một nửa sản phẩm) **không nằm trong git**, còn repo **public** đang chứa output của dự án khách (plan, transcript, đường dẫn footage).

## Cách review

- 7 reviewer song song, mỗi người phụ trách một phân hệ. Sau đó 7 verifier độc lập **cố bác bỏ** từng phát hiện: đọc lại code, lần theo caller, và tái hiện bằng thí nghiệm khi làm được (ffmpeg trên clip HEVC 4K tổng hợp, build thật trên plan mẫu `engine/NX_*`, `node --check`, stub Python).
- Không đề xuất lại những gì đã có trong `reports/Tối ưu Justin EDIT Suite.md` và `docs/LO_TRINH_THUAT_TOAN.md`. Phát hiện nào chạm cùng chủ đề thì ghi rõ mục liên quan.
- Giới hạn: không có GPU, cv2, torch hay footage thật trong môi trường review. Phát hiện cần các thứ đó được đánh dấu **🟡 Có cơ sở** thay vì **✅ Đã xác nhận**.

| Phân hệ | Phát hiện | Cao | Trung bình | Thấp | Bác bỏ | Trùng |
|---|---|---|---|---|---|---|
| ING · Ingest & analysis | 13 | 2 | 5 | 6 | – | – |
| BLD · Align, assemble & build | 15 | 2 | 6 | 5 | 2 | – |
| MON · Montage, music & taste | 13 | 1 | 5 | 7 | – | – |
| AI · Tầng AI | 14 | – | 9 | 5 | – | – |
| INT · CapCut, reverse, YouTube, doctor | 15 | – | 6 | 9 | – | – |
| PNL · Panel CEP + ExtendScript | 15 | 1 | 6 | 8 | – | – |
| XC · Kiến trúc chung | 14 | – | 1 | 7 | 1 | 5 |
| **Tổng** | **99** | **6** | **38** | **47** | **3** | **5** |

→ **91 phát hiện duy nhất còn hiệu lực**: 78 đã xác nhận bằng code hoặc thí nghiệm, 13 có cơ sở. BLD-4 được nâng từ Thấp lên Trung bình vì hai lần tái hiện độc lập cho thấy chỉ cần một folder output tên `SEQUENCES` là đủ gây lỗi. Chi tiết từng phát hiện (bằng chứng, tác động, cách sửa, kết quả verify) nằm ở [PHU_LUC_REVIEW.md](PHU_LUC_REVIEW.md). Dữ liệu thô ở [data/review_findings.json](data/review_findings.json).

## Quy trình và repo (soát tay, đã kiểm)

| # | Vấn đề | Bằng chứng | Đề xuất |
|---|---|---|---|
| R1 | **Panel CEP không nằm trong git.** Bản gốc chỉ có ở `%APPDATA%\Adobe\CEP\extensions\com.justin.editsuite`. Repo chỉ có bản sao do `dev/panel_harness/build.py` sinh ra và một zip backup ngày 09/10, và hai bản này **đã lệch nhau** | `ai.js` 1527 dòng (zip) so với 1180 (harness); `keys.js` chỉ có trong zip; `host.jsx` 1429 so với 1535. `build_package.ps1` đóng gói extension thẳng từ `%APPDATA%` | Chuyển source panel vào `extension/com.justin.editsuite/` trong repo. Máy dev dùng `mklink /J "%APPDATA%\Adobe\CEP\extensions\com.justin.editsuite" G:\JustinEditSuite\extension\com.justin.editsuite`. `build_package.ps1` và `build.py` đọc từ repo |
| R2 | **Repo public chứa dữ liệu dự án khách.** `engine/NX_*` gồm 297 file output (plan, XML, SRT, report) với đường dẫn footage (`G:/PQ/...`, `D:/Le Văn`) | `git ls-files engine/NX_* \| wc -l` = 297 | Chuyển repo sang **private** nếu không chủ đích open-source. Thêm `engine/NX_*/`, `dev/panel_harness/_site/`, `backup/`, `.claude/settings.local.json` vào `.gitignore`, rồi `git rm -r --cached` các path đó. Repo mới có 1 commit, nên nếu cần xoá khỏi lịch sử thì làm lại commit đầu là đủ |
| R3 | File cấu hình máy cá nhân bị commit | `.claude/settings.local.json` (lệnh PowerShell với `G:\`), `.claude/launch.json` (`C:/Users/NC/...`) | `settings.local.json` theo quy ước là file local, nên đưa vào `.gitignore`. `launch.json` nên dùng đường dẫn tương đối |
| R4 | Chưa có test hay CI. `dev/montage_eval` đo chất lượng nhưng không chặn được lỗi hồi quy kiểu BLD-2/3/4/7 | Verifier XC-9 đã chạy thử: `cmd_build` tất định, 10 ms mỗi plan, không cần GPU | Xem mục **Hạ tầng an toàn** bên dưới |

## Ưu tiên sửa

### Đợt 1: sai output hoặc mất dữ liệu, mỗi việc ≤ 0.5 ngày

| # | ID | Việc | Vì sao gấp |
|---|---|---|---|
| 1 | BLD-1 | `safe_media`: chỉ dò `[&#%;]` trên **basename**, đặt tên đích duy nhất theo hash đường dẫn đầy đủ, **không bao giờ** `copy2` video lớn | Clip CARD1 phát footage của CARD2 mà không báo. Trên exFAT, Build copy hàng trăm GB |
| 2 | BLD-2 | Tách 3 pha trong `cmd_build`: probe mọi VO → đẩy lùi → mới sinh clip A4 | Hai giọng chồng nhau trên A4. Phụ đề, marker và `.vo.json` lệch ~1 s so với tiếng |
| 3 | AI-2 | `aiedit.resolve()` kiểm giá trị op do LLM sinh: speed ∈ [10, 1000], scale ∈ [50, 400], trim ≤ dur, bỏ op trùng | `speed 0.5` → 0.5%, shot 3 s thành 600 s; hai `remove #2` xoá luôn shot kế tiếp |
| 4 | BLD-4 | Sinh `*_import.jsx` bằng khai báo biến ở đầu IIFE thay cho chuỗi `.replace()` | Folder output `SEQUENCES` hoặc tên chứa `SRT`/`XML` làm JSX lỗi cú pháp, Premiere không import |
| 5 | ING-3 | `vad.pcm`: kiểm returncode + timeout. Lỗi thì **không cache** | Clip phỏng vấn bị đánh dấu "không có giọng nói" vĩnh viễn, không bao giờ được transcribe |
| 6 | AI-1 | Worker AI bắt `SystemExit` → trả `ERR` | Worker chết im lặng, panel nhận kết quả rỗng, các lệnh sau không có phản hồi |
| 7 | MON-2 | `get_preset()` đọc `<presets>/<id>.json` với **mọi** id, cảnh báo khi fallback | Preset tự tạo mà panel đang liệt kê bị âm thầm thay bằng `talking_head` |
| 8 | MON-3 | `triage._merge_tags` không ghi `use` cho entry `auto`, và consumer bỏ qua `use` khi `auto` | Mất 18% footage ứng viên, chặn AI ghi `best_broll` |
| 9 | BLD-5, ING-9 | `utf-8-sig` + NFC ở `script_parser`. NFC khi so khớp trong `media.resolve` (không đổi key) | Kịch bản gõ Unikey "tổ hợp" mất toàn bộ cảnh, hook, pin `Nguồn:` mà không báo |
| 10 | BLD-3, BLD-7 | J-cut tính bằng frame nguyên. Text chồng thời gian được cấp làn V5/V6 | Badge lệch sync đỏ trong Premiere; title chớp 2 frame |
| 11 | BLD-10 | Template "theo video tham khảo" giữ đúng fps NTSC (59.94 → 59.94 hoặc 29.97, không phải 24) | Footage GoPro 59.94 bị judder trên sequence 24p |
| 12 | PNL-3, PNL-8 | Mọi job (Sửa AI, Lập chỉ mục, Tự sửa) đi qua `NX.exclusive`. Reset `cancelled` khi bắt đầu job | Build chạy chồng hàng đợi đêm; lỗi "đã hủy" giả sau khi bấm Hủy lúc rảnh |
| 13 | PNL-4, XC-6 | Một helper `readJSONStrict` + ghi atomic (JS), `jsonio.save_atomic` cho 13 chỗ còn `json.dump(open())` (Python) | Đọc lỗi parse bị coi là `{}`, nên một lần ghi có thể xoá sạch quyết định tay của 340 clip trong `broll_tags.json` |
| 14 | ING-2 | Helper `_font()` dùng `ImageFont.truetype(tên)` + đóng gói một font OFL tiếng Việt (Be Vietnam Pro / Noto Sans) | Trên Mac hoặc Windows không cài ở ổ C:, mọi title/highlight thành chữ 10 px với ô vuông |

### Đợt 2: hiệu năng, output không đổi

| ID | Việc | Lợi ích đo được hoặc ước lượng |
|---|---|---|
| ING-1 | `-threads 1` trước mỗi `-i` keyframe-seek, cộng `-fps_mode passthrough` cho output 1 khung. `trim=end_frame=1` trong `grab_one_pass` | make_thumb 744 → 154 MB, 0.31 s. Contact sheet 4 input: 19 s CPU / 2.8 GB → 0.56 s / 0.6 GB. **Lưu ý verifier:** chỉ thêm `-threads 1` mà thiếu `passthrough` sẽ *chậm hơn* |
| MON-1, MON-4 | Memo `best_sub` theo `(file, a, b, …)` (không theo `id`). Ma trận look-alike N×N khi N ≤ 6000 | Montage 340 clip: 11.6 s → 5.5 s nhờ memo, plan trùng từng bit. Look-alike thêm 5–30% tùy số luồng BLAS (verifier đo thấp hơn reviewer) |
| PNL-1, PNL-6 | `_itemByPath`: dựng index một lần, gom path thiếu, `importFiles` một lần cho mỗi bin. `_qeClip`: con trỏ đơn điệu trong batch | Label 340 clip: 69k–342k → ~1 lượt duyệt project. Hậu kỳ cả timeline nhanh 4–8× |
| AI-3 | Một `num_ctx` chung cho model VL (4096) | Không còn reload runner qwen3-vl 6 GB (3–10 s mỗi lần, dễ tràn shared memory) khi rerank và describe xen nhau |
| AI-9 | Cache diarize theo key clip + mtime transcript | Hết việc giải mã và embed lại cả thư mục mỗi lần bấm "Người nói" (~100 s/giờ thoại) |
| PNL-2 | Upload proxy Gemini qua pool 4, có log lỗi từng clip | Đường mặc định "Phân tích source": 15–30 phút tuần tự cho 340 clip → ~¼ |
| PNL-5 | Sau `taskkill` describe: `keep_alive:0` cho model trong `/api/ps`. Chỉ warm SigLIP2 khi mở tab tìm footage | Trả lại ~1 GB VRAM và VRAM của Ollama ngay khi job bắt đầu |
| AI-8, AI-10, AI-11 | Lấy 1 khung giữa khi không cần reframe; gọi `at()` một lần cho mỗi clip; lấy khung rerank song song | Hậu kỳ: 3–10 → 1 khung mỗi piece; RANK đầu tiên sớm hơn 5–10 s |
| ING-5, ING-12, BLD-14 | Không xoá `shake.json` theo triage; cache contact sheet theo từng clip; cache thẻ PNG theo nội dung | Bật lại một nhóm clip không còn phải decode 4K lại; thêm 1 clip không phải grab lại 340 clip |
| ING-4 | Whisper OOM: giải phóng, thử lại cùng clip với batch/2, chỉ chuyển CPU cho clip đó | Tránh chuyển *vĩnh viễn* sang CPU 4 luồng (từ vài phút thành vài giờ). **Bắt buộc làm cùng mục 0.1 của báo cáo cũ**, vì "No Sysmem Fallback" biến tràn VRAM thành OOM |
| MON-5, MON-8 | Ghi `engine_tried`, retry beat_this trên CPU một lần; cache VO theo model + lang | Hết việc phân tích nhạc lại mỗi lần Align khi beat_this lỗi |
| XC-11 | Một file PCM 16 kHz mono cho mỗi clip, dùng chung cho VAD, transcribe, sounds, speakers | Bớt 3 lượt decode audio mỗi clip |

### Đợt 3: hạ tầng an toàn để tối ưu tiếp

| ID | Việc |
|---|---|
| XC-9 | **Golden test plan → XML/JSX/SRT** từ chính `engine/NX_*` (sau khi chuyển chúng sang `tests/fixtures/`, bỏ dữ liệu khách). Đã chứng minh: build tất định, 10 ms mỗi plan, không cần GPU. Thêm assert bất biến: trên cùng track `clip[i].end ≤ clip[i+1].start`, cặp V/A có link thì `start − in` bằng nhau, keyframe tăng dần, `node --check` cho JSX |
| XC-5 | Quy ước lỗi: 218 `except` rộng, 159 không log. Mỗi cache entry phải lưu `status: ok/failed` + phiên bản, entry `failed` không bao giờ được coi là fresh. Lệnh CLI lỗi phải trả exit code ≠ 0 |
| XC-8 | Gom hạ tầng trùng lặp vào `nxcore/`: 6 hàm key, 4 hàm probe với 4 luật fps khác nhau, nhiều wrapper ffmpeg (mỗi bản một chính sách timeout và returncode) |
| XC-7, INT-9 | Pin dependency (lockfile `uv`/`pip-tools`). `beat_this` theo commit hoặc tag, không lấy `main.zip`. `doctor` kiểm SHA-256 cho `ffmpeg.exe` và checkpoint |
| PNL-7 | Agent: các tool phá hủy (`delete_sequence`, `open_project`, `create_project`, `save_as`) luôn hỏi lại. Không dựa vào regex lọc ExtendScript |
| PNL-15, INT-15, INT-6 | API key và refresh token: dùng Windows Credential Manager / macOS Keychain (`keyring`), hoặc tối thiểu chmod 600. Xử lý `invalid_grant` bằng thông báo "kết nối lại kênh" |

## Đối chiếu với báo cáo nghiên cứu cũ

- **0.1 "Prefer No Sysmem Fallback"** phải đi kèm **ING-4**. Nếu không, OOM mới sẽ đẩy Whisper xuống CPU cho cả phần còn lại của phiên.
- **0.2 thay Haar**: nên sửa thêm **AI-5** cùng lúc (NVDEC `at()` bỏ qua rotation), vì cả hai cùng làm hỏng tín hiệu bố cục và mặt người với clip điện thoại.
- **1.1 decode-once**: ING-1 là bước rẻ hơn nhiều, nên làm trước (giảm RAM ×4, không đổi kiến trúc). XC-11 là phiên bản audio của 1.1.
- **1.3 cache theo nội dung**: trước khi đổi sang XXH3, cần sửa các key đang sai: `digest` dùng trường `size` không tồn tại (ING-7), cache VO bỏ qua model (MON-8), `stem()` trùng tên (XC-10).
- **3.1 tách lớp host**: verifier xác nhận refactor `cmd_build` thành *timeline model thuần + writers* chính là tiền đề. Golden test XC-9 là cách đo "giống hệt từng bit" mà mục 3.1 yêu cầu.

## Phụ lục

- [PHU_LUC_REVIEW.md](PHU_LUC_REVIEW.md): 99 phát hiện, mỗi phát hiện kèm bằng chứng, tác động, cách sửa, kết quả verify và hiệu chỉnh của verifier.
- [data/review_findings.json](data/review_findings.json): dữ liệu thô (để agent khác hoặc script dùng lại).

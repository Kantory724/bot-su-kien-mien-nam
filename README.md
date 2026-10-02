# Bot tin lễ hội, sự kiện miền Nam

Bot tự động thu thập tin lễ hội/sự kiện đông người tại 8 tỉnh, thành miền Nam (TP.HCM, Đồng Nai, Tây Ninh, An Giang, Đồng Tháp, Vĩnh Long, Cần Thơ, Cà Mau), lọc – trích xuất – khử trùng, rồi gửi qua **Telegram**: bản tin 07:00 mỗi sáng, cảnh báo ngay khi có sự kiện lớn, và lệnh tra cứu.

Mục đích: nắm sớm các sự kiện tập trung đông người để chủ động vùng phủ, dung lượng mạng (tăng cường phát sóng, xe lưu động, theo dõi KPI).

```
 RSS báo lớn ─┐
 Google News ─┼─► Thu thập ─► Lọc (từ khóa + tỉnh) ─► Trích xuất ─► Khử trùng ─► SQLite
 (theo tỉnh)  │    (collector)    (extractor/geo)     (regex ± AI)   (pipeline)       │
 HTML/UBND   ─┘                                                                       ▼
                       Telegram ◄── Bản tin 07:00 · Cảnh báo sự kiện lớn · Lệnh /homnay /tuannay /tinh /sukien · Excel
```

## Vì sao chọn Telegram

Miễn phí, tạo bot trong 2 phút qua BotFather, gửi chủ động không cần duyệt mẫu tin, tài liệu đầy đủ, thêm người nhận chỉ cần thêm chat id. Zalo OA/ZNS và WhatsApp Cloud API đều cần đăng ký doanh nghiệp, duyệt mẫu và có thể phát sinh phí, chưa phù hợp mục tiêu "chi phí gần bằng 0". Cách mở rộng sang Zalo ở cuối tài liệu.

---

## Cài đặt trong 30 phút (GitHub Actions, miễn phí, không cần máy chủ)

### Bước 1 – Tạo bot Telegram (3 phút)
1. Trên Telegram, nhắn **@BotFather** → gõ `/newbot` → đặt tên và username (kết thúc bằng `bot`).
2. BotFather trả về **token** dạng `123456789:AA...`. Giữ kín, không gửi cho ai, không ghi vào mã nguồn.
3. Mở bot vừa tạo, bấm **Start** (bắt buộc, nếu không bot không nhắn được cho bạn).

### Bước 2 – Lấy Chat ID (2 phút)
Nhắn `/id` cho bot (cần bot đang chạy, xem bước 4), hoặc chạy trên máy:
```bash
cp .env.example .env        # điền TELEGRAM_BOT_TOKEN
pip install -r requirements.txt
python main.py get-chat-id
```
Với **nhóm**: thêm bot vào nhóm, nhắn `/id@ten_bot` trong nhóm; id của nhóm là số âm (vd `-1001234567890`).

### Bước 3 – Thử trên máy (5 phút, tuỳ chọn)
```bash
python main.py demo          # chạy thử bằng dữ liệu giả: xem định dạng bản tin, không cần mạng/token
python main.py check-sources # kiểm tra từng nguồn tin còn sống không
python main.py test-send     # gửi tin thử (cần TELEGRAM_CHAT_IDS trong .env)
python main.py --dry-run collect   # thu thập thật nhưng in ra màn hình, không gửi
```

### Bước 4 – Đưa lên GitHub và bật chạy tự động (10 phút)
1. Tạo repo GitHub (**khuyên dùng repo Public**: Actions miễn phí không giới hạn phút; repo chỉ chứa mã và dữ liệu tin công khai, mọi bí mật nằm trong Secrets). Đẩy toàn bộ thư mục này lên.
2. Vào **Settings → Secrets and variables → Actions → New repository secret**, tạo:

   | Secret | Giá trị | Bắt buộc |
   |---|---|---|
   | `TELEGRAM_BOT_TOKEN` | token từ BotFather | Có |
   | `TELEGRAM_CHAT_IDS` | chat id nhận tin, cách nhau dấu phẩy: `111,222,-1001234567890` | Có |
   | `ADMIN_CHAT_IDS` | chat id nhận cảnh báo nguồn lỗi (trống = gửi cho tất cả) | Không |
   | `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` | bật AI (xem phần AI) | Không |

3. Tab **Actions** → chọn workflow **Bot sự kiện miền Nam** → **Run workflow** → chọn `test-send` → kiểm tra Telegram có tin "đã kết nối".
4. Chạy tiếp `collect` để nạp dữ liệu lần đầu, rồi `digest --force` để xem bản tin.

Từ đó workflow tự chạy mỗi 5 phút: trả lời lệnh, thu thập mỗi 2 giờ, gửi bản tin ngay khi qua 07:00 (giờ VN). Dữ liệu lưu trong `data/events.db` và được workflow tự commit lại.

> Lưu ý GitHub Actions: lịch chạy là "cố gắng hết sức", có thể trễ vài phút đến vài chục phút giờ cao điểm, nên bản tin thường đến lúc 07:00–07:15, và lệnh `/homnay`… có thể trả lời chậm vài phút. Nếu cần đúng giờ và phản hồi tức thì, chạy chế độ `serve` trên máy chủ (mục bên dưới). Repo Public không có hoạt động 60 ngày có thể bị GitHub tắt lịch chạy; vì bot commit CSDL thường xuyên nên thường không gặp, nếu có thì vào tab Actions bấm bật lại. Nếu dùng repo Private, đổi cron thành `*/30 * * * *` để không vượt hạn mức 2.000 phút/tháng.

---

## Cách dùng hằng ngày

**Bạn không cần làm gì**: 07:00 mỗi sáng bot gửi bản tin; khi phát hiện sự kiện quy mô lớn, bot gửi cảnh báo riêng.

Bản tin có dạng:
```
BẢN TIN SỰ KIỆN MIỀN NAM — 07:00 Thứ Hai 12/10/2026
7 ngày tới: 3 sự kiện (1 quy mô lớn)

[CAO] 17–18/10 · Cần Thơ
<Tên sự kiện>
Địa điểm: <khu vực, phường/xã>
Quy mô: ~20.000 người · có bắn pháo hoa
Nguồn: <link bài gốc>
```
Mức ưu tiên: **CAO** = ≥10.000 người, hoặc có bắn pháo hoa/đại nhạc hội/countdown, hoặc ≥5.000 người ở địa điểm nhạy cảm (quảng trường, sân vận động, sân bay, cảng, khu du lịch…); **TB** = lễ hội/hội chợ/triển lãm/giải chạy…, quy mô ≥1.000 hoặc địa điểm nhạy cảm; **THẤP** = còn lại.

**Lệnh trong chat với bot**

| Lệnh | Tác dụng |
|---|---|
| `/homnay` | Sự kiện đang diễn ra hôm nay |
| `/tuannay` | Sự kiện trong 7 ngày tới |
| `/tinh Cần Thơ` | Sự kiện 60 ngày tới của một tỉnh (gõ có dấu/không dấu, `hcm`, `sài gòn` đều được) |
| `/sukien pháo hoa` | Tìm theo từ khóa (tên, địa điểm, tóm tắt) |
| `/excel` hoặc `/excel thang` | Nhận file Excel 7 ngày tới / cả tháng |
| `/trangthai` | Số sự kiện, lần thu thập gần nhất, nguồn nào đang lỗi |
| `/id`, `/help` | Xem Chat ID, danh sách lệnh |

Mỗi sáng thứ Hai bot tự gửi kèm Excel tuần, ngày 1 hằng tháng kèm Excel tháng.

Chỉ chat nằm trong `TELEGRAM_CHAT_IDS` mới dùng được lệnh tra cứu; người lạ chỉ lấy được Chat ID của chính họ.

### Thêm người nhận
Người cần nhận tin mở bot, bấm Start, nhắn `/id` để lấy số; bạn thêm số đó vào secret `TELEGRAM_CHAT_IDS` (cách nhau dấu phẩy). Hoặc thêm bot vào một nhóm và dùng id nhóm.

---

## Thêm/sửa nguồn tin

Sửa `config/sources.yaml`, không cần sửa mã. Có 3 loại: `rss`, `google_news` (tìm theo từng tỉnh, gom cả báo địa phương như Báo Cần Thơ, Báo Đồng Nai, SGGP…) và `html` (trang không có RSS).

**Cổng UBND/Sở VH-TT-DL và trang bán vé** thường không có RSS, đã có sẵn 2 mẫu `html` đang tắt. Cách bật:
1. Mở trang chuyên mục tin của cổng, bấm F12, chuột phải vào tiêu đề một tin → *Copy → Copy selector* (hoặc xem thẻ `<a>` bao quanh tiêu đề).
2. Điền `url`, `item_selector` (vd `h3 a`), `province_hint`; đổi `enabled: true`.
3. Chạy `python main.py check-sources`; dòng `OK … N mục` là đạt.

Khi một nguồn lỗi (hoặc trang đổi giao diện khiến selector không khớp) **3 lần liên tiếp**, bot tự nhắn cảnh báo cho admin; khi hồi phục bot báo lại. Nhìn lại bằng `/trangthai`.

## Bật AI để trích xuất chính xác hơn (tuỳ chọn, cộng điểm)

Mặc định bot dùng luật regex (miễn phí, chạy nhanh, đủ cho tin có ngày/địa điểm rõ). Bật LLM để đọc hiểu bài viết khó (ngày viết lắt léo, nhiều sự kiện trong một bài, đổi âm lịch sang dương lịch…):

- `LLM_PROVIDER=gemini` + `LLM_API_KEY=<khóa từ Google AI Studio>` (có bậc miễn phí), hoặc `LLM_PROVIDER=anthropic` + khóa Anthropic.
- `LLM_MODEL` để trống sẽ dùng mô hình mặc định trong `bot/llm.py`; nếu nhà cung cấp đổi tên mô hình, điền tên mới ở đây.
- Giới hạn `LLM_MAX_CALLS=30` lần gọi mỗi lượt thu thập để chi phí gần bằng 0. Hết hạn mức/lỗi khóa, bot tự quay về regex, không dừng.

## Chạy 24/7 trên máy chủ riêng (đúng giờ, phản hồi lệnh tức thì)

```bash
# VPS / Raspberry Pi / Oracle Cloud Free
cp .env.example .env && nano .env
pip install -r requirements.txt
python main.py serve                      # chạy liên tục, tự thu thập + gửi 07:00

# hoặc Docker
docker build -t mn-events . && docker run -d --restart=always --env-file .env -v $(pwd)/data:/app/data mn-events
```
Hoặc dùng cron thay `serve`: `*/5 * * * * cd /đường/dẫn && python main.py tick`. Nhớ tắt workflow GitHub nếu chuyển sang máy chủ riêng (tránh hai nơi cùng gửi tin).

---

## Cách bot hoạt động

1. **Thu thập**: đọc RSS (VnExpress, Tuổi Trẻ, Thanh Niên, Dân Trí), Google News theo từng nhóm tỉnh, và các nguồn `html` bạn bật. Mỗi nguồn lỗi chỉ bị ghi log, không làm dừng cả lượt.
2. **Lọc**: tiêu đề/tóm tắt phải có từ khóa lễ hội/sự kiện (lễ hội, đại nhạc hội, pháo hoa, countdown, hội chợ, khai mạc, vía Bà, Ok Om Bok, Chôl Chnăm Thmây…) và nội dung phải thuộc 8 tỉnh. Nhận diện tỉnh dùng cả địa danh/tỉnh cũ đã sáp nhập từ 01/07/2025 (vd Phú Quốc → An Giang, Bến Tre → Vĩnh Long, Vũng Tàu → TP.HCM; xem `bot/geo.py`).
3. **Trích xuất**: tên, tỉnh, địa điểm cụ thể (+phường/xã), ngày giờ bắt đầu–kết thúc, quy mô, pháo hoa, đại nhạc hội. Bỏ qua ngày âm lịch; tự suy luận năm; loại số liệu "lượt khách cả năm".
4. **Khử trùng**: cùng tỉnh, lệch ≤1 ngày, tên giống nhau → gộp thành một sự kiện (giữ quy mô lớn nhất, gom các link nguồn). Bài cùng URL chỉ xử lý một lần.
5. **Cảnh báo**: sự kiện ≥10.000 người, có pháo hoa hoặc đại nhạc hội → gửi ngay, mỗi sự kiện chỉ cảnh báo một lần.
6. **Bản tin**: 07:00, sự kiện từ hôm nay đến 7 ngày tới, sắp theo ngày rồi theo mức ưu tiên.

Ngưỡng chỉnh được bằng biến môi trường: `LARGE_CROWD`, `LOOKAHEAD_DAYS`, `DIGEST_HOUR`, `COLLECT_INTERVAL_MIN`, `FAIL_ALERT_THRESHOLD`.

## Kiểm thử

```bash
pip install pytest && python -m pytest -q tests     # 34 test: ngày/giờ/quy mô, tỉnh, khử trùng, lệnh, Excel, lịch 07:00, cảnh báo lỗi nguồn
```

## Khắc phục sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| Bot không nhắn cho tôi | Chưa bấm Start với bot, hoặc chat id sai/chưa có trong `TELEGRAM_CHAT_IDS` |
| Workflow đỏ, "Thiếu TELEGRAM_BOT_TOKEN" | Chưa tạo secret hoặc gõ sai tên secret |
| `git push` bị từ chối | Settings → Actions → General → Workflow permissions → *Read and write* |
| Không thấy sự kiện nào | Chạy `check-sources`; xem log của lần chạy `collect` (số `items`, `new`) |
| Nhiều nguồn báo lỗi 403 trên GitHub Actions | Một số trang chặn IP trung tâm dữ liệu. Chạy workflow `check-sources` để biết nguồn nào bị chặn; tắt nguồn đó (`enabled: false`) và dựa vào Google News theo tỉnh, hoặc chạy `serve` trên máy có IP thường |
| Sự kiện thiếu địa điểm/ngày | Bài báo không nêu rõ; bật AI, hoặc xem link nguồn |
| Link Google News không đọc được nội dung | Bình thường (Google bọc link). Bot vẫn dùng tiêu đề; cài thêm `googlenewsdecoder` (bỏ dấu `#` trong `requirements.txt`) để đọc bài gốc |
| Lệnh phản hồi chậm | Giới hạn của GitHub Actions (cron 5 phút); dùng `serve` nếu cần tức thì |

## Bảo mật

- Token, khóa API, chat id chỉ nằm trong **GitHub Secrets** hoặc file `.env` (đã nằm trong `.gitignore`). Mã nguồn không chứa bí mật; log không in token.
- Nếu lỡ lộ token: BotFather → `/revoke` để cấp token mới, cập nhật Secret.
- Lệnh tra cứu chỉ phục vụ chat nằm trong danh sách cho phép.

## Giới hạn đã biết

- Trích xuất bằng luật có thể sai với bài viết lắt léo (nhiều sự kiện trong một bài, ngày nêu gián tiếp). Bật AI để cải thiện; mọi tin đều kèm link nguồn để kiểm chứng.
- Tin chỉ có thể báo khi báo chí/cổng thông tin đã đăng; "quy mô dự kiến" lấy từ số liệu bài báo nêu, không có thì để trống.
- Cổng UBND/Sở VH-TT-DL và trang bán vé cần bạn điền selector (mỗi trang một kiểu).

## Cấu trúc

```
main.py                 điểm vào CLI
config/sources.yaml     danh sách nguồn
bot/collector.py        thu thập RSS/HTML, tải bài
bot/extractor.py        lọc từ khóa, trích xuất bằng regex
bot/geo.py              tỉnh/thành, bí danh, nhận diện tỉnh
bot/llm.py              trích xuất bằng AI (tuỳ chọn)
bot/pipeline.py         luồng chính, khử trùng, cảnh báo, bản tin
bot/commands.py         lệnh /homnay /tuannay /tinh /sukien ...
bot/runner.py           poll lệnh, lịch (tick), chế độ serve
bot/telegram.py         gọi Telegram Bot API
bot/exporter.py         xuất Excel
bot/db.py, models.py    SQLite và mô hình dữ liệu
.github/workflows/bot.yml   lịch chạy GitHub Actions
tests/                  kiểm thử
```

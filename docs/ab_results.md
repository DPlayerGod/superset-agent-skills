# Kết quả đo Baseline vs Agent Skills

## Trạng thái: ĐÃ ĐO (2026-08-14)

Đo trực tiếp qua `claude_gateway:8090/api/v1/agent/query`, payload giống hệt panel
(`context: {path, superset_user}`, `row_limit: 200`), mỗi lượt một `session_id` mới.
Kết quả đúng/sai được chấm với ground truth lấy thẳng từ Postgres, và với `params`
thật của chart đọc qua Superset REST API — không chấm bằng cảm nhận về câu chữ.

- CLI `2.1.197`, model `claude-opus-4-8[1m]`.
- **59 lượt hợp lệ**: baseline n=33, skills n=26. Mỗi lớp truy vấn n≥3 mỗi nhánh
  (trừ nhánh chart: n=2).
- Dữ liệu lúc đo: `fact_employee_allocation` 46.109 dòng, **chỉ năm 2026**,
  100 nhân viên, 7 dự án, 4 `organization_name`
  (Data Analytics, Product Management, Quality Assurance, Software Development).

## Kết quả theo lớp truy vấn

| Lớp truy vấn | baseline | skills | Kết luận |
|---|---|---|---|
| Lọc theo giá trị dimension không tồn tại (`FTE phòng Digital Transformation`) | 0/5 nói được là phòng không tồn tại; 3/4 khẳng định thẳng **"FTE = 0"** | **5/5** báo không tìm thấy + liệt kê 4 org có thật | **skills thắng** |
| Chart nhiều chiều (`FTE theo dự án theo từng tháng`) | 2/2 đặt `x_axis = project_name`, **mất hẳn chiều tháng** | 2/2 đặt `x_axis = working_month_year`, series split `project_name` | **skills thắng** |
| Câu mơ hồ FTE vs headcount (`bao nhiêu nhân sự`) | 5/5 chỉ trả headcount `100` | 3/3 trả cả `100` và FTE `1122.40` | skills đầy đủ hơn, baseline không sai |
| Tổng / Top-N / theo tháng / theo quý | đúng số | đúng số | hoà, baseline nhanh hơn |
| Chart 1 chiều (line theo tháng, pie theo phòng ban, big number) | `params` đúng | `params` **giống hệt từng chữ** | hoà |
| Kỳ không có dữ liệu (`tháng 1/2025`) | 4/4 báo "không có dữ liệu" | 2/3 như baseline; 1/3 nói thêm data chỉ có `01/2026`–`12/2026` | hoà, skills nhỉnh không ổn định |
| Liệt kê vượt `row_limit` (1.200 dòng) | báo bị giới hạn 200 | nâng `row_limit` lên 500 rồi báo bị cắt | hoà — **skills vi phạm chính luật của nó** |
| Dashboard | chưa đo | chưa đo | — |

**skills thắng 2/8 lớp.** Cả hai đều là lỗi *im lặng*: câu trả lời vẫn trôi chảy,
vẫn có số, chart vẫn render đẹp — chỉ là sai. Đó là loại lỗi người dùng BI không tự
phát hiện, nên 2/8 này đắt hơn con số 2/8 gợi ý.

## Cơ chế: skill chỉ có giá trị ở chỗ docstring và system prompt im lặng

Đây là phát hiện quan trọng nhất, và nó giải thích toàn bộ bảng trên. Nội dung skill
rơi vào 3 nhóm:

1. **Trùng system prompt** (`current_row_indicator = 'Y'`, tính cộng dồn của
   `project_allocated_hc`, định dạng trả lời). Đã biết trước, `AGENTS.md` có cảnh báo.
2. **Trùng docstring của MCP tool** — nhóm này *không ai lường trước*.
   `create_chart` docstring đã ghi sẵn 5 giá trị `viz_type`, "metrics: SQL aggregate
   expressions", và luật một-metric của `pie`/`big_number_total`. Docstring đi vào
   tool schema nên có mặt ở **cả hai** nhánh.
3. **Không ở đâu khác** — chỉ còn 4 điểm, và đúng 2 trong số đó là 2 lớp skills thắng.

Giả thuyết bị bác bỏ bằng số liệu: baseline **8/8 lượt** viết đúng
`metrics = ["SUM(project_allocated_hc)"]` và đúng `viz_type`. Nửa nội dung
`vdt-charting` vì thế không đo được gì. Thứ duy nhất docstring **không** nói là
`groupby[0]` trở thành trục x — và đó chính xác là probe chart duy nhất có khác biệt.

> Khi viết skill mới ở đây: đọc docstring của tool trước. Luật nào docstring đã nói
> thì viết vào skill là vô ích — nó xuất hiện ở baseline và tự triệt tiêu.

## Cái giá

| | tool calls / lượt | latency TB | median |
|---|---|---|---|
| baseline (n=33) | 2.24 | 25.7s | 24.4s |
| skills (n=26) | 3.69 | 39.3s | 35.1s |

**+1.45 tool call, +53% thời gian.** skills là nhánh duy nhất từng chạm
`DEADLINE_SECONDS = 150` (1 lượt, câu "Tổng FTE hiện tại").

Chênh lệch tool call đến từ **routing nhiều chặng** — mỗi lần nạp skill là một
tool call riêng:

| Skill | Số lần được gọi | Ghi chú |
|---|---|---|
| `vdt-fte-sql` | 24 | xương sống |
| `vdt-bi` | 18 | **chỉ định tuyến, chưa lần nào tự tạo hành động** |
| `vdt-charting` | 10 | đa số nội dung trùng docstring |
| `vdt-troubleshooting` | 6 | ít nhưng đúng chỗ — nguồn của điểm thắng lớn nhất |
| `vdt-dashboards` | 1 | gần như chưa được thực thi |

Câu "FTE phòng Digital Transformation" đi **3 chặng**
(`vdt-bi` → `vdt-fte-sql` → `vdt-troubleshooting`, 4/4 lượt). Chặng thứ ba đáng tiền.
Chặng đầu thì không: bỏ `vdt-bi` và đưa từ khoá trigger vào `description` của 4 skill
kia sẽ tiết kiệm một round-trip mỗi lượt.

## Luật đang ngủ

Nhóm luật sắp xếp thời gian của `vdt-fte-sql` (`working_month_year` là text `MM/YYYY`
nên `ORDER BY` trực tiếp sai; `working_quarter` không kèm năm) **không kích hoạt được**
với dữ liệu hiện tại vì chỉ có duy nhất năm 2026: `'01/2026'`..`'12/2026'` sort
lexicographic vẫn đúng, và group theo quý không thể trộn năm. Muốn đo nhóm luật này
phải nạp ≥2 năm dữ liệu.

## Hai cảnh báo về phương pháp

1. **n=1 là không đủ.** Vòng đo đầu tiên ghi nhận baseline "không gọi tool nào, đòi
   xin phép chạy query" ở 2/14 lượt, và suýt thành kết luận. Chạy lặp 10 lượt trên
   đúng 2 câu đó: baseline **10/10 trả lời đúng**. Đó là fluke, không phải hành vi.
2. **Nhiễu do tải.** Một phiên dựng dashboard chạy song song trong lúc đo làm mọi số
   latency tăng lên; lượt timeout duy nhất rơi đúng khung giờ đó. Số đúng/sai không
   bị ảnh hưởng (mỗi lượt có `timing` và `answer` riêng), số latency thì có.

## Việc đã làm sau khi đo

- **Siết `DISALLOWED_TOOLS`** (`gateway_server.py`) từ 10 lên 30 tên. Phát hiện ngoài
  lề nhưng nghiêm trọng hơn bài đo: `ALLOWED_TOOLS` chỉ là danh sách tự-duyệt, không
  phải hàng rào — turn thật đã gọi được `TaskCreate`/`TaskUpdate` dù chúng không nằm
  trong đó; và deny `Task` không deny `TaskCreate`. `Monitor` (nhận lệnh shell) từng
  mở, tức là lệnh cấm `Bash` có cửa sau. Sau khi siết, tool built-in còn lại đúng 2:
  `Skill` (bắt buộc, để nạp plugin) và `ReportFindings`.
- **Bổ sung docstring `create_dashboard`** rằng nó *thay thế* membership — chart đang
  ở dashboard khác sẽ bị **chuyển đi**, không phải copy. Hành vi phá hoại âm thầm mà
  tool không khai báo.
- **Sửa recipe `vdt-fte-sql`** vốn hardcode `organization_name = 'Digital Transformation'`
  — giá trị không tồn tại trong dữ liệu đã nạp.
- **Chuyển 2 luật thắng ra chỗ dùng chung** (xem mục dưới).

## Sau bài đo này, A/B không còn đo ra khác biệt

Hai luật tạo ra toàn bộ chênh lệch đã được chuyển sang chỗ áp dụng cho **cả hai**
nhánh, vì mục tiêu sản phẩm thắng mục tiêu thí nghiệm:

- `groupby[0]` là trục x → docstring `create_chart` trong `mcp_server.py`.
- Kiểm giá trị thật trước khi báo "không có dữ liệu" → `claude_gateway/role_prompt.md`.

Đã kiểm chứng sau khi chuyển, chạy trên **baseline** (trước đó baseline sai 100% ở cả hai):

- "FTE phòng Digital Transformation": **2/2** báo không tồn tại + liệt kê 4 org có thật.
  Một lượt còn nói thẳng *"trả lời 0 FTE cho Digital Transformation sẽ không đúng"*.
- "Biểu đồ cột FTE theo dự án theo từng tháng": **2/2** ra
  `x_axis = working_month_year`, `groupby = ['project_name']`.

Nghĩa là **chạy lại A/B bây giờ sẽ ra hoà ở cả 8 lớp** — đó là kết quả mong muốn, không
phải hồi quy. Bảng phía trên là ảnh chụp trạng thái *trước* khi chuyển, giữ lại làm
bằng chứng. Muốn `skills_plugin` có giá trị đo được trở lại thì phải cho nó nội dung
mới thuộc nhóm 3 (không trùng system prompt, không trùng docstring) — ví dụ nhánh
dashboard, hoặc nhóm luật thời gian sau khi nạp dữ liệu nhiều năm.

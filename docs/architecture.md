# Kiến trúc hệ thống: thành phần & luồng chạy

Tài liệu này mô tả **hiện trạng** của `super_dplayergod` — có những thành phần
nào, mỗi thành phần chịu trách nhiệm gì, và một câu hỏi trong chat đi qua đâu để
biến thành SQL / chart / dashboard.

Các doc khác bổ trợ, không trùng lặp:
- [ROLLOUT.md](ROLLOUT.md) — cách chạy, note vận hành, A/B baseline vs skills.
- [task.md](task.md) — roadmap và business rule Daily FTE.
- [mock_data_docs.md](mock_data_docs.md) — schema `fact_employee_allocation`.
- [preview_feature.md](preview_feature.md), [chat_experiment_feature.md](chat_experiment_feature.md)
  — thiết kế của 2 feature đã triển khai.

---

## 1. Bức tranh tổng thể

Hệ thống gồm **3 container** và **1 tiến trình con** (MCP server không phải là
service riêng — điểm này hay bị hiểu nhầm):

```
                    TRÌNH DUYỆT (đã đăng nhập Superset)
                    ┌──────────────────────────────────┐
                    │  Superset SPA                    │
                    │  + panel "AI Agent"              │
                    │    (vdt-ai-chat.js / .css)       │
                    └───────┬──────────────────▲───────┘
       POST /api/v1/vdt-ai-chat/query          │  answer + previews + timing
       (cookie session Superset)               │
┌───────────────────────────▼──────────────────┴───────────────────────────┐
│ container: super_superset          :8088  (publish ra host)              │
│                                                                          │
│  Apache Superset 6.1.0                                                   │
│  └─ superset_config.py :: FLASK_APP_MUTATOR                              │
│     ├─ Blueprint /api/v1/vdt-ai-chat                                     │
│     │   ├─ GET  /static/<file>   → phục vụ js/css của panel              │
│     │   └─ POST /query           → check login rồi proxy sang gateway    │
│     └─ ChoiceLoader nạp templates/tail_js_custom_extra.html              │
└───────────────────────────┬──────────────────▲───────────────────────────┘
        POST /api/v1/agent/query               │  JSON trả nguyên bytes
        (mạng nội bộ Docker, timeout 170s)     │
┌───────────────────────────▼──────────────────┴───────────────────────────┐
│ container: super_claude_gateway    :8090  (KHÔNG publish ra host)        │
│                                                                          │
│  gateway_server.py — ThreadingHTTPServer                                 │
│  └─ subprocess: claude -p --output-format stream-json ...                │
│     (Claude Code CLI headless, deadline 150s)                            │
│     └─ stdio subprocess: python3 /app/mcp_server.py   ◄── MCP server     │
│        ├─ tool đọc  → SQLAlchemy → Postgres                              │
│        └─ tool ghi  → REST API   → Superset                              │
└──────────┬───────────────────────────────────────────┬───────────────────┘
           │ postgresql://…@postgres:5432              │ http://superset:8088
┌──────────▼──────────────────┐                        │
│ container: super_postgres   │                        │
│ :5432  super_db             │◄───────────────────────┘
│ fact_employee_allocation    │   (Superset cũng tự query bảng này
└─────────────────────────────┘    khi render chart)
```

Điểm cần nhớ: **có 2 đường đi tới Postgres**. Agent đọc số liệu qua MCP tool
(`run_sql_readonly`), còn chart do agent tạo thì Superset tự query lấy dữ liệu
khi render — agent không "đổ" data vào chart.

---

## 2. Bảng thành phần

### 2.1 Container

| Service | Image | Port | Vai trò |
| --- | --- | --- | --- |
| `postgres` | `postgres:15` | 5432 (public) | Data warehouse. Chạy [init-db/01_init_data.sql](../init-db/01_init_data.sql) lần đầu khởi tạo. |
| `superset` | build từ [superset/Dockerfile](../superset/Dockerfile) | 8088 (public) | BI + host của panel chat + lớp xác thực. |
| `claude_gateway` | build từ [claude_gateway/Dockerfile](../claude_gateway/Dockerfile) | 8090 (**chỉ nội bộ**) | Bọc Claude Code CLI thành HTTP service. |

`claude_gateway` cố tình không publish port: mọi request tới nó **bắt buộc** đi
qua lớp kiểm tra đăng nhập của Superset.

### 2.2 File theo vai trò

| File | Vai trò |
| --- | --- |
| [superset/templates/tail_js_custom_extra.html](../superset/templates/tail_js_custom_extra.html) | Chèn `<link>`/`<script>` của panel vào mọi trang Superset (có `csp_nonce`). |
| [superset/static/vdt-ai-chat.js](../superset/static/vdt-ai-chat.js) | Toàn bộ UI panel: đa hội thoại, gửi câu hỏi, render bảng/preview/badge. |
| [superset/static/vdt-ai-chat.css](../superset/static/vdt-ai-chat.css) | Style panel. |
| [superset/superset_config.py](../superset/superset_config.py) | Config Superset + Blueprint `/api/v1/vdt-ai-chat` (auth + proxy + static). |
| [claude_gateway/gateway_server.py](../claude_gateway/gateway_server.py) | HTTP server, spawn `claude -p`, parse stream-json, gom preview/timing. |
| [claude_gateway/render_config.py](../claude_gateway/render_config.py) | Render template MCP config bằng env var lúc khởi động. |
| [claude_gateway/mcp_servers.json.template](../claude_gateway/mcp_servers.json.template) | Khai báo MCP server + **danh sách env var truyền xuống** nó. |
| [claude_gateway/role_prompt.md](../claude_gateway/role_prompt.md) | Phần role của system prompt (tool nào dùng khi nào, format trả lời). |
| [mcp_server.py](../mcp_server.py) | 7 MCP tool: 3 đọc Postgres, 4 ghi Superset REST. |
| [generate_data.py](../generate_data.py) | Sinh mock data Daily FTE. |

### 2.3 System prompt được ghép lúc build

[claude_gateway/Dockerfile](../claude_gateway/Dockerfile) dòng 25:

```
cat role_prompt.md mock_data_docs.md > /app/system_prompt.md
```

Nên **sửa `role_prompt.md` hoặc `mock_data_docs.md` đều phải rebuild image**
`claude_gateway` mới có tác dụng. File này được nạp qua `--append-system-prompt`
ở mỗi lượt gọi (`_system_prompt()`, đọc lại từ đĩa mỗi lần).

### 2.4 7 MCP tool

| Tool | Đích | Ghi chú |
| --- | --- | --- |
| `list_datasets` | Postgres | Liệt kê bảng trong schema. |
| `describe_table` | Postgres | Cột + kiểu dữ liệu. |
| `run_sql_readonly` | Postgres | Chỉ `SELECT`/`WITH`, tự chèn `LIMIT` nếu thiếu. |
| `create_dataset` | Superset REST | Get-or-create, tự tìm/tạo Database connection. |
| `create_chart` | Superset REST | Dựng `params` theo `viz_type`. |
| `update_chart` | Superset REST | Đọc `params` hiện tại rồi merge — tham số `None` giữ nguyên. |
| `create_dashboard` | Superset REST | Tạo dashboard rồi `PUT` từng chart gắn vào. |

---

## 3. Luồng chính: một câu hỏi từ đầu tới cuối

### Bước 1 — Panel gắn vào trang

`vdt-ai-chat.js` chỉ mount khi `location.pathname` khớp `/dashboard*` hoặc
`/sqllab*` (`allowedRoute`). Vì Superset là SPA, một `MutationObserver` chạy lại
`start()` mỗi lần DOM đổi, để panel ẩn/hiện đúng khi điều hướng nội bộ.

### Bước 2 — Người dùng gửi câu hỏi

`form.onsubmit` đẩy message vào thread đang mở, lưu `localStorage`, hiện dòng
"Đang phân tích dữ liệu…" rồi `fetch`:

```json
POST /api/v1/vdt-ai-chat/query      (credentials: same-origin)
{ "question": "...", "session_id": "<uuid của thread>",
  "variant": "baseline", "context": { "path": "/dashboard/list/" },
  "row_limit": 200 }
```

`session_id` **gắn với thread, không phải với lần load trang** — đây là thứ giữ
cho hội thoại được `--resume` đúng sau khi F5.

### Bước 3 — Superset xác thực và proxy

Trong [superset_config.py](../superset/superset_config.py):
1. `_require_login()` — chưa đăng nhập → `401`, dừng tại đây.
2. Ghi đè `context.superset_user = current_user.username` (client không tự khai
   được danh tính).
3. `urlopen` sang `http://claude_gateway:8090/api/v1/agent/query`, **timeout 170s**.
4. Trả nguyên `response.read()` về browser → mọi key mới của gateway
   (`previews`, `timing`, `variant`) tự đi qua, không cần sửa proxy.

Blueprint được `csrf.exempt` vì extension chạy trong session web thường không
lấy được CSRF token kiểu bearer của Superset; bù lại nó đã bị chặn bởi bước 1.

### Bước 4 — Gateway chuẩn hoá request

`do_POST` (chỉ nhận đúng path `/api/v1/agent/query`):
- `question` rỗng → `400`.
- `session_id` không khớp regex 36 ký tự hex-dash → sinh `uuid4()` mới.
- `variant` không thuộc `("baseline", "skills")` → về `baseline`.

### Bước 5 — Khoá session & quyết định resume

`_query_claude`:
- Lấy lock theo `session_id` (`_lock_for_session`) — chặn double-send tạo 2 tiến
  trình `claude` cùng ghi một transcript.
- `resume = session_id in _known_sessions` (set in-memory).
- Nếu CLI báo sai chế độ (`is already in use` / `No conversation found`) thì
  **thử lại chế độ ngược lại**. Cần thiết vì `_known_sessions` mất sau khi
  gateway restart, trong khi browser vẫn giữ `session_id` cũ.

### Bước 6 — Chạy Claude Code CLI headless

`_build_argv` dựng:

```
claude -p
  --output-format stream-json --verbose
  --mcp-config /app/mcp_servers.json --strict-mcp-config
  --allowedTools    mcp__superset-postgres__{7 tool}
  --disallowedTools Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task
  --permission-mode dontAsk
  --append-system-prompt "<nội dung system_prompt.md>"
  [--plugin-dir /app/claude_gateway/skills_plugin]     # chỉ variant "skills"
  (--session-id | --resume) <session_id>
  "<prompt>"
```

Prompt được thêm header context: `[Superset user: …] [Page: …] [row_limit: …]`.

Tiến trình chạy với `start_new_session=True` để CLI và mọi tiến trình con nằm
chung một process group. Một `threading.Timer` 150s (`DEADLINE_SECONDS`) đóng vai
watchdog: hết giờ thì `killpg` cả group. Phải kill cả group vì vòng lặp đọc
`for line in proc.stdout` chỉ kết thúc khi **người ghi cuối cùng** vào pipe chết
— một MCP server còn sống là đủ để treo vòng đọc.

> **Thang timeout:** 150s (gateway) < 170s (proxy Superset). Cố ý xếp vậy để
> gateway luôn hết giờ trước và trả JSON lỗi tử tế, thay vì Flask trả HTML mà
> frontend không `JSON.parse` được.

### Bước 7 — CLI khởi động MCP server

CLI đọc `/app/mcp_servers.json` (do `render_config.render()` sinh ra từ template
lúc gateway start) và spawn `python3 /app/mcp_server.py` qua **stdio**.

Hệ quả quan trọng: MCP server **chỉ nhận đúng các biến môi trường liệt kê trong
template**, không kế thừa toàn bộ env của container. Thêm env var mới cho
`mcp_server.py` mà quên khai trong `mcp_servers.json.template` thì nó sẽ không
thấy biến đó.

### Bước 8 — Agent gọi tool

Ví dụ *"vẽ pie chart FTE theo phòng ban"*: `create_dataset` → `create_chart`.

Nhóm tool ghi xác thực bằng `_superset_session()`: login `/api/v1/security/login`
lấy JWT, lấy CSRF token, set `Referer`, cache session **600s**, retry 3 lần với
backoff (Superset khởi động chậm hơn gateway).

### Bước 9 — Gateway đọc stream-json

Với mỗi dòng JSON:
- `_log_event` — log `tool_use` / `tool_result` (xem bằng `docker compose logs -f claude_gateway`).
- `_collect_preview` — ghép `tool_use.id` ↔ `tool_result.tool_use_id`; chỉ giữ
  kết quả của `create_chart`/`update_chart`/`create_dashboard`, parse JSON, và
  chỉ nhận dict có `embed_url`. Kết quả của tool đọc cũng chạy qua đây nhưng tự
  rớt vì không có `embed_url`. Hàm trả về số tool call để đếm cho `timing`.
- `type: "result"` — chốt `answer`, `is_error`, `duration_ms`.

`is_error=true` được coi là **lỗi**, không phải câu trả lời (ví dụ
"Invalid API key" cũng nằm trong field `result`).

### Bước 10 — Response

```json
{ "answer": "…",
  "previews": [ { "type": "chart", "chart_id": 20,
                  "url": "http://localhost:8088/explore/?slice_id=20",
                  "embed_url": "http://localhost:8088/explore/?slice_id=20&standalone=1" } ],
  "timing": { "total_ms": 8421, "first_event_ms": 1180,
              "tool_calls": 2, "cli_duration_ms": 8100, "variant": "baseline" },
  "variant": "baseline" }
```

Lỗi → `502` với `{"message", "timing", "variant"}`.

### Bước 11 — Panel render

`renderMessage` dựng bằng `createElement`/`textContent` (không nối chuỗi vào
`innerHTML`) vì `answer` là text do model viết:
- `data.sql` → khối `<pre>` + nút copy.
- `data.columns`/`data.rows` → bảng.
- `data.previews` → iframe cùng-origin, có 2 tab **Preview** (`embed_url`) và
  **Explore** (`url`, tự phóng to panel), kèm link mở tab mới.
- `data.timing`/`data.variant` → badge `⏱ 8.4s · 2 tool`.

`isHttpUrl` chặn scheme lạ (`javascript:`) lọt vào `src`/`href`.

Cả object `result` được lưu làm `message.data` trong `localStorage`, nên reload
trang là preview và badge tự dựng lại — không cần gọi lại gateway.

---

## 4. Các luồng phụ

### 4.1 Hội thoại & session

| Cấp | Định danh | Lưu ở đâu |
| --- | --- | --- |
| Thread (UI) | `thread.id` | `localStorage['vdt-ai-chat-threads-v1']`, tối đa 20 thread × 60 message |
| Hội thoại Claude | `thread.session_id` | Trình duyệt gửi lên; transcript nằm trong volume `claude_home` |

Vì transcript nằm ở volume `claude_home` (không mất khi rebuild) còn
`_known_sessions` chỉ nằm trong RAM, nên sau mỗi lần restart gateway, lượt đầu
tiên của thread cũ sẽ đi qua nhánh retry ở bước 5 — chậm thêm ~1 giây, đúng như
thiết kế.

### 4.2 A/B baseline vs skills

`variant` cố định theo thread (chọn khi tạo thread mới) và **không đổi giữa
chừng** — vì `--resume` sẽ nối tiếp một transcript mà model đã nhớ, đổi tool/prompt
giữa chừng thì kết quả không còn giải thích được.

Muốn đối chiếu 2 variant thì tạo 2 thread — mỗi thread một variant — rồi hỏi
cùng một câu. Nút **So sánh** (chạy song song cả 2 variant trong một lượt) đã bị
gỡ khỏi UI.

### 4.3 Hai URL Superset — đừng nhầm

| Biến | Giá trị | Dùng cho |
| --- | --- | --- |
| `SUPERSET_URL` | `http://superset:8088` | REST call **từ trong** Docker network |
| `SUPERSET_PUBLIC_URL` | `http://localhost:8088` | Mọi thứ **trình duyệt** phải load: `url`, `embed_url` |

Trong `mcp_server.py`, chỉ `_chart_urls`/`_dashboard_urls` dùng
`SUPERSET_PUBLIC_URL`; toàn bộ `sess.get/post/put` vẫn dùng `SUPERSET_URL`. Deploy
ngoài localhost thì bắt buộc override `SUPERSET_PUBLIC_URL`.

Tham số `standalone`: chart dùng `1`, dashboard dùng `2` — hai route đọc tham số
này theo 2 kiểu khác nhau (boolean vs enum), đã kiểm chứng trên chính build
Superset 6.1.0 đang chạy.

---

## 5. Các lớp bảo vệ

Xếp từ ngoài vào:

1. **Đăng nhập Superset** — `POST /query` yêu cầu `current_user.is_authenticated`.
2. **Cô lập mạng** — gateway không publish port, chỉ tới được từ trong network.
3. **Danh sách tool** — `--allowedTools` chỉ mở 7 MCP tool;
   `--disallowedTools` khoá Bash/Read/Write/Edit/WebFetch…; `--strict-mcp-config`
   chặn nạp MCP server ngoài file chỉ định.
4. **Chặn SQL ghi** — `_is_readonly_sql` chỉ cho `SELECT`/`WITH` và từ chối mọi
   token DDL/DML, bất kể model yêu cầu gì. `_enforce_limit` chặn trên
   `MCP_MAX_ROWS=500`.
5. **Sanitize phía render** — `createElement`/`textContent` + `isHttpUrl`.

**Điểm đánh đổi đã biết:** tool ghi dùng **một service account cố định**
(`SUPERSET_ADMIN_USERNAME`/`PASSWORD`, mặc định `admin`/`admin`). Mọi chart do
agent tạo đều thuộc sở hữu tài khoản đó, không phải người đang chat — đây là
tradeoff của bản V1, không phải per-user identity forwarding.

---

## 6. Biến môi trường

| Biến | Service | Mặc định | Ghi chú |
| --- | --- | --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | gateway | *(bắt buộc)* | Sinh bằng `claude setup-token`; trừ quota subscription (Pro/Max) của tài khoản đó, không phải API key trả theo token. Compose fail ngay nếu thiếu. |
| `DATABASE_URL` | gateway → MCP | `postgresql://super_user:…@postgres:5432/super_db` | |
| `MCP_MAX_ROWS` | gateway → MCP | `500` | Trần cứng của `run_sql_readonly`. |
| `SUPERSET_URL` | gateway → MCP | `http://superset:8088` | REST nội bộ. |
| `SUPERSET_PUBLIC_URL` | gateway → MCP | `http://localhost:8088` | Cho trình duyệt. |
| `SUPERSET_ADMIN_USERNAME` / `_PASSWORD` | gateway → MCP | `admin` / `admin` | Service account của tool ghi. |
| `CLAUDE_DEADLINE_SECONDS` | gateway | `150` | Phải nhỏ hơn timeout 170s của proxy. |
| `CLAUDE_GATEWAY_PORT` | gateway | `8090` | |
| `AGENT_GATEWAY_URL` | superset | `http://claude_gateway:8090` | |

Các biến đánh dấu "gateway → MCP" muốn tới được `mcp_server.py` thì **phải có
tên trong `mcp_servers.json.template`** (xem bước 7).

---

## 7. Build & deploy

Không service nào mount code qua volume — **mọi thứ được COPY vào image lúc
build**. Nên sau khi sửa code luôn phải rebuild:

| Sửa file | Lệnh |
| --- | --- |
| `mcp_server.py`, `gateway_server.py`, `role_prompt.md`, `mock_data_docs.md`, `mcp_servers.json.template` | `docker compose up -d --build claude_gateway` |
| `superset_config.py`, `vdt-ai-chat.js/.css`, `templates/` | `docker compose up -d --build superset` |
| `docker-compose.yml` (env) | `docker compose up -d` |

Volume giữ state: `postgres_data` (dữ liệu), `superset_home` (metadata Superset:
chart/dashboard/dataset), `claude_home` (transcript hội thoại).

Rebuild `claude_gateway` sẽ kéo theo restart `superset` (do `depends_on`), và
Superset mất ~30–60s để healthy — REST call từ gateway sẽ `Connection refused`
trong khoảng đó. Đợi `curl -sf http://localhost:8088/health` trả OK rồi hãy test.

`vdt-ai-chat.js/.css` được phục vụ với `Cache-Control: no-cache` (đè mặc định
`max-age` 1 năm của Superset) vì tên file không đổi giữa các bản build. Trình
duyệt đã cache bản **trước** thay đổi này cần một lần Ctrl+Shift+R.

---

## 8. Sửa ở đâu khi gặp lỗi

| Triệu chứng | Nơi cần nhìn |
| --- | --- |
| Chart tạo ra render sai/thiếu metric | `_build_chart_params` trong [mcp_server.py](../mcp_server.py) — xem §9 |
| Preview iframe trắng / nhảy ra trang login | Session Superset trong browser; `standalone=` trong `_chart_urls`/`_dashboard_urls` |
| Link preview không mở được | `SUPERSET_PUBLIC_URL` đang trỏ sai |
| "AI gateway timed out" | `DEADLINE_SECONDS`; đọc log `docker compose logs -f claude_gateway` xem kẹt ở tool nào |
| Panel không hiện | Đang ở route ngoài `/dashboard*`, `/sqllab*`; hoặc browser cache JS cũ |
| Sửa prompt mà agent không đổi hành vi | Quên rebuild `claude_gateway` (prompt bake vào image) |
| MCP tool không thấy env var mới | Thiếu khai báo trong `mcp_servers.json.template` |
| Agent trả lời có bảng Markdown vỡ layout | Quy tắc format trong `role_prompt.md` (panel chỉ render backtick đơn + xuống dòng) |

---

## 9. Cạm bẫy đã biết: `params` của chart

`create_chart` dựng `params` thủ công cho một tập `viz_type` được chọn sẵn, và
**mỗi viz_type đọc form_data theo một kiểu khác nhau** — đây là nguồn lỗi hay gặp
nhất của hệ thống:

| `viz_type` | Field metric | Field chiều | Số metric |
| --- | --- | --- | --- |
| `table` | `metrics` (số nhiều) | `groupby` | nhiều |
| `echarts_timeseries_bar` / `_line` | `metrics` (số nhiều) | `x_axis` + `groupby` (phần còn lại) | nhiều |
| `pie` | **`metric` (số ít)** | `groupby` | **1** |
| `big_number_total` | **`metric` (số ít)** | *(không có)* | **1** |

Pie và big_number chỉ nhận **một** metric, và đọc field `metric` số ít — truyền
`metrics` số nhiều thì chart render rỗng. `_extract_chart_spec` là hàm nghịch đảo
của `_build_chart_params`, dùng cho `update_chart` giữ lại field mà caller không
truyền; sửa một trong hai hàm thì phải sửa hàm còn lại tương ứng.

Khi thêm `viz_type` mới: dựng thử một chart bằng tay trong UI Superset, mở
DevTools xem payload `POST /api/v1/chart/` thật, rồi map lại cho khớp — đừng đoán
tên field.

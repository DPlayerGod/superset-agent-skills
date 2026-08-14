# Chatbox đa hội thoại + Preview/Explore + đo E2E + Baseline vs Agent Skills

## Trạng thái: ĐÃ TRIỂN KHAI (2026-08-14)

Cả 4 mục A/B/C/D đã code xong, cùng với toàn bộ `preview_feature.md`. Các giả
định trong doc này đã được **kiểm chứng thực tế** (không còn là giả định):

| Câu hỏi Bước 0 | Kết quả đo được |
| --- | --- |
| Iframe cùng-origin có bị chặn? | **Không.** Route embed trả `200`, `X-Frame-Options: SAMEORIGIN`, CSP **không có** `frame-ancestors` → không cần sửa `superset_config.py`. |
| Giá trị `standalone=` đúng? | Explore xử lý như boolean (`superset/utils/core.py`: mọi giá trị khác `0`/`false`) → dùng `1`. Dashboard đọc enum số trong bundle JS: `None=0, HideNav=1, HideNavAndTitle=2, Report=3` → dùng `2`. |
| Agent Skills chạy được ở headless `-p`? | **Được**, qua `--plugin-dir` (CLI 2.1.197). Event `init` báo `plugins: [{name, path, source: '<name>@inline'}]` và liệt kê skill trong `skills`. |
| Có cần lock toàn cục (phương án dự phòng)? | **Không.** `--plugin-dir` áp dụng theo từng lần gọi, không đụng state chung trên đĩa → 2 variant chạy song song được, `_lock_for_session` giữ nguyên. |

Khác biệt so với thiết kế ban đầu:
- **Baseline = argv y hệt hiện tại** (không thêm cờ nào), `skills` = baseline +
  `--plugin-dir`. Ban đầu định dùng `--disable-slash-commands` cho baseline,
  nhưng như vậy baseline sẽ khác hiện trạng (CLI có sẵn skill built-in như
  `code-review`, `deep-research`). Để đúng yêu cầu "phần hiện tại là baseline"
  và để phép so sánh chỉ đổi **một** biến, baseline giữ nguyên không cờ; skill
  built-in có mặt ở cả 2 nhánh nên tự triệt tiêu khi so sánh.
- Nội dung skill thật **chưa viết** (người dùng sẽ tự thêm sau). Toàn bộ hạ tầng
  A/B đã sẵn sàng; thư mục `claude_gateway/skills_plugin/skills/` hiện chỉ có
  README hướng dẫn.
- `timing` có thêm `cli_duration_ms` — chính CLI đã báo `duration_ms` trong event
  `result`, dùng kèm số wall-clock của gateway để đối chiếu.

### 3 lỗi phát hiện khi triển khai (đều đã sửa)

1. **`session_id` sinh lại mỗi lần load trang** (`vdt-ai-chat.js` cũ, dòng 4):
   `--resume` chưa bao giờ thật sự resume sau reload. Mô hình thread đã sửa —
   mỗi thread giữ `session_id` cố định trong `localStorage`.
2. **`DEADLINE_SECONDS` không có tác dụng khi lượt chạy bị treo im lặng.** Cũ:
   kiểm tra deadline **bên trong** `for line in proc.stdout`, mà vòng lặp này
   block chờ dòng tiếp theo → API không phản hồi thì gateway chờ vô hạn (đã tái
   hiện thực tế khi container mất egress: CLI in event `init` rồi đứng im).
   Sửa: `threading.Timer` watchdog + `start_new_session` và `os.killpg` — phải
   kill cả process group, vì tiến trình con thừa kế stdout sẽ giữ pipe mở khiến
   vòng lặp vẫn đứng dù đã kill tiến trình cha. Đo được: cắt đúng deadline, và
   toàn tuyến qua proxy Superset trả 502 kèm `timing` sau đúng 150.1s.
3. **Thread bền vững làm lộ lỗi "Session ID is already in use".** `--session-id`
   bị CLI từ chối nếu transcript đã tồn tại; `--resume` bị từ chối nếu chưa có.
   Gateway chỉ nhớ session trong RAM (`_known_sessions`), nên **sau mỗi lần
   restart gateway**, mọi thread cũ (giờ đã bền vững) sẽ hỏng vĩnh viễn. Sửa:
   bắt đúng 2 chuỗi lỗi của CLI rồi thử lại theo chiều ngược lại — tốn ~1s vì
   CLI từ chối trước khi gọi API; timeout **không** khớp regex nên không bị thử
   lại (tránh nhân đôi độ trễ). Đã kiểm chứng với CLI thật.

## Quan hệ với `preview_feature.md`

Doc này **build trên** `preview_feature.md` (không thay thế). Cụ thể:
- Mục B (Preview/Explore toggle) đọc `preview.url` / `preview.embed_url` — 2 field do
  `preview_feature.md` thêm vào response của `create_chart`/`update_chart`/`create_dashboard`.
  Nếu `preview_feature.md` chưa triển khai, Mục B **không có dữ liệu để dùng**.
- Cả 2 doc cùng sửa `add()` trong `superset/static/vdt-ai-chat.js` và cùng thêm class
  `.vdt-ai-preview*` trong `vdt-ai-chat.css` → khi code thật, merge theo thứ tự:
  `preview_feature.md` trước, `chat_experiment_feature.md` sau (Mục B chỉ thêm 1 thanh
  tab nhỏ vào trong khối `.vdt-ai-preview` đã có, không viết lại khối đó).
- Mục A, C, D độc lập với `preview_feature.md`, có thể làm trước/sau/song song.

## Bối cảnh hiện tại (đã đọc code, không suy đoán)

`superset/static/vdt-ai-chat.js` hiện tại:
- `sessionId = crypto.randomUUID()` được sinh **một lần khi script load** (top-level,
  dòng 4) — mỗi lần người dùng **reload trang** là một `sessionId` mới.
- `gateway_server.py._query_claude` dùng `sessionId` này làm `claude --session-id`/
  `--resume`. Vì `sessionId` đổi mỗi lần reload, **`--resume` chỉ có tác dụng trong
  cùng một lượt mở trang** (nhiều câu hỏi liên tiếp không reload) — reload xong là
  Claude bắt đầu conversation mới ở backend, dù UI vẫn hiển thị lại toàn bộ lịch sử cũ.
- Lịch sử lưu ở `localStorage['vdt-ai-chat-v1']` là **một mảng phẳng duy nhất**
  (`messages.slice(-30)`), không có khái niệm nhiều cuộc hội thoại (thread) tách biệt,
  không có tiêu đề, không lọc/chuyển được giữa các phiên chat trước.
- Panel có kích thước cố định: `width: min(430px, ...)`, `height: min(640px, ...)`
  (`vdt-ai-chat.css:4`), không resize/maximize được.
- Không có bất kỳ số đo thời gian nào (client hay server) cho một lượt hỏi-đáp.
- `role_prompt.md` là system prompt **cố định duy nhất** (`--append-system-prompt`),
  `ALLOWED_TOOLS`/`MCP_CONFIG_PATH` cũng cố định — không có khái niệm "biến thể"
  (baseline vs có thêm Agent Skills). Claude Code CLI Agent Skills (`SKILL.md` tự
  động discover) **chưa được xác nhận là hoạt động ở chế độ headless `-p`** trong
  version CLI đang cài (`2.1.197`, theo docstring `gateway_server.py:9-16`) — phải
  kiểm tra thực nghiệm trước (xem Bước 0 ở Mục D).

## Mục tiêu 4 phần

1. **A** — Chatbox mở rộng (maximize) + lưu/chuyển đổi nhiều đoạn chat trước.
2. **B** — Mỗi preview có 2 chế độ xem: Preview (compact) và Explore (đầy đủ).
3. **C** — Đo thời gian E2E hoàn thành 1 truy vấn, hiển thị cho người dùng.
4. **D** — Kiến trúc baseline (hiện tại) vs gắn thêm Agent Skills: chuyển đổi được
   theo từng đoạn chat, và so sánh trực tiếp (chạy song song, xem cạnh nhau).

---

## A. Nhiều đoạn chat (threads) + phóng to panel

### Quyết định phạm vi
- Thay lưu trữ phẳng bằng **danh sách thread**, mỗi thread có `session_id` riêng
  và **cố định** (không đổi khi reload) → đây cũng là **fix bug** "resume không
  thật sự resume sau reload" nêu ở trên.
- "Mở rộng" implement bằng **toggle 2 kích thước cố định** (compact ↔ maximized),
  không làm free-drag-resize ở v1 — free-resize phải xử lý thêm: giới hạn min/max,
  lưu kích thước tuỳ ý vào localStorage, reflow iframe bên trong khi kéo — rủi ro
  bug cao hơn nhiều so với lợi ích ở giai đoạn này. Có thể làm sau như nâng cấp.

### Data model mới — `localStorage['vdt-ai-chat-threads-v1']`
```json
{
  "active_thread_id": "<uuid>",
  "threads": [
    {
      "id": "<uuid>",
      "session_id": "<uuid dùng cho claude --session-id>",
      "title": "<50 ký tự đầu của câu hỏi đầu tiên>",
      "variant": "baseline",
      "created_at": "2026-08-13T10:00:00Z",
      "updated_at": "2026-08-13T10:05:00Z",
      "messages": [ { "role": "user"|"agent", "content": "...", "data": {...} } ]
    }
  ]
}
```
- Giới hạn: tối đa **20 thread** (xoá thread cũ nhất khi vượt), mỗi thread tối đa
  **60 message** (thay cho giới hạn 30 message toàn cục hiện tại) — tránh
  localStorage phình to (quota trình duyệt thường ~5–10MB).
- Migration: nếu phát hiện key cũ `vdt-ai-chat-v1` còn tồn tại (người dùng cũ), gói
  nguyên mảng đó thành 1 thread duy nhất `variant: "baseline"`, `session_id` sinh
  mới (không thể phục hồi session cũ vì trước đây không lưu), rồi xoá key cũ.

### UI mới trong `vdt-ai-chat.js`
- Header (`.vdt-ai-header`) thêm 3 nút, cạnh nút đóng hiện có:
  - `+` — New chat: tạo thread mới (mở dropdown chọn `variant` nếu Mục D đã có,
    mặc định `baseline`).
  - `☰` — History: mở dropdown liệt kê thread theo `updated_at` giảm dần
    (`title`, thời gian tương đối, badge `variant`), click để `switchThread(id)`
    (render lại `.vdt-ai-history` từ `thread.messages`, không gọi API).
  - `⤢` — Maximize/restore: toggle class `vdt-ai-panel--max` trên `.vdt-ai-panel`.
- Không đổi hành vi nút `×` (đóng) hiện có.

### CSS mới
```css
.vdt-ai-panel--max { width: min(1100px, calc(100vw - 32px)) !important; height: min(88vh, 900px) !important; }
.vdt-ai-thread-menu { position: absolute; top: 52px; right: 16px; width: 260px; max-height: 320px; overflow: auto; background: #172033; border: 1px solid #334155; border-radius: 8px; }
.vdt-ai-thread-item { padding: 8px 10px; cursor: pointer; border-bottom: 1px solid #223049; font-size: 12px; }
.vdt-ai-thread-item:hover { background: #1e293b; }
.vdt-ai-thread-item .badge { font-size: 10px; padding: 1px 6px; border-radius: 10px; background: #334155; margin-left: 6px; }
```

### Verification
1. Gửi 2 câu hỏi liên tiếp trong 1 thread, reload trang, gửi câu hỏi thứ 3 —
   `docker compose logs -f claude_gateway` phải log `exec claude (resume, session=...)`
   với **cùng** `session_id` như trước reload (khác hành vi hiện tại).
2. Tạo 3 thread, xác nhận dropdown History liệt kê đủ 3, click chuyển qua lại giữ
   đúng nội dung từng thread.
3. Tạo >20 thread (script test), xác nhận thread cũ nhất bị xoá, localStorage
   không lỗi quota.
4. Bấm maximize, xác nhận panel lớn lên và iframe preview (nếu có) không vỡ layout;
   bấm lại để restore.

---

## B. Preview vs Explore (mở rộng khối preview của `preview_feature.md`)

### Ý tưởng
`preview_feature.md` đã tính sẵn 2 URL cho mỗi chart/dashboard: `embed_url`
(standalone, dùng cho iframe compact) và `url` (trang Superset đầy đủ, có nav +
công cụ chỉnh sửa — với chart chính là trang `/explore/`). Thay vì chỉ có link
"Mở trong Superset ↗" mở tab mới, thêm 1 thanh tab nhỏ ngay trong khối preview để
**đổi `iframe.src` tại chỗ** giữa 2 chế độ, không rời khỏi chat:
- **Preview** (mặc định): `iframe.src = preview.embed_url` — như `preview_feature.md`.
- **Explore**: `iframe.src = preview.url` — trang đầy đủ, cho phép tương tác/chỉnh
  sửa trực tiếp ngay trong panel.

### Thay đổi `vdt-ai-chat.js` (trong khối render preview đã có)
```js
const tabs = document.createElement('div');
tabs.className = 'vdt-ai-preview-tabs';
['preview', 'explore'].forEach(mode => {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = mode === 'preview' ? 'Preview' : 'Explore';
  btn.className = mode === 'preview' ? 'active' : '';
  btn.onclick = () => {
    iframe.src = mode === 'preview' ? preview.embed_url : preview.url;
    tabs.querySelectorAll('button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  };
  tabs.append(btn);
});
wrap.insertBefore(tabs, iframe); // chèn trước iframe, sau label
```

### CSS thêm
```css
.vdt-ai-preview-tabs { display: flex; gap: 4px; padding: 4px 10px; background: #172033; }
.vdt-ai-preview-tabs button { border: 0; background: transparent; color: #b8c4d8; font-size: 11px; padding: 4px 8px; border-radius: 6px; cursor: pointer; }
.vdt-ai-preview-tabs button.active { background: #334155; color: #eef2ff; }
```

### Giả định cần xác minh (Bước 0 riêng cho Mục B)
Trang Explore đầy đủ (`/explore/?slice_id=<id>`, không có `standalone=`) nặng hơn
nhiều (top nav, control panel, save/edit buttons) — trong khung ~380–680px cao có
thể quá chật. Cần test tay: mở tab Explore trong panel compact, nếu chật thì **tự
động gọi maximize (Mục A)** khi người dùng bấm tab Explore lần đầu — thêm 1 dòng
`panel.classList.add('vdt-ai-panel--max')` trong `onclick` ở trên nếu xác nhận cần.

---

## C. Đo thời gian E2E của một truy vấn

### Quyết định
Đo **ở backend** (`claude_gateway`) làm nguồn sự thật chính, vì mục đích chính là
so sánh baseline vs skills (Mục D) — đo phía client thêm cả round-trip mạng, gây
nhiễu khi so sánh giữa 2 biến thể chạy cùng điều kiện mạng. Trả `timing` luôn có
mặt trong response (như `previews` ở `preview_feature.md`), rỗng/0 nếu lỗi.

### Thay đổi `claude_gateway/gateway_server.py`
- Trong `_run_claude`, ngay trước `subprocess.Popen`: `t0 = time.monotonic()`.
- Trong vòng lặp đọc `proc.stdout`, lần đầu tiên `event` được parse thành công:
  ghi `t_first_event` (nếu chưa có). Đếm số `tool_use` block (`tool_calls += 1`)
  cùng chỗ với `_log_event`.
- Khi gặp `event.get("type") == "result"`: tính
  ```python
  timing = {
      "total_ms": round((time.monotonic() - t0) * 1000),
      "first_event_ms": round((t_first_event - t0) * 1000) if t_first_event else None,
      "tool_calls": tool_calls,
  }
  ```
- Đổi chữ ký `_run_claude` thành trả về 4 giá trị:
  `(answer, previews, timing, error)` — nhánh timeout trả
  `(None, [], {"total_ms": round((time.monotonic()-t0)*1000), "first_event_ms": None, "tool_calls": tool_calls}, "...")`
  (vẫn muốn biết mất bao lâu mới timeout), nhánh lỗi exit code và nhánh thành công
  tương tự bổ sung `timing`.
- `_query_claude` và `do_POST`: unpack/return theo 4-tuple, `do_POST` trả
  `self._reply(200, {"answer": ..., "previews": ..., "timing": timing})`.

### Thay đổi `vdt-ai-chat.js`
- Trong `add()`, nếu `data?.timing?.total_ms` tồn tại, thêm 1 `<span>` nhỏ cuối
  message agent: `⏱ 3.2s` (`(data.timing.total_ms / 1000).toFixed(1)`).
- Lưu `timing` vào `message.data` khi `push` vào `thread.messages` như các field
  khác (`sql`, `columns`, `rows`, `previews`) — tự động round-trip qua
  localStorage, không cần code riêng.

### Verification
Test độc lập không cần gọi `claude` CLI thật, tương tự cách `preview_feature.md`
test `_collect_preview` — mock 1 chuỗi event JSON có `result` ở dòng cuối, chạy
qua `_run_claude`-shaped logic tách timing ra hàm riêng `_extract_timing(events, t0)`
để test được mà không cần subprocess thật. Sau đó test tay: hỏi 1 câu, xác nhận
badge `⏱` hiện đúng số giây hợp lý (so với thời gian chờ thực tế bằng đồng hồ tay).

---

## D. Baseline vs Agent Skills — chuyển đổi & so sánh

### Bước 0 — Xác minh thực nghiệm (BẮT BUỘC, làm trước khi thiết kế chi tiết hơn)
Chưa xác nhận Claude Code CLI 2.1.197 hỗ trợ Agent Skills ở chế độ headless
(`-p`), và nếu có thì:
1. `docker compose exec -T claude_gateway claude --help` — tìm flag liên quan
   skills (`--skills-dir`, `--disable-skills`, hoặc tương tự); ghi lại tên chính
   xác, vì docstring `gateway_server.py:9-16` đã cảnh báo phải re-verify `--help`
   mỗi khi cần flag mới, không được đoán.
2. Nếu không có flag, tìm đường dẫn discover mặc định: thử tạo
   `/app/.claude/skills/demo/SKILL.md` (cwd của `claude` trong container là `/app`,
   theo `subprocess.Popen(..., cwd="/app")` ở `gateway_server.py:125`) VÀ thử
   `$HOME/.claude/skills/demo/SKILL.md` (volume `claude_home:/home/node/.claude`
   đã mount sẵn, theo `docker-compose.yml:63`) — SKILL.md tối thiểu:
   ```markdown
   ---
   name: demo-skill
   description: Demo skill để xác minh discovery trong headless mode.
   ---
   Khi được hỏi "demo skill test", trả lời đúng chuỗi "DEMO_SKILL_ACTIVE".
   ```
3. Chạy `docker compose exec -T claude_gateway claude -p --output-format stream-json --verbose "demo skill test"`
   (không cần `--mcp-config` cho test này), xem model có trả `DEMO_SKILL_ACTIVE`
   và log có `tool_use: Skill(...)` hay tương đương không.
4. Xác nhận có cách **loại trừ hoàn toàn** skill khỏi 1 lần gọi cụ thể (để baseline
   đảm bảo publicly "sạch", không lẫn skill) — hoặc bằng flag ở bước 1, hoặc bằng
   cách không tạo file trong thư mục discover cho lượt đó.
5. Ghi kết quả 4 bước trên vào đầu mục D này trước khi code — nếu Agent Skills
   không hoạt động được ở headless mode với version CLI hiện tại, cả Mục D cần
   thiết kế lại (ví dụ: giả lập "skill" bằng cách nối thêm đoạn hướng dẫn vào
   `--append-system-prompt` thay vì dùng cơ chế Skill thật — vẫn so sánh được
   baseline vs "skills" nhưng không phải cơ chế Skill chính thức của CLI).

### Thiết kế (áp dụng SAU khi Bước 0 xác nhận cơ chế khả thi — giả sử có flag chọn
thư mục skill theo từng lần gọi; nếu không, xem phương án dự phòng bên dưới)

**Layout skill trong repo** (bake vào image, theo đúng pattern hiện tại — cả
`superset` và `claude_gateway` COPY code tĩnh lúc build, không mount volume code):
```
claude_gateway/skills/<skill-name>/SKILL.md
```

**API contract**: request thêm field `variant: "baseline" | "skills"`
(mặc định `"baseline"` nếu thiếu, để không phá client cũ). `do_POST` đọc field
này, truyền xuống `_query_claude(question, session_id, context, row_limit, variant)`
→ `_run_claude(..., variant)` → `_build_argv` chọn:
- `variant == "baseline"`: không trỏ tới `claude_gateway/skills/` (dùng flag từ
  Bước 0 để tắt hẳn, hoặc trỏ tới thư mục rỗng).
- `variant == "skills"`: trỏ tới `claude_gateway/skills/`.

Response trả kèm `"variant": variant` (echo lại) để frontend gắn nhãn đúng vào
message/thread.

**Phương án dự phòng** (nếu Bước 0 cho thấy không override được thư mục discover
theo từng lần gọi, chỉ có 1 đường dẫn cố định toàn container): gateway phải
materialize/xoá thư mục skill ngay trước/sau mỗi lần gọi subprocess, và bắt buộc
dùng **1 lock toàn cục** (không phải `_lock_for_session` hiện tại vốn chỉ khoá
theo từng session) bọc quanh *mọi* lời gọi `_run_claude` bất kể variant — vì 2
request "skills" và "baseline" chạy đồng thời sẽ giẫm lên cùng thư mục filesystem.
Đánh đổi: gateway mất khả năng xử lý song song hoàn toàn (hiện tại có thể chạy
nhiều session cùng lúc). Đây là lý do Bước 0 phải làm trước — quyết định kiến
trúc phụ thuộc hoàn toàn vào việc CLI có hỗ trợ chọn thư mục per-invocation
hay không.

### Ràng buộc UX: variant gắn theo thread, không đổi giữa chừng
- Chọn `variant` khi bấm "New chat" (Mục A) — dropdown 2 lựa chọn, mặc định
  `baseline`. Một thread đã tạo giữ nguyên `variant` suốt vòng đời (không cho đổi
  giữa chừng), vì `--session-id`/`--resume` gắn với 1 cấu hình system-prompt/tool
  cố định — đổi variant giữa chừng nghĩa là đổi "danh tính" của conversation đang
  resume, dễ tạo hành vi khó hiểu (model nhớ ngữ cảnh cũ nhưng đột ngột có/mất tool).
- Badge `variant` hiển thị trong dropdown History (Mục A) và trong mỗi message
  agent, cạnh badge thời gian (Mục C): `[baseline] ⏱ 3.2s`.

### Chế độ So sánh (Compare) — ĐÃ GỠ

> **Trạng thái: đã triển khai rồi gỡ bỏ.** Nút "So sánh" không còn trong
> `.vdt-ai-input`; `compareBtn.onclick`, `addComparePair` và toàn bộ CSS
> `.vdt-ai-compare*` đã bị xoá khỏi `vdt-ai-chat.js` / `vdt-ai-chat.css`. Cách
> đối chiếu 2 variant hiện nay: mở 2 thread, mỗi thread một variant, hỏi cùng một
> câu. Phần đặc tả dưới đây giữ lại làm tham chiếu nếu muốn dựng lại.

- Nút "So sánh" cạnh nút Gửi trong `.vdt-ai-input`. Khi bấm thay vì Gửi thường:
  gửi **2 request song song** tới cùng endpoint, khác `variant` (`baseline` và
  `skills`), mỗi request dùng **session_id tạm thời riêng** (không thuộc thread
  nào, không lưu vào danh sách thread — tránh trộn 2 luồng hội thoại khác nhau
  vào chung 1 `--resume` session).
  ```js
  compareBtn.onclick = async () => {
    const question = input.value.trim(); if (!question) return;
    input.value = ''; add('user', question);
    panel.classList.add('vdt-ai-panel--max'); // tự động phóng to để có chỗ 2 cột
    const run = variant => fetch('/api/v1/vdt-ai-chat/query', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, session_id: crypto.randomUUID(), variant, context: { path: location.pathname }, row_limit: 200 }) }).then(r => r.json());
    const [baseline, skills] = await Promise.all([run('baseline'), run('skills')]);
    addComparePair(baseline, skills); // render 2 cột cạnh nhau, mỗi cột = 1 message agent bình thường + badge variant/timing
  };
  ```
- `addComparePair` tái dùng logic render của `add()` (bảng/sql/preview/timing),
  chỉ khác là bọc 2 kết quả trong `<div class="vdt-ai-compare">` (CSS grid 2 cột,
  `grid-template-columns: 1fr` khi panel ở size compact — chỉ thật sự cạnh nhau
  khi đã maximize, nên bước tự động maximize ở trên là bắt buộc, không phải tuỳ
  chọn).
- Cặp so sánh **không tự lưu vào thread** ở v1 (tránh làm phức tạp data model
  Mục A với "message có 2 câu trả lời") — chỉ tồn tại trong phiên xem hiện tại,
  mất khi đổi thread/reload. Có thể nâng cấp sau nếu cần giữ lại.

### CSS thêm
```css
.vdt-ai-compare { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.vdt-ai-panel:not(.vdt-ai-panel--max) .vdt-ai-compare { grid-template-columns: 1fr; }
.vdt-ai-variant-badge { font-size: 10px; padding: 1px 6px; border-radius: 10px; background: #334155; color: #b8c4d8; margin-right: 6px; }
```

### Verification
1. Sau Bước 0: xác nhận baseline (không skill) và skills (có skill demo) cho ra
   khác biệt quan sát được — ví dụ hỏi đúng câu kích hoạt `DEMO_SKILL_ACTIVE` ở
   Bước 0, chỉ variant `skills` mới trả đúng chuỗi đó.
2. Test unit `_build_argv`/tương đương cho cả 2 variant (giống cách
   `preview_feature.md` test `_collect_preview` độc lập, không cần gọi CLI thật):
   xác nhận argv khác nhau đúng như thiết kế.
3. Tạo 1 thread `baseline`, 1 thread `skills`, xác nhận badge hiển thị đúng và
   không đổi được variant giữa chừng (không có UI nào cho phép).
4. Bấm "So sánh" với 1 câu hỏi cụ thể có dùng tool ghi (ví dụ tạo chart) — xác
   nhận panel tự maximize, 2 cột hiện cạnh nhau, mỗi cột có preview
   (nếu `preview_feature.md` đã xong) + badge variant + badge timing riêng, và
   2 giá trị `total_ms` khác nhau một cách hợp lý (nếu skill thêm bước xử lý,
   thường variant `skills` sẽ chậm hơn — không bắt buộc nhưng nên quan sát để
   sanity-check số đo).
5. Nếu rơi vào "phương án dự phòng" (lock toàn cục): test 2 request đồng thời
   khác variant không bị giẫm skill folder lên nhau (đọc log, xác nhận request
   thứ 2 đợi request thứ 1 xong mới chạy — chấp nhận được vì đây chỉ là chế độ
   Compare, tần suất thấp).

---

## File sẽ sửa/thêm (tổng hợp cả 4 mục)
- `superset/static/vdt-ai-chat.js` — thread model, maximize, history dropdown,
  preview/explore tab, timing badge, compare mode. (File lớn nhất, đụng nhiều
  nhất — nên code từng mục A→B→C→D tuần tự, test riêng từng mục trước khi gộp.)
- `superset/static/vdt-ai-chat.css` — style tương ứng từng mục.
- `claude_gateway/gateway_server.py` — timing (`_run_claude` 3→4 giá trị trả về),
  variant plumbing.
- `claude_gateway/skills/<name>/SKILL.md` — mới, chỉ sau khi Bước 0 (Mục D)
  xác nhận khả thi.
- `claude_gateway/Dockerfile` — nếu cần COPY thư mục `skills/` mới vào image.
- `ROLLOUT.md`, `task.md` — cập nhật tài liệu sau khi từng mục verify xong.

## Thứ tự triển khai đề xuất
1. **Bước 0 của Mục D trước tiên** — vì đây là rủi ro kiến trúc lớn nhất
   (nếu Agent Skills không chạy được ở headless mode, toàn bộ thiết kế Mục D
   phải viết lại) — không nên code A/B/C rồi mới phát hiện D bất khả thi và phải
   sửa lại `vdt-ai-chat.js` đã viết.
2. Mục C (timing) — nhỏ, độc lập, có ích ngay cả khi D chưa xong.
3. Mục A (threads + maximize) — nền tảng UI cho B và D.
4. Mục D (variant + compare) — cần A (thread variant) và C (timing badge) đã có.
5. Mục B (preview/explore tab) — cần `preview_feature.md` đã triển khai; có thể
   làm song song với 2-4 vì không phụ thuộc chúng.

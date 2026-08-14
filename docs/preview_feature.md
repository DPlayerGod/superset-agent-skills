# Live preview cho chart/dashboard trong AI Agent chat panel

## Context

Hiện tại khi agent tạo/sửa chart hoặc dashboard qua các MCP write tool
(`create_chart`, `update_chart`, `create_dashboard`), kết quả trả về cho người
dùng trong panel "AI Agent" chỉ là một dòng text chứa URL — và URL đó thực ra
**bị hỏng**: nó được dựng từ `SUPERSET_URL=http://superset:8088` (hostname nội
bộ Docker dùng để gọi Superset REST API từ bên trong container
`claude_gateway`), nên khi người dùng bấm/copy link đó trên trình duyệt sẽ
không truy cập được (`superset` không resolve được ngoài mạng Docker).

Người dùng muốn thấy ngay chart/dashboard vừa tạo mà không phải tự mở tab
Superset và tìm lại. Vì Superset không có sẵn Celery/Redis để render thumbnail
ảnh tĩnh (`THUMBNAILS` feature cần async worker, không tồn tại trong
`docker-compose.yml` hiện tại), giải pháp khả thi là **nhúng iframe live**,
cùng-origin với session Superset đã đăng nhập sẵn trong trình duyệt (chat
panel được Superset tự serve, nên cookie session tự động đi kèm iframe).

Quyết định phạm vi (đã hỏi người dùng):
- Preview áp dụng cho **cả chart và dashboard** (không chỉ chart).
- Preview **hiện ngay lập tức**, không thu gọn/toggle.
- Nếu một lượt tạo nhiều chart rồi tạo dashboard, **hiện tất cả preview theo
  đúng thứ tự tạo** (không ẩn bớt).

## Bước 0 — Xác minh thực nghiệm (làm TRƯỚC khi sửa code)

Tham số `standalone=` chính xác cho Superset 6.1.0 và việc header
`X-Frame-Options`/CSP có chặn iframe cùng-origin hay không **chưa được xác
nhận** — cần kiểm tra trực tiếp trước khi hardcode vào `mcp_server.py`:

1. Đăng nhập `http://localhost:8088` (admin/admin).
2. Mở URL chart có sẵn (`/explore/?slice_id=<id>`), thử lần lượt
   `&standalone=1`, `=2`, `=3`, `=true` — tìm giá trị ẩn được top nav nhưng
   vẫn giữ canvas chart.
3. Mở dashboard có sẵn (`/superset/dashboard/<id>/`), thử tương tự.
4. Trong DevTools Network tab, kiểm tra header `X-Frame-Options` và
   `Content-Security-Policy: frame-ancestors` trên response **document**
   (không phải các sub-request JS/CSS) của cả 2 route trên —
   `tail_js_custom_extra.html` đã dùng `csp_nonce()` nên Talisman/CSP có khả
   năng đang bật mặc định dù `superset_config.py` không set gì thêm.
5. Ghi lại 2 giá trị `standalone=` thắng cuộc để dùng ở Bước 3.
6. Nếu frame-ancestors chặn iframe cùng-origin (khó xảy ra nhưng phải kiểm
   tra, không giả định): fallback là thêm `TALISMAN_CONFIG`/`HTTP_HEADERS`
   trong `superset/superset_config.py` để cho phép `frame-ancestors 'self'`.

## Các thay đổi code

### 1. `docker-compose.yml`
Thêm biến môi trường mới cho service `claude_gateway`, ngay sau
`SUPERSET_URL: http://superset:8088`:
```yaml
SUPERSET_PUBLIC_URL: http://localhost:8088
```
Đây là base URL **dành cho trình duyệt** — tách biệt với `SUPERSET_URL` (nội
bộ, dùng để gọi REST API thật). Không đổi gì ở service `superset`.

### 2. `claude_gateway/mcp_servers.json.template`
`render_config.py` dùng `Template(...).substitute(os.environ)` — sẽ
`KeyError` nếu thêm `${VAR}` mà biến không tồn tại trong env của container.
Thêm 1 dòng trong object `"env"`, ngay sau `"SUPERSET_URL": "${SUPERSET_URL}"`:
```json
"SUPERSET_PUBLIC_URL": "${SUPERSET_PUBLIC_URL}",
```
(Bắt buộc — `mcp_server.py` chạy như stdio subprocess do `claude` CLI spawn,
chỉ nhận đúng các biến khai báo trong file này, không kế thừa toàn bộ env của
container cha.)

### 3. `mcp_server.py`
- Thêm gần dòng khai báo `SUPERSET_URL` (dòng 17):
  ```python
  SUPERSET_PUBLIC_URL = os.getenv("SUPERSET_PUBLIC_URL", SUPERSET_URL).rstrip("/")
  ```
- Thêm 2 helper (đặt cạnh `_extract_chart_spec`, dùng giá trị `standalone=`
  đã xác minh ở Bước 0):
  ```python
  def _chart_urls(chart_id: int) -> dict[str, Any]:
      return {
          "type": "chart",
          "url": f"{SUPERSET_PUBLIC_URL}/explore/?slice_id={chart_id}",
          "embed_url": f"{SUPERSET_PUBLIC_URL}/explore/?slice_id={chart_id}&standalone=<GIÁ_TRỊ_ĐÃ_VERIFY>",
      }

  def _dashboard_urls(dashboard_id: int) -> dict[str, Any]:
      return {
          "type": "dashboard",
          "url": f"{SUPERSET_PUBLIC_URL}/superset/dashboard/{dashboard_id}/",
          "embed_url": f"{SUPERSET_PUBLIC_URL}/superset/dashboard/{dashboard_id}/?standalone=<GIÁ_TRỊ_ĐÃ_VERIFY>",
      }
  ```
- `create_chart` (dòng ~309): đổi `return` thành
  `return {"chart_id": chart_id, **_chart_urls(chart_id)}`
- `update_chart` (dòng ~375): tương tự,
  `return {"chart_id": chart_id, **_chart_urls(chart_id)}`
- `create_dashboard` (dòng ~398-402): đổi thành
  `return {"dashboard_id": dashboard_id, **_dashboard_urls(dashboard_id), "chart_ids": chart_ids}`
- **Không đổi** bất kỳ lời gọi `sess.get/post/put(f"{SUPERSET_URL}/...")` nào
  khác — mọi REST call thật vẫn dùng `SUPERSET_URL` nội bộ.

### 4. `claude_gateway/gateway_server.py`
Hiện tại `_run_claude` chỉ log `tool_use`/`tool_result` rồi vứt đi, response
HTTP cuối chỉ có `{"answer": ...}`. Cần bắt kết quả của 3 write tool và trả
kèm theo dạng `"previews": [...]`.

- Thêm hằng số cạnh `ALLOWED_TOOLS`:
  ```python
  _PREVIEW_TOOLS = {"create_chart", "update_chart", "create_dashboard"}
  ```
- Thêm helper bóc prefix MCP (`mcp__superset-postgres__create_chart` →
  `create_chart`) và helper bóc text từ `tool_result.content` (có thể là
  string hoặc list block `{"type":"text","text":...}`):
  ```python
  def _short_tool_name(name: str) -> str:
      prefix = f"mcp__{MCP_SERVER_NAME}__"
      return name[len(prefix):] if name.startswith(prefix) else name

  def _tool_result_text(content: Any) -> str | None:
      if isinstance(content, str):
          return content
      if isinstance(content, list):
          parts = [b.get("text") for b in content
                   if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
          return "\n".join(parts) if parts else None
      return None
  ```
- Thêm hàm correlate `tool_use` id ↔ `tool_result`, chỉ giữ lại kết quả của
  3 write tool, parse JSON, bỏ qua mọi thứ không parse được (kết quả của
  `list_datasets`/`run_sql_readonly`/... đi qua cùng code path nhưng không có
  `embed_url` nên tự động bị loại):
  ```python
  def _collect_preview(event: dict[str, Any], pending: dict[str, str], previews: list[dict[str, Any]]) -> None:
      etype = event.get("type")
      for block in event.get("message", {}).get("content", []) or []:
          if not isinstance(block, dict):
              continue
          if etype == "assistant" and block.get("type") == "tool_use":
              tool_use_id = block.get("id")
              if tool_use_id:
                  pending[tool_use_id] = _short_tool_name(block.get("name", ""))
          elif etype == "user" and block.get("type") == "tool_result":
              name = pending.get(block.get("tool_use_id"))
              if name not in _PREVIEW_TOOLS:
                  continue
              text = _tool_result_text(block.get("content"))
              if not text:
                  continue
              try:
                  parsed = json.loads(text)
              except (json.JSONDecodeError, TypeError):
                  continue
              if isinstance(parsed, dict) and parsed.get("embed_url"):
                  previews.append(parsed)
  ```
- Trong `_run_claude`: khởi tạo `pending_tool_names: dict[str, str] = {}` và
  `previews: list[dict[str, Any]] = []`, gọi
  `_collect_preview(event, pending_tool_names, previews)` ngay sau
  `_log_event(event)` trong vòng lặp `for line in proc.stdout:`. Đổi mọi
  `return` trong hàm này thành bộ 3 `(answer, previews, error)` thay vì
  `(answer, error)` — cả nhánh timeout (`return None, [], "..."`), nhánh lỗi
  exit code, và nhánh thành công (`return answer, previews, None`).
- `_query_claude`: cập nhật unpack/return theo 3-tuple ở mọi điểm gọi
  `_run_claude` (bao gồm nhánh retry khi resume fail).
- `do_POST`: cập nhật
  ```python
  answer, previews, error = _query_claude(question, session_id, context, row_limit)
  if error:
      self._reply(502, {"message": error})
      return
  self._reply(200, {"answer": answer or "", "previews": previews})
  ```
  Luôn có key `"previews"` (rỗng nếu lượt đó không gọi write tool) để
  frontend xử lý nhất quán.
- **Không đổi** `ALLOWED_TOOLS`/`DISALLOWED_TOOLS`, `_build_argv`,
  `_lock_for_session`/`_session_locks`, `_known_sessions` — đây chỉ là parse
  thêm dữ liệu từ stdout đã đọc sẵn, không đụng tới sandbox hay khoá session.

### 5. `superset/superset_config.py` — không cần sửa
View `/api/v1/vdt-ai-chat/query` forward nguyên bytes response của gateway
(`Response(response.read(), status=response.status, content_type="application/json")`),
nên key `previews` mới tự động đi qua. Chỉ cần verify lại ở bước test.

### 6. `superset/static/vdt-ai-chat.js`
Trong hàm `add(role, content, data)`, sau khối render bảng
`data?.columns?.length && data?.rows?.length` hiện có, thêm khối render
preview — dùng `createElement`/gán property (không nối chuỗi vào `innerHTML`)
dù URL do server dựng từ id số, không phải input người dùng:
```js
if (Array.isArray(data?.previews) && data.previews.length) {
  data.previews.forEach(preview => {
    if (typeof preview?.embed_url !== 'string' || typeof preview?.url !== 'string') return;
    const wrap = document.createElement('div');
    wrap.className = `vdt-ai-preview vdt-ai-preview-${preview.type === 'dashboard' ? 'dashboard' : 'chart'}`;
    const label = document.createElement('div');
    label.className = 'vdt-ai-preview-label';
    label.textContent = preview.type === 'dashboard' ? 'Dashboard preview' : 'Chart preview';
    const iframe = document.createElement('iframe');
    iframe.loading = 'lazy';
    iframe.src = preview.embed_url;
    const link = document.createElement('a');
    link.href = preview.url; link.target = '_blank'; link.rel = 'noopener';
    link.className = 'vdt-ai-preview-link'; link.textContent = 'Mở trong Superset ↗';
    wrap.append(label, iframe, link);
    message.append(wrap);
  });
}
```
Không cần đổi gì ở phần replay-on-load (`messages.forEach(m => add(m.role,
m.content, m.data))`) hay `save()`/`load()` — `previews` chỉ là 1 key nữa
trong `data`, tự round-trip qua `localStorage` JSON. Submit handler đã truyền
nguyên object `result` làm `data` (`add('agent', result.answer, result)`) nên
không cần đổi call site.

### 7. `superset/static/vdt-ai-chat.css`
Thêm style cho khối preview, theo đúng bảng màu đang dùng trong file
(`#334155` border, `#172033`/`#0f172a` nền, `#b8c4d8` text phụ):
```css
.vdt-ai-preview { margin-top: 10px; border: 1px solid #334155; border-radius: 8px; overflow: hidden; }
.vdt-ai-preview-label { padding: 6px 10px; font-size: 11px; font-weight: 700; color: #b8c4d8; background: #172033; }
.vdt-ai-preview iframe { display: block; width: 100%; border: 0; background: #0f172a; }
.vdt-ai-preview-chart iframe { height: 260px; }
.vdt-ai-preview-dashboard iframe { height: 380px; }
.vdt-ai-preview-link { display: block; padding: 6px 10px; font-size: 12px; color: #7db8ff; background: #0f172a; text-decoration: none; }
.vdt-ai-preview-link:hover { text-decoration: underline; }
```

### 8. Tài liệu
- `ROLLOUT.md` §5 (Notes): thêm 1 bullet mô tả `SUPERSET_PUBLIC_URL` (mục
  đích, mặc định `http://localhost:8088`, ai deploy ngoài localhost phải tự
  override), và 1 dòng ghi chú panel giờ hiện live preview thay vì chỉ link.
- `task.md` Phase 5: thêm 1 dòng checklist mới cho tính năng preview, tick
  sau khi verify xong end-to-end.
- `role_prompt.md`: không cần đổi — hướng dẫn hiện tại (luôn kèm URL trong
  câu trả lời text) vẫn đúng, preview là cơ chế tự động riêng, không phụ
  thuộc vào nội dung Claude viết.

## Verification — kiểm tra từng lớp trước khi rebuild toàn bộ

1. **Bước 0** (browser, thủ công) phải xong trước, quyết định giá trị
   `standalone=` dùng ở Bước 3.
2. Sau khi sửa `docker-compose.yml` + `mcp_servers.json.template` +
   `mcp_server.py`: `docker compose up -d --build claude_gateway`, rồi test
   trực tiếp qua Python trong container (theo đúng cách đã dùng trong session
   này để test `create_dataset`/`create_chart`/`update_chart`):
   ```
   docker compose exec -T claude_gateway python3 -c "
   import mcp_server as m
   print(m.create_chart(dataset_id=1, chart_name='preview-test', viz_type='table', metrics=['COUNT(*)']))
   "
   ```
   Xác nhận dict trả về có `type: "chart"`, cả `url` và `embed_url` bắt đầu
   bằng `http://localhost:8088` (không phải `http://superset:8088`), và
   `embed_url` có đúng tham số `standalone=` đã verify. Test tương tự cho
   `update_chart` và `create_dashboard`.
3. Test logic correlate `tool_use`↔`tool_result` của gateway độc lập, không
   cần gọi `claude` CLI thật:
   ```
   docker compose exec -T claude_gateway python3 -c "
   import json, gateway_server as g
   pending, previews = {}, []
   events = [
     {'type':'assistant','message':{'content':[{'type':'tool_use','id':'t1','name':'mcp__superset-postgres__create_chart','input':{}}]}},
     {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'t1','content':[{'type':'text','text': json.dumps({'chart_id':1,'type':'chart','url':'http://localhost:8088/explore/?slice_id=1','embed_url':'http://localhost:8088/explore/?slice_id=1&standalone=X'})}]}]}},
     {'type':'assistant','message':{'content':[{'type':'tool_use','id':'t2','name':'mcp__superset-postgres__run_sql_readonly','input':{}}]}},
     {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'t2','content':[{'type':'text','text':'not json'}]}]}},
   ]
   for e in events: g._collect_preview(e, pending, previews)
   print(previews)
   "
   ```
   Kỳ vọng: chỉ đúng 1 phần tử trong `previews` (từ `create_chart`), kết quả
   của `run_sql_readonly` không làm crash và không bị thêm vào.
4. Rebuild nốt phần frontend: `docker compose up -d --build claude_gateway superset`
   (cả 2 image đều COPY tĩnh code vào lúc build, không mount volume — đã xác
   nhận từ các lần rebuild trước trong session này).
5. Test thật trên trình duyệt: đăng nhập Superset, mở panel AI Agent ở trang
   `/dashboard*` hoặc `/sqllab*`, yêu cầu tạo 1 chart. Xác nhận:
   - Câu trả lời text vẫn hiện như cũ.
   - Xuất hiện khối preview có viền, iframe render đúng chart thật (không
     trắng/lỗi — mở DevTools Network, xác nhận request document của iframe
     trả 200, không bị chặn bởi `X-Frame-Options`/CSP, không redirect ra
     trang login).
   - Link "Mở trong Superset ↗" mở tab mới tới `http://localhost:8088/...`
     và load được (xác nhận `SUPERSET_PUBLIC_URL` hoạt động đúng).
   - Reload trang, xác nhận preview render lại đúng từ lịch sử đã lưu
     `localStorage`.
6. Lặp lại bước 5 với yêu cầu tạo dashboard (gộp nhiều chart), xác nhận
   preview dashboard cũng hiện đúng, và nếu agent tạo nhiều chart rồi mới gộp
   dashboard trong cùng 1 lượt, tất cả các preview chart + preview dashboard
   cuối đều hiện theo đúng thứ tự tạo trong cùng bong bóng trả lời.

### File quan trọng sẽ sửa
- `mcp_server.py`
- `claude_gateway/gateway_server.py`
- `claude_gateway/mcp_servers.json.template`
- `docker-compose.yml`
- `superset/static/vdt-ai-chat.js`
- `superset/static/vdt-ai-chat.css`
- `ROLLOUT.md`, `task.md` (tài liệu, nhỏ)

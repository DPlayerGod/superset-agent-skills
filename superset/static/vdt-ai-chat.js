(() => {
  const allowedRoute = () => /^\/dashboard\b|^\/sqllab\b/i.test(location.pathname);
  const storeKey = 'vdt-ai-chat-threads-v1';
  const legacyKey = 'vdt-ai-chat-v1';
  const maxThreads = 20;
  const maxMessages = 60;
  const variants = ['baseline', 'skills'];
  // Starter questions for an empty thread. Each one leans on a different column of
  // fact_employee_allocation so the answers show off a different tool path.
  const suggestions = [
    'Tổng FTE theo từng dự án trong quý này',
    'Top 10 nhân sự có FTE cao nhất tháng gần nhất',
    'Vẽ biểu đồ FTE theo tháng của từng đơn vị',
    'Nhân sự nào đang tham gia nhiều dự án nhất?',
  ];
  const escapeHtml = text => String(text).replace(/[&<>]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[char]));
  const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

  const blankThread = variant => ({
    id: newId(),
    // Fixed for the thread's whole life, unlike the old per-page-load id: this is
    // what the gateway passes to `claude --resume`, so a reload now continues the
    // same Claude conversation instead of silently starting a new one.
    session_id: newId(),
    title: 'Đoạn chat mới',
    variant: variants.includes(variant) ? variant : 'baseline',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    messages: [],
  });

  function loadStore() {
    let store;
    try {
      store = JSON.parse(localStorage.getItem(storeKey));
    } catch { store = null; }
    if (!store || !Array.isArray(store.threads)) store = { active_thread_id: null, threads: [] };

    // One-time migration: the old flat message list becomes a single thread. Its
    // Claude session is unrecoverable (it was never persisted), so it starts fresh.
    try {
      const legacy = JSON.parse(localStorage.getItem(legacyKey));
      if (Array.isArray(legacy) && legacy.length) {
        const thread = blankThread('baseline');
        thread.messages = legacy.slice(-maxMessages);
        thread.title = (legacy.find(m => m.role === 'user') || {}).content || 'Lịch sử cũ';
        store.threads.unshift(thread);
        store.active_thread_id = thread.id;
      }
      if (legacy !== null) localStorage.removeItem(legacyKey);
    } catch { /* nothing usable to migrate */ }

    if (!store.threads.length) {
      const thread = blankThread();
      store.threads.push(thread);
      store.active_thread_id = thread.id;
    }
    if (!store.threads.some(t => t.id === store.active_thread_id)) store.active_thread_id = store.threads[0].id;
    return store;
  }

  function saveStore(store) {
    store.threads.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
    store.threads = store.threads.slice(0, maxThreads);
    store.threads.forEach(thread => { thread.messages = thread.messages.slice(-maxMessages); });
    try {
      localStorage.setItem(storeKey, JSON.stringify(store));
    } catch {
      // Quota exceeded: drop the oldest threads and keep the active one rather than
      // losing every write from here on.
      store.threads = store.threads.slice(0, 3);
      try { localStorage.setItem(storeKey, JSON.stringify(store)); } catch { /* give up silently */ }
    }
  }

  const relativeTime = iso => {
    const minutes = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (!Number.isFinite(minutes)) return '';
    if (minutes < 1) return 'vừa xong';
    if (minutes < 60) return `${minutes} phút trước`;
    if (minutes < 1440) return `${Math.round(minutes / 60)} giờ trước`;
    return `${Math.round(minutes / 1440)} ngày trước`;
  };

  function start() {
    const existing = document.getElementById('vdt-ai-chat-root');
    if (existing) {
      existing.style.display = allowedRoute() ? '' : 'none';
      return;
    }
    if (!allowedRoute()) return;

    const root = document.createElement('section');
    root.id = 'vdt-ai-chat-root';
    root.innerHTML = `<div class="vdt-ai-panel" aria-label="AI Agent"><div class="vdt-ai-header"><span class="vdt-ai-title">AI Data Agent</span><span class="vdt-ai-actions"><button class="vdt-ai-new" title="Đoạn chat mới">+</button><button class="vdt-ai-threads" title="Lịch sử đoạn chat">☰</button><button class="vdt-ai-max" title="Phóng to / thu nhỏ">⤢</button><button class="vdt-ai-close" title="Đóng">×</button></span></div><div class="vdt-ai-menu vdt-ai-thread-menu" hidden></div><div class="vdt-ai-menu vdt-ai-variant-menu" hidden></div><div class="vdt-ai-history"></div><form class="vdt-ai-input"><textarea maxlength="2000" placeholder="Hỏi về dữ liệu FTE, bảng hoặc SQL… (Enter để gửi)"></textarea><button type="submit" class="vdt-ai-send" title="Gửi (Enter)" aria-label="Gửi"><svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M2.01 21 23 12 2.01 3 2 10l15 2-15 2z"/></svg></button></form></div><button class="vdt-ai-button" aria-label="Mở AI Agent">AI Agent</button>`;
    document.body.append(root);

    const panel = root.querySelector('.vdt-ai-panel');
    const toggle = root.querySelector('.vdt-ai-button');
    const history = root.querySelector('.vdt-ai-history');
    const form = root.querySelector('form');
    const input = root.querySelector('textarea');
    const threadMenu = root.querySelector('.vdt-ai-thread-menu');
    const variantMenu = root.querySelector('.vdt-ai-variant-menu');
    const titleEl = root.querySelector('.vdt-ai-title');

    let store = loadStore();
    const activeThread = () => store.threads.find(t => t.id === store.active_thread_id) || store.threads[0];

    const badge = (text, className) => {
      const span = document.createElement('span');
      span.className = `vdt-ai-badge ${className || ''}`.trim();
      span.textContent = text;
      return span;
    };

    // Both URLs are built by the gateway out of SUPERSET_PUBLIC_URL and a numeric
    // id, but they still end up in `src`/`href`, so they are checked rather than
    // trusted: a scheme like `javascript:` reaching either would be executable.
    const isHttpUrl = value => typeof value === 'string' && /^https?:\/\//i.test(value);

    function renderPreviews(message, previews) {
      previews.forEach(preview => {
        if (!isHttpUrl(preview?.embed_url) || !isHttpUrl(preview?.url)) return;
        const isDashboard = preview.type === 'dashboard';
        const wrap = document.createElement('div');
        wrap.className = `vdt-ai-preview vdt-ai-preview-${isDashboard ? 'dashboard' : 'chart'}`;

        const label = document.createElement('div');
        label.className = 'vdt-ai-preview-label';
        label.textContent = isDashboard ? 'Dashboard' : 'Chart';

        const iframe = document.createElement('iframe');
        iframe.loading = 'lazy';
        iframe.src = preview.embed_url;

        const tabs = document.createElement('div');
        tabs.className = 'vdt-ai-preview-tabs';
        [['preview', 'Preview'], ['explore', 'Explore']].forEach(([mode, text]) => {
          const tab = document.createElement('button');
          tab.type = 'button';
          tab.textContent = text;
          if (mode === 'preview') tab.classList.add('active');
          tab.onclick = () => {
            iframe.src = mode === 'preview' ? preview.embed_url : preview.url;
            // The full Explore/dashboard page carries Superset's nav and control
            // panel; it is unusable at the compact panel width, so open up.
            if (mode === 'explore') panel.classList.add('vdt-ai-panel--max');
            tabs.querySelectorAll('button').forEach(other => other.classList.remove('active'));
            tab.classList.add('active');
          };
          tabs.append(tab);
        });
        label.append(tabs);

        const link = document.createElement('a');
        link.href = preview.url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.className = 'vdt-ai-preview-link';
        link.textContent = 'Mở trong Superset ↗';

        wrap.append(label, iframe, link);
        message.append(wrap);
      });
    }

    // Shared by the finished message and by each streamed chunk, so a half-written
    // answer is marked up the same way the final one will be. escapeHtml runs first
    // and on the whole string, which is what keeps this safe for model-written text.
    const formatAgent = text => escapeHtml(text)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br>');

    // Built with createElement/textContent rather than innerHTML: the values come
    // back from the gateway, and one of them (the answer) is model-written text.
    function renderMessage(role, content, data) {
      const message = document.createElement('article');
      message.className = `vdt-ai-message vdt-ai-${role}`;
      message.innerHTML = role === 'agent' ? formatAgent(content) : escapeHtml(content);

      if (data?.sql) {
        const sql = document.createElement('pre');
        sql.textContent = data.sql;
        message.append(sql);
        const copy = document.createElement('button');
        copy.className = 'vdt-ai-copy';
        copy.textContent = 'Sao chép SQL';
        copy.onclick = () => navigator.clipboard.writeText(data.sql);
        message.append(copy);
      }

      if (data?.columns?.length && data?.rows?.length) {
        const table = document.createElement('table');
        table.className = 'vdt-ai-table';
        table.innerHTML = `<thead><tr>${data.columns.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead><tbody>${data.rows.map(row => `<tr>${data.columns.map(c => `<td>${escapeHtml(row[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody>`;
        message.append(table);
      }

      if (Array.isArray(data?.previews) && data.previews.length) renderPreviews(message, data.previews);

      if (role === 'agent' && (data?.timing || data?.variant)) {
        const meta = document.createElement('div');
        meta.className = 'vdt-ai-meta';
        if (data.variant) meta.append(badge(data.variant, `vdt-ai-badge-${data.variant}`));
        if (Number.isFinite(data.timing?.total_ms)) meta.append(badge(`⏱ ${(data.timing.total_ms / 1000).toFixed(1)}s`));
        if (Number.isFinite(data.timing?.tool_calls) && data.timing.tool_calls > 0) {
          meta.append(badge(`${data.timing.tool_calls} tool`));
        }
        message.append(meta);
      }
      return message;
    }

    const add = (role, content, data) => {
      history.append(renderMessage(role, content, data));
      history.scrollTop = history.scrollHeight;
    };

    // Only drawn on an empty thread: these are a way in, not a permanent toolbar,
    // so they are dropped the moment the conversation has a first question.
    function renderSuggestions() {
      const wrap = document.createElement('div');
      wrap.className = 'vdt-ai-suggest';
      const heading = document.createElement('div');
      heading.className = 'vdt-ai-suggest-heading';
      heading.textContent = 'Gợi ý câu hỏi';
      wrap.append(heading);
      suggestions.forEach(text => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'vdt-ai-chip';
        chip.textContent = text;
        // requestSubmit (not submit) so the form's onsubmit handler still runs.
        chip.onclick = () => { input.value = text; form.requestSubmit(); };
        wrap.append(chip);
      });
      history.append(wrap);
    }

    function renderThread() {
      const thread = activeThread();
      history.replaceChildren();
      thread.messages.forEach(message => add(message.role, message.content, message.data));
      if (!thread.messages.length) renderSuggestions();
      titleEl.textContent = thread.variant === 'skills' ? 'AI Data Agent · skills' : 'AI Data Agent';
      history.scrollTop = history.scrollHeight;
    }

    const closeMenus = () => { threadMenu.hidden = true; variantMenu.hidden = true; };

    function openThreadMenu() {
      variantMenu.hidden = true;
      threadMenu.replaceChildren();
      store.threads.forEach(thread => {
        const item = document.createElement('div');
        item.className = `vdt-ai-thread-item${thread.id === store.active_thread_id ? ' active' : ''}`;
        const title = document.createElement('div');
        title.className = 'vdt-ai-thread-title';
        title.textContent = thread.title;
        const sub = document.createElement('div');
        sub.className = 'vdt-ai-thread-sub';
        sub.textContent = `${relativeTime(thread.updated_at)} · ${thread.messages.length} tin nhắn`;
        sub.append(badge(thread.variant, `vdt-ai-badge-${thread.variant}`));
        item.append(title, sub);
        item.onclick = () => {
          store.active_thread_id = thread.id;
          saveStore(store);
          renderThread();
          closeMenus();
        };
        threadMenu.append(item);
      });
      threadMenu.hidden = false;
    }

    function openVariantMenu() {
      threadMenu.hidden = true;
      variantMenu.replaceChildren();
      const heading = document.createElement('div');
      heading.className = 'vdt-ai-menu-heading';
      heading.textContent = 'Đoạn chat mới với:';
      variantMenu.append(heading);
      [['baseline', 'Baseline', 'Cấu hình hiện tại, không nạp Agent Skills'],
       ['skills', 'Agent Skills', 'Baseline + plugin skills của dự án']].forEach(([variant, label, note]) => {
        const item = document.createElement('div');
        item.className = 'vdt-ai-thread-item';
        const title = document.createElement('div');
        title.className = 'vdt-ai-thread-title';
        title.textContent = label;
        const sub = document.createElement('div');
        sub.className = 'vdt-ai-thread-sub';
        sub.textContent = note;
        item.append(title, sub);
        item.onclick = () => {
          const thread = blankThread(variant);
          store.threads.unshift(thread);
          store.active_thread_id = thread.id;
          saveStore(store);
          renderThread();
          closeMenus();
          input.focus();
        };
        variantMenu.append(item);
      });
      variantMenu.hidden = false;
    }

    // Server-Sent Events, read by hand rather than with EventSource: EventSource is
    // GET-only and cannot carry the question body. `onEvent` is called for every
    // progress event; the promise resolves with the final `done` event, whose shape
    // is the same {answer, previews, timing, variant} the buffered endpoint returns.
    const ask = async (question, thread_session_id, variant, onEvent) => {
      const response = await fetch('/api/v1/vdt-ai-chat/stream', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          session_id: thread_session_id,
          variant,
          context: { path: location.pathname },
          row_limit: 200,
        }),
      });
      // A rejected request (401/400) never becomes a stream, so it still answers
      // in JSON and is read as such.
      if (!response.ok || !response.body) {
        const failure = await response.json().catch(() => ({}));
        throw new Error(failure.message || 'Không thể xử lý yêu cầu');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let finished = null;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // An event ends at a blank line; anything after the last one is a partial
        // frame and stays in the buffer until the rest of it arrives.
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const line = frame.split('\n').find(part => part.startsWith('data:'));
          if (!line) continue;
          let event;
          try { event = JSON.parse(line.slice(5)); } catch { continue; }
          if (event.type === 'error') throw new Error(event.message || 'Không thể xử lý yêu cầu');
          if (event.type === 'done') finished = event; else onEvent(event);
        }
      }
      if (!finished) throw new Error('Kết nối bị ngắt trước khi có câu trả lời');
      return finished;
    };

    const pending = text => {
      const status = document.createElement('article');
      status.className = 'vdt-ai-message vdt-ai-agent vdt-ai-muted';
      status.textContent = text;
      history.append(status);
      history.scrollTop = history.scrollHeight;
      return status;
    };

    root.querySelector('.vdt-ai-new').onclick = () => (variantMenu.hidden ? openVariantMenu() : closeMenus());
    root.querySelector('.vdt-ai-threads').onclick = () => (threadMenu.hidden ? openThreadMenu() : closeMenus());
    root.querySelector('.vdt-ai-max').onclick = () => panel.classList.toggle('vdt-ai-panel--max');
    root.querySelector('.vdt-ai-close').onclick = () => { panel.classList.remove('open'); closeMenus(); };
    toggle.onclick = () => {
      panel.classList.toggle('open');
      closeMenus();
      if (panel.classList.contains('open')) input.focus();
    };

    form.onsubmit = async event => {
      event.preventDefault();
      const question = input.value.trim();
      if (!question) return;
      const thread = activeThread();
      input.value = '';
      closeMenus();
      history.querySelector('.vdt-ai-suggest')?.remove();
      add('user', question);
      thread.messages.push({ role: 'user', content: question });
      if (!thread.messages.some(m => m.role === 'agent')) thread.title = question.slice(0, 50);
      thread.updated_at = new Date().toISOString();
      saveStore(store);

      // One element carries the whole turn: first the progress line, then the answer
      // as it streams in, and it is replaced by the fully rendered message (previews,
      // timing badges) once `done` arrives.
      const status = pending('Đang phân tích dữ liệu…');
      let streamed = '';
      const waiting = text => {
        streamed = '';
        status.classList.add('vdt-ai-muted');
        status.textContent = text;
        history.scrollTop = history.scrollHeight;
      };
      const onEvent = event => {
        // A reply that arrives after the user switched threads is still stored on
        // its own thread below, but must not redraw over the conversation on screen.
        if (activeThread().id !== thread.id) return;
        if (event.type === 'tool') {
          waiting(`Đang chạy ${event.name}…`);
        } else if (event.type === 'reset') {
          // A new assistant message began. Anything streamed before it was narration
          // leading up to a tool call, not the answer - drop it, the way the buffered
          // endpoint does by returning only the final message.
          if (streamed) waiting('Đang soạn câu trả lời…');
        } else if (event.type === 'delta') {
          streamed += event.text;
          status.classList.remove('vdt-ai-muted');
          status.innerHTML = formatAgent(streamed);
          history.scrollTop = history.scrollHeight;
        }
      };

      try {
        const result = await ask(question, thread.session_id, thread.variant, onEvent);
        status.remove();
        thread.messages.push({ role: 'agent', content: result.answer, data: result });
        thread.updated_at = new Date().toISOString();
        saveStore(store);
        // The answer is always stored on the thread that asked, but only drawn if
        // that thread is still the one on screen - switching threads mid-request
        // must not drop another conversation's reply into this one.
        if (activeThread().id === thread.id) add('agent', result.answer, result);
      } catch (error) {
        // The element may be mid-answer by now, so this restores the status look
        // rather than leaving an error line styled as a finished reply.
        if (activeThread().id === thread.id) waiting(`Lỗi: ${error.message}`);
      }
    };

    // Enter sends, Shift+Enter keeps the newline the textarea is there for.
    // `isComposing` is what makes this safe for Vietnamese: with a telex/VNI IME
    // the Enter keystroke can be the one committing a syllable, and sending on it
    // would cut the word in half.
    input.onkeydown = event => {
      if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
      event.preventDefault();
      form.requestSubmit();
    };

    renderThread();
  }

  start();
  new MutationObserver(start).observe(document.documentElement, { childList: true, subtree: true });
})();

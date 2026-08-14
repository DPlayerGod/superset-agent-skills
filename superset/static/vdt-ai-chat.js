(() => {
  const allowedRoute = () => /^\/dashboard\b|^\/sqllab\b/i.test(location.pathname);
  const storeKey = 'vdt-ai-chat-threads-v1';
  const legacyKey = 'vdt-ai-chat-v1';
  const maxThreads = 20;
  const maxMessages = 60;
  const variants = ['baseline', 'skills'];
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
      const thread = blankThread('baseline');
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
    root.innerHTML = `<div class="vdt-ai-panel" aria-label="AI Agent"><div class="vdt-ai-header"><span class="vdt-ai-title">AI Data Agent</span><span class="vdt-ai-actions"><button class="vdt-ai-new" title="Đoạn chat mới">+</button><button class="vdt-ai-threads" title="Lịch sử đoạn chat">☰</button><button class="vdt-ai-max" title="Phóng to / thu nhỏ">⤢</button><button class="vdt-ai-close" title="Đóng">×</button></span></div><div class="vdt-ai-menu vdt-ai-thread-menu" hidden></div><div class="vdt-ai-menu vdt-ai-variant-menu" hidden></div><div class="vdt-ai-history"></div><form class="vdt-ai-input"><textarea maxlength="2000" placeholder="Hỏi về dữ liệu FTE, bảng hoặc SQL…"></textarea><span class="vdt-ai-buttons"><button type="submit">Gửi</button><button type="button" class="vdt-ai-compare" title="Chạy song song baseline và skills để so sánh">So sánh</button></span></form></div><button class="vdt-ai-button" aria-label="Mở AI Agent">AI Agent</button>`;
    document.body.append(root);

    const panel = root.querySelector('.vdt-ai-panel');
    const toggle = root.querySelector('.vdt-ai-button');
    const history = root.querySelector('.vdt-ai-history');
    const form = root.querySelector('form');
    const input = root.querySelector('textarea');
    const threadMenu = root.querySelector('.vdt-ai-thread-menu');
    const variantMenu = root.querySelector('.vdt-ai-variant-menu');
    const titleEl = root.querySelector('.vdt-ai-title');
    const compareBtn = root.querySelector('.vdt-ai-compare');

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

    // Built with createElement/textContent rather than innerHTML: the values come
    // back from the gateway, and one of them (the answer) is model-written text.
    function renderMessage(role, content, data) {
      const message = document.createElement('article');
      message.className = `vdt-ai-message vdt-ai-${role}`;
      message.innerHTML = role === 'agent'
        ? escapeHtml(content).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\n/g, '<br>')
        : escapeHtml(content);

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

    function renderThread() {
      const thread = activeThread();
      history.replaceChildren();
      thread.messages.forEach(message => add(message.role, message.content, message.data));
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

    const ask = (question, thread_session_id, variant) => fetch('/api/v1/vdt-ai-chat/query', {
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
    }).then(async response => {
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || 'Không thể xử lý yêu cầu');
      return result;
    });

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
      add('user', question);
      thread.messages.push({ role: 'user', content: question });
      if (!thread.messages.some(m => m.role === 'agent')) thread.title = question.slice(0, 50);
      thread.updated_at = new Date().toISOString();
      saveStore(store);

      const status = pending('Đang phân tích dữ liệu…');
      try {
        const result = await ask(question, thread.session_id, thread.variant);
        status.remove();
        thread.messages.push({ role: 'agent', content: result.answer, data: result });
        thread.updated_at = new Date().toISOString();
        saveStore(store);
        // The answer is always stored on the thread that asked, but only drawn if
        // that thread is still the one on screen - switching threads mid-request
        // must not drop another conversation's reply into this one.
        if (activeThread().id === thread.id) add('agent', result.answer, result);
      } catch (error) {
        if (activeThread().id === thread.id) status.textContent = `Lỗi: ${error.message}`;
      }
    };

    // Compare runs both variants on one question, each under its own throwaway
    // session id: sharing the thread's id would make the gateway resume one Claude
    // transcript from two directions at once. The pair is shown, not stored - the
    // thread history stays a single conversation.
    compareBtn.onclick = async () => {
      const question = input.value.trim();
      if (!question) return;
      input.value = '';
      closeMenus();
      panel.classList.add('vdt-ai-panel--max');
      add('user', question);

      const startedIn = activeThread().id;
      const status = pending('Đang chạy song song baseline và skills…');
      const settled = await Promise.all(variants.map(variant =>
        ask(question, newId(), variant).then(
          result => ({ variant, result }),
          error => ({ variant, error }),
        )));
      status.remove();
      if (activeThread().id !== startedIn) return;

      const pair = document.createElement('div');
      pair.className = 'vdt-ai-compare';
      settled.forEach(({ variant, result, error }) => {
        const column = document.createElement('div');
        column.className = 'vdt-ai-compare-col';
        const head = document.createElement('div');
        head.className = 'vdt-ai-compare-head';
        head.append(badge(variant, `vdt-ai-badge-${variant}`));
        column.append(head);
        column.append(error
          ? renderMessage('agent', `Lỗi: ${error.message}`, null)
          : renderMessage('agent', result.answer, result));
        pair.append(column);
      });
      history.append(pair);
      history.scrollTop = history.scrollHeight;
    };

    renderThread();
  }

  start();
  new MutationObserver(start).observe(document.documentElement, { childList: true, subtree: true });
})();

(() => {
  const allowedRoute = () => /^\/dashboard\b|^\/sqllab\b/i.test(location.pathname);
  const storeKey = 'vdt-ai-chat-v1';
  const sessionId = crypto.randomUUID();
  const escapeHtml = text => String(text).replace(/[&<>]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[char]));
  const load = () => { try { return JSON.parse(localStorage.getItem(storeKey)) || []; } catch { return []; } };
  const save = messages => localStorage.setItem(storeKey, JSON.stringify(messages.slice(-30)));

  function start() {
    const existing = document.getElementById('vdt-ai-chat-root');
    if (existing) {
      existing.style.display = allowedRoute() ? '' : 'none';
      return;
    }
    if (!allowedRoute()) return;
    const root = document.createElement('section');
    root.id = 'vdt-ai-chat-root';
    root.innerHTML = `<div class="vdt-ai-panel" aria-label="AI Agent"><div class="vdt-ai-header"><span>AI Data Agent</span><button title="Đóng">×</button></div><div class="vdt-ai-history"></div><form class="vdt-ai-input"><textarea maxlength="2000" placeholder="Hỏi về dữ liệu FTE, bảng hoặc SQL…"></textarea><button type="submit">Gửi</button></form></div><button class="vdt-ai-button" aria-label="Mở AI Agent">AI Agent</button>`;
    document.body.append(root);
    const panel = root.querySelector('.vdt-ai-panel'), toggle = root.querySelector('.vdt-ai-button'), close = root.querySelector('.vdt-ai-header button');
    const history = root.querySelector('.vdt-ai-history'), form = root.querySelector('form'), input = root.querySelector('textarea');
    const messages = load();
    const add = (role, content, data) => {
      const message = document.createElement('article');
      message.className = `vdt-ai-message vdt-ai-${role}`;
      message.innerHTML = role === 'agent' ? escapeHtml(content).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\n/g, '<br>') : escapeHtml(content);
      if (data?.sql) {
        const sql = document.createElement('pre'); sql.textContent = data.sql; message.append(sql);
        const copy = document.createElement('button'); copy.className = 'vdt-ai-copy'; copy.textContent = 'Sao chép SQL'; copy.onclick = () => navigator.clipboard.writeText(data.sql); message.append(copy);
      }
      if (data?.columns?.length && data?.rows?.length) {
        const table = document.createElement('table'); table.className = 'vdt-ai-table';
        table.innerHTML = `<thead><tr>${data.columns.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead><tbody>${data.rows.map(row => `<tr>${data.columns.map(c => `<td>${escapeHtml(row[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody>`;
        message.append(table);
      }
      history.append(message); history.scrollTop = history.scrollHeight;
    };
    messages.forEach(message => add(message.role, message.content, message.data));
    toggle.onclick = () => { panel.classList.toggle('open'); if (panel.classList.contains('open')) input.focus(); };
    close.onclick = () => panel.classList.remove('open');
    form.onsubmit = async event => {
      event.preventDefault(); const question = input.value.trim(); if (!question) return;
      input.value = ''; add('user', question); messages.push({ role: 'user', content: question }); save(messages);
      const status = document.createElement('article'); status.className = 'vdt-ai-message vdt-ai-agent vdt-ai-muted'; status.textContent = 'Đang phân tích dữ liệu…'; history.append(status);
      try {
        const response = await fetch('/api/v1/vdt-ai-chat/query', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, session_id: sessionId, context: { path: location.pathname }, row_limit: 200 }) });
        const result = await response.json(); if (!response.ok) throw new Error(result.message || 'Không thể xử lý yêu cầu');
        status.remove(); add('agent', result.answer, result); messages.push({ role: 'agent', content: result.answer, data: result }); save(messages);
      } catch (error) { status.textContent = `Lỗi: ${error.message}`; }
    };
  }
  start(); new MutationObserver(start).observe(document.documentElement, { childList: true, subtree: true });
})();

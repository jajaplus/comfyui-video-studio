const state = {
  token: localStorage.getItem('h3_token') || '',
  tasks: [],
  authRequired: false,
};

const $ = (selector) => document.querySelector(selector);
const statusLabels = {
  queued: '排队中', starting: '正在启动', running: '生成中',
  completed: '已完成', failed: '失败', cancelled: '已取消',
};

function authHeaders(extra = {}) {
  return state.token ? { ...extra, Authorization: `Bearer ${state.token}` } : extra;
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: authHeaders(options.headers || {}) });
  if (response.status === 401) {
    $('#tokenDialog').showModal();
    throw new Error('请填写正确的访问令牌');
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[c]);
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.remove('hidden');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.add('hidden'), 2800);
}

function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleString('zh-CN', { hour12: false });
}

function renderTasks() {
  const list = $('#taskList');
  const counts = { queued: 0, running: 0, completed: 0, failed: 0 };
  for (const task of state.tasks) {
    if (task.status === 'queued') counts.queued += 1;
    else if (['starting', 'running'].includes(task.status)) counts.running += 1;
    else if (task.status === 'completed') counts.completed += 1;
    else if (['failed', 'cancelled'].includes(task.status)) counts.failed += 1;
  }
  $('#queuedCount').textContent = counts.queued;
  $('#runningCount').textContent = counts.running;
  $('#completedCount').textContent = counts.completed;
  $('#failedCount').textContent = counts.failed;

  if (!state.tasks.length) {
    list.innerHTML = '<div class="empty">还没有任务</div>';
    return;
  }
  list.innerHTML = state.tasks.map((task) => {
    const status = statusLabels[task.status] || task.status;
    const queue = task.queue_position ? `<span>队列第 ${task.queue_position} 位</span>` : '';
    const error = task.error ? `<p class="task-error">${escapeHtml(task.error)}</p>` : '';
    const download = task.download_url
      ? `<a href="${task.download_url}" data-download="${task.id}">下载成片</a>` : '';
    const retry = ['failed', 'cancelled'].includes(task.status)
      ? `<button data-retry="${task.id}">重试</button>` : '';
    return `<article class="task">
      <div class="task-main">
        <div class="task-head">
          <h3 title="${escapeHtml(task.name)}">${escapeHtml(task.name)}</h3>
          <span class="badge ${escapeHtml(task.status)}">${escapeHtml(status)}</span>
        </div>
        <div class="task-meta">
          ${queue}<span>${task.duration} 秒 · ${escapeHtml(task.aspect_ratio)}</span>
          <span>${escapeHtml(task.source_video)}</span><span>${formatTime(task.created_at)}</span>
        </div>
        ${error}
        <div class="progress"><div style="width:${Math.max(0, Math.min(100, task.progress || 0))}%"></div></div>
      </div>
      <div class="task-actions">${download}${retry}<button class="delete" data-delete="${task.id}">删除</button></div>
    </article>`;
  }).join('');
}

async function loadTasks(silent = false) {
  try {
    const data = await api('/api/tasks');
    state.tasks = data.items || [];
    renderTasks();
  } catch (error) {
    if (!silent) toast(error.message);
  }
}

async function loadHealth() {
  const element = $('#health');
  try {
    const response = await fetch('/health');
    const data = await response.json();
    if (data.engine === 'ok') {
      element.className = 'health ok';
      element.querySelector('b').textContent = 'H3 服务正常';
    } else {
      element.className = 'health bad';
      element.querySelector('b').textContent = 'H3 服务未启动';
    }
  } catch (_) {
    element.className = 'health bad';
    element.querySelector('b').textContent = '服务连接失败';
  }
}

document.querySelectorAll('.tab').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((item) => item.classList.toggle('active', item === button));
    document.querySelectorAll('.tab-content').forEach((item) => item.classList.toggle('active', item.id.startsWith(button.dataset.tab)));
  });
});

$('#singleForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  button.textContent = '正在上传…';
  try {
    const body = new FormData(event.currentTarget);
    if (!body.get('seed')) body.delete('seed');
    await api('/api/tasks', { method: 'POST', body });
    event.currentTarget.reset();
    event.currentTarget.querySelector('[name=duration]').value = 5;
    event.currentTarget.querySelector('[name=source_start]').value = 0;
    toast('任务已加入队列');
    await loadTasks();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '加入生成队列';
  }
});

$('#excelForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  button.textContent = '正在导入…';
  const result = $('#importResult');
  try {
    const data = await api('/api/tasks/import', { method: 'POST', body: new FormData(event.currentTarget) });
    result.classList.remove('hidden');
    const details = data.errors.length
      ? `<br>${data.errors.map((item) => `第 ${item.row} 行：${escapeHtml(item.error)}`).join('<br>')}` : '';
    result.innerHTML = `成功创建 ${data.created} 个任务，${data.errors.length} 行未导入。${details}`;
    toast(`已导入 ${data.created} 个任务`);
    await loadTasks();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '导入并加入队列';
  }
});

$('#taskList').addEventListener('click', async (event) => {
  const deleteButton = event.target.closest('[data-delete]');
  const retryButton = event.target.closest('[data-retry]');
  const downloadLink = event.target.closest('[data-download]');
  if (deleteButton) {
    if (!confirm('确定删除这个任务吗？生成中的任务会同时请求取消。')) return;
    try {
      await api(`/api/tasks/${deleteButton.dataset.delete}`, { method: 'DELETE' });
      toast('任务已删除');
      await loadTasks();
    } catch (error) { toast(error.message); }
  }
  if (retryButton) {
    try {
      await api(`/api/tasks/${retryButton.dataset.retry}/retry`, { method: 'POST' });
      toast('任务已重新加入队列');
      await loadTasks();
    } catch (error) { toast(error.message); }
  }
  if (downloadLink && state.token) {
    event.preventDefault();
    try {
      const response = await fetch(downloadLink.href, { headers: authHeaders() });
      if (!response.ok) throw new Error('下载失败');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'result.mp4';
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) { toast(error.message); }
  }
});

$('#refreshButton').addEventListener('click', () => { loadTasks(); loadHealth(); });
$('#templateLink').addEventListener('click', async (event) => {
  if (!state.token) return;
  event.preventDefault();
  try {
    const response = await fetch('/api/template', { headers: authHeaders() });
    if (!response.ok) throw new Error('模板下载失败');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'h3_tasks_template.xlsx';
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (error) { toast(error.message); }
});
$('#tokenForm').addEventListener('submit', (event) => {
  event.preventDefault();
  state.token = $('#tokenInput').value.trim();
  localStorage.setItem('h3_token', state.token);
  $('#tokenDialog').close();
  loadTasks();
});

(async function init() {
  try {
    const config = await (await fetch('/api/config')).json();
    state.authRequired = config.auth_required;
    if (state.authRequired && !state.token) $('#tokenDialog').showModal();
  } catch (_) {}
  await Promise.all([loadTasks(true), loadHealth()]);
  setInterval(() => loadTasks(true), 3000);
  setInterval(loadHealth, 15000);
})();


const state = {
  tasks: [],
  selectedVideos: [],
  selectedImages: [],
};
const videoMetadataCache = new WeakMap();

const $ = (selector) => document.querySelector(selector);
const statusLabels = {
  queued: '排队中', starting: '正在启动', running: '生成中',
  completed: '已完成', failed: '失败', cancelled: '已取消',
};
const qualityLabels = {
  low: '低清', standard: '标清', high: '高清',
};
const qualityDimensions = {
  low: {
    '16:9': [672, 384], '9:16': [384, 672], '1:1': [512, 512],
    '4:3': [576, 448], '3:4': [448, 576], '21:9': [768, 320],
  },
  standard: {
    '16:9': [832, 480], '9:16': [480, 832], '1:1': [640, 640],
    '4:3': [736, 544], '3:4': [544, 736], '21:9': [960, 416],
  },
  high: {
    '16:9': [1344, 768], '9:16': [768, 1344], '1:1': [1024, 1024],
    '4:3': [1024, 768], '3:4': [768, 1024], '21:9': [1568, 672],
  },
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
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

function formatSeconds(value) {
  const number = Number(value || 0);
  return Number.isInteger(number) ? `${number}` : number.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}

function formatElapsedSeconds(value) {
  const total = Math.max(0, Math.floor(Number(value || 0)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours) return `${hours}小时${minutes}分${seconds}秒`;
  if (minutes) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}

function elapsedSeconds(startedAt, finishedAt = '') {
  const start = new Date(startedAt).getTime();
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  return Math.max(0, (end - start) / 1000);
}

function elapsedLabel(startedAt, finishedAt = '') {
  const prefix = finishedAt ? '总耗时' : '已耗时';
  return `${prefix} ${formatElapsedSeconds(elapsedSeconds(startedAt, finishedAt))}`;
}

function updateElapsedTimes() {
  document.querySelectorAll('[data-elapsed-start]').forEach((element) => {
    element.textContent = elapsedLabel(
      element.dataset.elapsedStart,
      element.dataset.elapsedFinish || '',
    );
  });
}

function formatMegabytes(value) {
  const number = Number(value || 0);
  return number >= 1024 ? `${(number / 1024).toFixed(1)} GB` : `${Math.round(number)} MB`;
}

function segmentCount(duration) {
  return Math.max(1, Math.ceil(Number(duration || 0) / 15));
}

function isVideo(file) {
  return file.type.startsWith('video/') || /\.(mp4|mov|mkv|webm|avi)$/i.test(file.name);
}

function readVideoMetadata(file) {
  if (videoMetadataCache.has(file)) return Promise.resolve(videoMetadataCache.get(file));
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    const url = URL.createObjectURL(file);
    video.preload = 'metadata';
    video.onloadedmetadata = () => {
      const metadata = {
        duration: Number(video.duration.toFixed(3)),
        width: Number(video.videoWidth || 0),
        height: Number(video.videoHeight || 0),
      };
      videoMetadataCache.set(file, metadata);
      URL.revokeObjectURL(url);
      resolve(metadata);
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(`无法读取视频时长：${file.name}`));
    };
    video.src = url;
  });
}

function readVideoDuration(file) {
  return readVideoMetadata(file).then((metadata) => metadata.duration);
}

function closestAspectRatio(width, height) {
  if (!width || !height) return '16:9';
  const ratio = width / height;
  const ratios = {
    '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1,
    '4:3': 4 / 3, '3:4': 3 / 4, '21:9': 21 / 9,
  };
  return Object.keys(ratios).reduce((best, name) => (
    Math.abs(ratio - ratios[name]) < Math.abs(ratio - ratios[best]) ? name : best
  ), '16:9');
}

function updateQualityResolution() {
  const form = $('#singleForm');
  const hint = $('#qualityResolution');
  if (!form || !hint) return;
  const quality = form.elements.quality.value || 'low';
  const selectedRatio = form.elements.aspect_ratio.value || 'auto';
  let ratios = [selectedRatio];
  if (selectedRatio === 'auto') {
    ratios = [...new Set(state.selectedVideos.map((file) => {
      const metadata = videoMetadataCache.get(file);
      return metadata ? closestAspectRatio(metadata.width, metadata.height) : '16:9';
    }))];
    if (!ratios.length) ratios = ['16:9'];
  }
  const sizes = ratios.map((ratio) => {
    const [width, height] = qualityDimensions[quality][ratio];
    return `${width}×${height}`;
  });
  const autoText = selectedRatio === 'auto' ? '（按每个上传视频的比例）' : '';
  hint.textContent = `预计输出尺寸：${sizes.join(' / ')} ${autoText}`.trim();
}

function clearMediaPreview(container) {
  for (const url of container._objectUrls || []) URL.revokeObjectURL(url);
  container._objectUrls = [];
  container.innerHTML = '';
  container.classList.add('hidden');
}

function fileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function syncFileInput(input, files) {
  const transfer = new DataTransfer();
  for (const file of files) transfer.items.add(file);
  input.files = transfer.files;
}

function renderMediaPreview(files, container, kind) {
  clearMediaPreview(container);
  if (!files.length) return;
  container.classList.remove('hidden');
  for (const file of files) {
    const card = document.createElement('article');
    card.className = 'media-card';
    const url = URL.createObjectURL(file);
    container._objectUrls.push(url);
    const media = document.createElement(isVideo(file) ? 'video' : 'img');
    media.src = url;
    if (isVideo(file)) {
      media.controls = true;
      media.preload = 'metadata';
      media.muted = true;
    } else {
      media.alt = file.name;
    }
    const info = document.createElement('div');
    info.className = 'media-info';
    const name = document.createElement('strong');
    name.textContent = file.name;
    const meta = document.createElement('span');
    meta.textContent = isVideo(file) ? '正在读取时长…' : `${(file.size / 1024 / 1024).toFixed(1)} MB`;
    info.append(name, meta);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'media-remove';
    remove.title = `删除 ${file.name}`;
    remove.setAttribute('aria-label', `删除 ${file.name}`);
    remove.textContent = '×';
    remove.addEventListener('click', () => {
      const stateKey = kind === 'video' ? 'selectedVideos' : 'selectedImages';
      const input = kind === 'video' ? $('#uploadVideos') : $('#referenceImages');
      state[stateKey] = state[stateKey].filter((item) => fileKey(item) !== fileKey(file));
      syncFileInput(input, state[stateKey]);
      renderMediaPreview(state[stateKey], container, kind);
      if (kind === 'video') updateQualityResolution();
    });
    card.append(media, info, remove);
    container.append(card);
    if (isVideo(file)) {
      readVideoMetadata(file).then((metadata) => {
        meta.textContent = `${formatSeconds(metadata.duration)} 秒 · 原视频 ${metadata.width}×${metadata.height}`;
        if (metadata.duration < 4) {
          meta.className = 'invalid';
          meta.textContent += ' · 视频不能短于 4 秒';
        } else if (metadata.duration > 15) {
          meta.textContent += ` · 将自动切成 ${segmentCount(metadata.duration)} 段生成后合并`;
        }
        updateQualityResolution();
      }).catch((error) => {
        meta.className = 'invalid';
        meta.textContent = error.message;
      });
    }
  }
}

function appendSelectedFiles(kind, input, newFiles, maxFiles, container) {
  const stateKey = kind === 'video' ? 'selectedVideos' : 'selectedImages';
  const merged = [...state[stateKey]];
  const known = new Set(merged.map(fileKey));
  for (const file of newFiles) {
    if (!known.has(fileKey(file)) && merged.length < maxFiles) {
      merged.push(file);
      known.add(fileKey(file));
    }
  }
  if (newFiles.length && merged.length >= maxFiles && new Set([...state[stateKey], ...newFiles].map(fileKey)).size > maxFiles) {
    toast(`${kind === 'video' ? '上传视频' : '参考图片'}最多选择 ${maxFiles} 个`);
  }
  state[stateKey] = merged;
  syncFileInput(input, merged);
  renderMediaPreview(merged, container, kind);
  if (kind === 'video') updateQualityResolution();
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
    const progressValue = Math.max(0, Math.min(100, Number(task.progress || 0)));
    const stage = task.stage || (task.status === 'queued' ? '等待队列' : status);
    const segments = Number(task.segment_count || 1) > 1
      ? ` · ${Number(task.segment_count)} 段` : '';
    const quality = qualityLabels[task.quality] || qualityLabels.low;
    const presetSize = task.aspect_ratio !== 'auto'
      ? qualityDimensions[task.quality || 'low']?.[task.aspect_ratio] : null;
    const resolution = task.output_width && task.output_height
      ? `${task.output_width}×${task.output_height}`
      : (presetSize ? `${presetSize[0]}×${presetSize[1]}` : '等待计算尺寸');
    const elapsed = task.started_at
      ? `<span class="task-elapsed" data-elapsed-start="${escapeHtml(task.started_at)}" data-elapsed-finish="${escapeHtml(task.finished_at || '')}">${escapeHtml(elapsedLabel(task.started_at, task.finished_at || ''))}</span>`
      : '';
    return `<article class="task">
      <div class="task-main">
        <div class="task-head">
          <h3 title="${escapeHtml(task.name)}">${escapeHtml(task.name)}</h3>
          <span class="badge ${escapeHtml(task.status)}">${escapeHtml(status)}</span>
        </div>
        <div class="task-meta">
          ${queue}<span>${formatSeconds(task.duration)} 秒${segments} · ${escapeHtml(task.aspect_ratio)} · ${escapeHtml(quality)} ${escapeHtml(resolution)}</span>
          <span>${escapeHtml(task.source_video)}</span><span>${formatTime(task.created_at)}</span>${elapsed}
        </div>
        ${error}
        <div class="task-stage"><span>${escapeHtml(stage)}</span><strong>${progressValue}%</strong></div>
        <div class="progress"><div style="width:${progressValue}%"></div></div>
      </div>
      <div class="task-actions">${download}${retry}<button class="delete" data-delete="${task.id}">删除</button></div>
    </article>`;
  }).join('');
}

function setMeter(id, value) {
  $(id).style.width = `${Math.max(0, Math.min(100, Number(value || 0)))}%`;
}

function renderGpuStatus(payload) {
  const panel = $('#gpuPanel');
  const gpuStatus = payload.gpu || {};
  const gpu = (gpuStatus.gpus || [])[0];
  if (!gpuStatus.available || !gpu) {
    panel.classList.add('unavailable');
    $('#gpuName').textContent = '未检测到 NVIDIA GPU';
    $('#gpuState').textContent = '不可用';
    $('#gpuUtilization').textContent = '--';
    $('#gpuMemory').textContent = '--';
    $('#gpuTemperature').textContent = '--';
    $('#gpuPower').textContent = '--';
    setMeter('#gpuUtilizationBar', 0);
    setMeter('#gpuMemoryBar', 0);
    return;
  }
  panel.classList.remove('unavailable');
  const extra = gpuStatus.gpus.length > 1 ? ` · 共 ${gpuStatus.gpus.length} 张` : '';
  $('#gpuName').textContent = `${gpu.name}${extra}`;
  $('#gpuState').textContent = gpu.utilization > 0 ? '运行中' : '空闲';
  $('#gpuUtilization').textContent = `${gpu.utilization.toFixed(0)}%`;
  $('#gpuMemory').textContent = `${formatMegabytes(gpu.memory_used_mb)} / ${formatMegabytes(gpu.memory_total_mb)}`;
  $('#gpuTemperature').textContent = `${gpu.temperature_c.toFixed(0)} °C`;
  $('#gpuPower').textContent = `${gpu.power_draw_w.toFixed(0)} / ${gpu.power_limit_w.toFixed(0)} W`;
  setMeter('#gpuUtilizationBar', gpu.utilization);
  setMeter('#gpuMemoryBar', gpu.memory_percent);
}

async function loadSystemStatus() {
  try {
    renderGpuStatus(await api('/api/system/status'));
  } catch (_) {
    renderGpuStatus({ gpu: { available: false, gpus: [] } });
  }
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
      element.querySelector('b').textContent = 'ComfyUI 正常';
      element.title = `已连接 ${data.engine_url || 'ComfyUI'}`;
    } else {
      element.className = 'health bad';
      element.querySelector('b').textContent = 'ComfyUI 未连接（6008）';
      element.title = `客户端无法连接 ${data.engine_url || 'http://127.0.0.1:6008'}`;
    }
  } catch (_) {
    element.className = 'health bad';
    element.querySelector('b').textContent = '服务连接失败';
    element.title = '无法访问客户端健康检查接口';
  }
}

document.querySelectorAll('.tab').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((item) => item.classList.toggle('active', item === button));
    document.querySelectorAll('.tab-content').forEach((item) => item.classList.toggle('active', item.id.startsWith(button.dataset.tab)));
  });
});

$('#uploadVideos').addEventListener('change', (event) => {
  appendSelectedFiles('video', event.currentTarget, [...event.currentTarget.files], 3, $('#videoPreview'));
});
$('#referenceImages').addEventListener('change', (event) => {
  appendSelectedFiles('image', event.currentTarget, [...event.currentTarget.files], 9, $('#imagePreview'));
});
$('#singleForm').elements.aspect_ratio.addEventListener('change', updateQualityResolution);
$('#singleForm').elements.quality.addEventListener('change', updateQualityResolution);

$('#singleForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter || form.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = '正在上传…';
  try {
    const videos = state.selectedVideos;
    if (!videos.length) throw new Error('请至少上传一个视频');
    if (videos.length > 3) throw new Error('上传视频最多选择 3 个');
    if (state.selectedImages.length > 9) throw new Error('参考图片最多上传 9 张');
    const durations = await Promise.all(videos.map(readVideoDuration));
    const invalidIndex = durations.findIndex((duration) => duration < 4);
    if (invalidIndex >= 0) {
      throw new Error(`${videos[invalidIndex].name} 为 ${formatSeconds(durations[invalidIndex])} 秒；视频不能短于 4 秒`);
    }
    const body = new FormData(form);
    if (!body.get('seed')) body.delete('seed');
    body.append('video_durations', JSON.stringify(durations));
    const data = await api('/api/tasks', { method: 'POST', body });
    form.reset();
    state.selectedVideos = [];
    state.selectedImages = [];
    clearMediaPreview($('#videoPreview'));
    clearMediaPreview($('#imagePreview'));
    updateQualityResolution();
    toast(`已加入 ${data.created} 个复刻任务`);
    await loadTasks();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '批量加入复刻队列';
  }
});

$('#excelForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter || form.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = '正在导入…';
  const result = $('#importResult');
  try {
    const body = new FormData(form);
    const data = await api('/api/tasks/import', { method: 'POST', body });
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
});

$('#refreshButton').addEventListener('click', () => { loadTasks(); loadHealth(); });

(async function init() {
  updateQualityResolution();
  await Promise.all([loadTasks(true), loadHealth(), loadSystemStatus()]);
  setInterval(() => loadTasks(true), 3000);
  setInterval(updateElapsedTimes, 1000);
  setInterval(loadSystemStatus, 3000);
  setInterval(loadHealth, 15000);
})();

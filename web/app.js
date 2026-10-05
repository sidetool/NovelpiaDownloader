const $ = selector => document.querySelector(selector);
const jobForm = $('#job-form');
let state;
let initialized = false;
let pending = false;
let lastLog;
let lastQueue;
let noticeTimer;
let storedFiles = [];
let filesReady = false;
let fileRefreshVersion = 0;

function notice(message, error = false) {
  clearTimeout(noticeTimer);
  $('#notice').textContent = message;
  $('#notice').className = error ? 'error' : 'success';
  $('#notice').hidden = false;
  noticeTimer = setTimeout(() => { $('#notice').hidden = true; }, error ? 15000 : 6000);
}

function applySettings(settings, limits) {
  for (const [name, value] of Object.entries(settings)) {
    const input = jobForm.elements.namedItem(name);
    if (!input) continue;
    if (name === 'format') input.value = value;
    else if (input.type === 'checkbox') input.checked = value;
    else input.value = value;
  }
  for (const [name, bounds] of Object.entries(limits)) {
    const input = jobForm.elements.namedItem(name);
    input.min = bounds.min;
    input.max = bounds.max;
  }
  $('#bonus-mode').value = settings.bonusNever ? 'never' : settings.bonusAlways ? 'always' : 'range';
  updateDisabled();
}

function settings() {
  const data = {};
  for (const input of jobForm.elements) {
    if (!input.name || input.type === 'radio') continue;
    data[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value.trim();
  }
  data.format = jobForm.elements.namedItem('format').value;
  data.bonusNever = $('#bonus-mode').value === 'never';
  data.bonusAlways = $('#bonus-mode').value === 'always';
  return data;
}

function updateDisabled() {
  const busy = pending || Boolean(state?.action) || Boolean(state?.running || state?.queueRunning);
  for (const form of [jobForm, $('#login-form')]) {
    for (const input of form.elements) input.disabled = busy;
  }
  if (!busy) {
    jobForm.elements.namedItem('from').disabled = !$('#from-enabled').checked;
    jobForm.elements.namedItem('to').disabled = !$('#to-enabled').checked;
  }
  for (const id of ['queue-remove', 'queue-clear', 'queue-start']) {
    $(`#${id}`).disabled = busy || !state?.queue?.length;
  }
  $('#stop-button').disabled = pending || !state?.running || Boolean(state?.cancelRequested);
  $('#download-button').textContent = pending ? '처리 중…' : '다운로드';
  const fileBusy = !state || busy || !filesReady;
  $('#files-clear').disabled = fileBusy || !storedFiles.length;
  for (const button of document.querySelectorAll('[data-file-delete]')) button.disabled = fileBusy;
  $('#file-delete-hint').textContent = state?.running || state?.queueRunning
    ? '다운로드가 끝나면 파일을 삭제할 수 있습니다.'
    : '삭제한 파일은 복구할 수 없습니다. 다운로드 중에는 삭제할 수 없으며, 로그인 정보와 설정은 유지됩니다.';
}

function render(next) {
  state = next;
  $('#connection').textContent = '연결됨';
  $('#connection').className = 'connected';
  if (!initialized) {
    applySettings(state.settings, state.limits);
    $('#email').value = state.email;
    $('#account-section').open = !state.authenticated;
    initialized = true;
  }
  $('#account-status').textContent = state.authenticated
    ? state.authMode === 'key' ? 'LOGINKEY 적용됨' : state.authMode === 'saved' ? '저장된 로그인 정보' : '로그인됨'
    : '로그인 정보 입력';
  const progress = state.progress;
  $('#job-status').textContent = state.cancelRequested ? '중단 중' : state.running ? '다운로드 중' : state.action ? '요청 처리 중' : state.queueRunning ? '대기열 처리 중' : '대기 중';
  $('#progress').max = Math.max(progress.total, 1);
  if (state.running && progress.total === 0) $('#progress').removeAttribute('value');
  else $('#progress').value = progress.done + progress.failed;
  $('#progress-done').textContent = progress.done;
  $('#progress-fail').textContent = progress.failed;
  $('#progress-total').textContent = progress.total;
  if (state.log !== lastLog) {
    const log = $('#log');
    log.textContent = state.log || '작품을 선택하면 실행 로그가 여기에 표시됩니다.';
    if ($('#auto-scroll').checked) log.scrollTop = log.scrollHeight;
    lastLog = state.log;
  }
  const queueJson = JSON.stringify(state.queue);
  if (queueJson !== lastQueue) {
    const selected = new Set([...document.querySelectorAll('#queue input:checked')].map(input => Number(input.value)));
    $('#queue').replaceChildren();
    for (const job of state.queue) {
      const item = document.createElement('li');
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox'; checkbox.value = job.index; checkbox.checked = selected.has(job.index);
      const text = document.createElement('span'); text.textContent = job.label;
      label.append(checkbox, text); item.append(label); $('#queue').append(item);
    }
    $('#queue-count').textContent = state.queue.length;
    $('#queue-empty').hidden = state.queue.length > 0;
    lastQueue = queueJson;
  }
  updateDisabled();
}

async function poll() {
  try {
    const response = await fetch('/api/state', { cache: 'no-store' });
    if (!response.ok) throw new Error();
    render(await response.json());
  } catch {
    $('#connection').textContent = '다시 연결 중';
    $('#connection').className = '';
  } finally {
    setTimeout(poll, 1000);
  }
}

async function action(path, data = {}) {
  if (pending) return;
  pending = true; updateDisabled();
  try {
    const response = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'Novelpia-Web' },
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '요청을 처리하지 못했습니다.');
    if (result.message) notice(result.message);
    const current = await fetch('/api/state', { cache: 'no-store' });
    if (current.ok) render(await current.json());
    return result;
  } catch (error) {
    notice(error.message, true);
  } finally {
    pending = false; updateDisabled();
  }
}

function loginMode() {
  const key = document.querySelector('input[name="login-mode"]:checked').value === 'key';
  $('#email-login').hidden = key; $('#key-login').hidden = !key;
  $('#login-button').textContent = key ? 'LOGINKEY 적용' : '로그인';
}
for (const input of document.querySelectorAll('[name="login-mode"]')) input.addEventListener('change', loginMode);
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const mode = document.querySelector('input[name="login-mode"]:checked').value;
  const data = mode === 'key' ? { mode, loginKey: $('#login-key').value } : { mode, email: $('#email').value, password: $('#password').value };
  const result = await action('/api/login', data);
  if (result) { $('#password').value = ''; $('#login-key').value = ''; $('#account-section').open = false; }
});
$('#from-enabled').addEventListener('change', updateDisabled);
$('#to-enabled').addEventListener('change', updateDisabled);
jobForm.addEventListener('submit', async event => {
  event.preventDefault();
  const result = await action('/api/download', { settings: settings() });
  if (result && matchMedia('(max-width: 850px)').matches) $('.monitor-column').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
$('#save-settings').addEventListener('click', () => action('/api/settings', { settings: settings() }));
$('#add-queue').addEventListener('click', () => action('/api/queue/add', { settings: settings() }));
$('#stop-button').addEventListener('click', () => action('/api/stop'));
$('#queue-start').addEventListener('click', async () => {
  const result = await action('/api/queue/start');
  if (result) {
    showPanel('download');
    if (matchMedia('(max-width: 850px)').matches) $('.monitor-column').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
});
$('#queue-clear').addEventListener('click', () => action('/api/queue/clear'));
$('#queue-remove').addEventListener('click', () => {
  const indices = [...document.querySelectorAll('#queue input:checked')].map(input => Number(input.value));
  if (!indices.length) return notice('삭제할 작품을 선택해 주세요.', true);
  action('/api/queue/remove', { indices });
});
$('#copy-log').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(state?.log || ''); notice('로그를 복사했습니다.'); }
  catch { notice('복사할 로그를 직접 선택해 주세요.', true); }
});

function showPanel(name) {
  for (const id of ['download', 'queue', 'files']) {
    $(`#${id}-panel`).hidden = id !== name;
    $(`#${id}-tab`).classList.toggle('active', id === name);
    $(`#${id}-tab`).setAttribute('aria-pressed', String(id === name));
  }
  if (name === 'files') refreshFiles();
}
for (const id of ['download', 'queue', 'files']) $(`#${id}-tab`).addEventListener('click', () => showPanel(id));

function formatSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
async function refreshFiles() {
  const version = ++fileRefreshVersion;
  try {
    const response = await fetch('/api/files', { cache: 'no-store' });
    if (!response.ok) throw new Error();
    const files = (await response.json()).filter(file => file.type === 'file' && /\.(epub|txt)$/i.test(file.name))
      .sort((a, b) => b.mtime.localeCompare(a.mtime));
    if (version !== fileRefreshVersion) return;
    storedFiles = files; filesReady = true;
    $('#file-count').textContent = files.length; $('#files').replaceChildren();
    for (const file of files) {
      const item = document.createElement('li');
      const kind = document.createElement('span'); kind.className = 'file-kind'; kind.textContent = file.name.split('.').pop().toUpperCase();
      const details = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = file.name;
      const meta = document.createElement('small'); meta.textContent = `${formatSize(file.size)} · ${new Date(file.mtime).toLocaleString('ko-KR')}`;
      details.append(title, meta);
      const link = document.createElement('a'); link.href = `/files/${encodeURIComponent(file.name)}`; link.download = file.name; link.textContent = '다운로드';
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'danger';
      remove.dataset.fileDelete = file.name; remove.textContent = '삭제';
      remove.setAttribute('aria-label', `${file.name} 삭제`);
      remove.addEventListener('click', async () => {
        if (!confirm(`“${file.name}” 파일을 삭제할까요?\n삭제 후 복구할 수 없습니다.`)) return;
        await action('/api/files/delete', { name: file.name });
        await refreshFiles();
      });
      const actions = document.createElement('div'); actions.className = 'file-actions'; actions.append(link, remove);
      item.append(kind, details, actions); $('#files').append(item);
    }
    $('#files-status').textContent = files.length ? '' : '아직 저장된 파일이 없습니다.';
    $('#files-status').hidden = files.length > 0;
  } catch {
    if (version !== fileRefreshVersion) return;
    filesReady = false;
    $('#files-status').hidden = false;
    $('#files-status').textContent = '파일 목록을 불러오지 못했습니다. 새로고침해 주세요.';
  }
  updateDisabled();
}
$('#refresh').addEventListener('click', refreshFiles);
$('#files-clear').addEventListener('click', async () => {
  if (!confirm(`저장된 EPUB/TXT 파일 ${storedFiles.length}개를 모두 삭제할까요?\n삭제 후 복구할 수 없습니다.`)) return;
  await action('/api/files/clear');
  await refreshFiles();
});
loginMode(); poll(); refreshFiles(); setInterval(refreshFiles, 10000);

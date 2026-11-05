const csrfMeta = document.querySelector('meta[name="csrf-token"]');
let csrfToken = csrfMeta?.content || '';
let currentUser = null;
let mediaRecorder = null;
let recordedBlob = null;
let recordingStarted = 0;
let recordingTimer = null;
let audioStream = null;
let audioContext = null;
let waveformFrame = null;
let historyChart = null;
let authMode = 'login';
let latestAttemptId = null;
let toastTimer = null;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('X-CSRF-Token', csrfToken);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
}

function setAuthMode(mode) {
  authMode = mode;
  const isRegister = mode === 'register';
  $('#name-field').classList.toggle('hidden', !isRegister);
  $('#level-field').classList.toggle('hidden', !isRegister);
  $('#auth-title').textContent = isRegister ? 'Start your practice' : 'Welcome back';
  $('#auth-subtitle').textContent = isRegister ? 'Set your level. You can change it later.' : 'Pick up where your last practice ended.';
  $('#auth-submit').textContent = isRegister ? 'Create account' : 'Sign in';
  $('#signin-tab').classList.toggle('active', !isRegister);
  $('#register-tab').classList.toggle('active', isRegister);
  $('#signin-tab').setAttribute('aria-selected', String(!isRegister));
  $('#register-tab').setAttribute('aria-selected', String(isRegister));
  $('#auth-message').textContent = '';
  $('#auth-form [name="password"]').setAttribute('autocomplete', isRegister ? 'new-password' : 'current-password');
}

function showApp(user) {
  currentUser = user;
  $('#auth-view').classList.add('hidden');
  $('#app-view').classList.remove('hidden');
  $('#logout-button').classList.remove('hidden');
  $('#user-name').textContent = user.display_name;
  $('#user-initial').textContent = (user.display_name || 'S').trim().slice(0, 1).toUpperCase();
  $('#user-level').textContent = `${capitalize(user.level)} practice`;
  $('#level-select').value = user.level;
  $('#profile-name').value = user.display_name;
  $('#profile-level').value = user.level;
  $('#admin-nav').classList.toggle('hidden', user.role !== 'admin');
  loadDashboard();
  loadSentences();
}

function showAuth() {
  $('#app-view').classList.add('hidden');
  $('#auth-view').classList.remove('hidden');
  $('#logout-button').classList.add('hidden');
}

function capitalize(value) { return value ? value[0].toUpperCase() + value.slice(1) : ''; }

$('#signin-tab').addEventListener('click', () => setAuthMode('login'));
$('#register-tab').addEventListener('click', () => setAuthMode('register'));

$('#auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const payload = Object.fromEntries(form.entries());
  const endpoint = authMode === 'register' ? '/api/register' : '/api/login';
  $('#auth-submit').disabled = true;
  $('#auth-message').textContent = '';
  try {
    const data = await api(endpoint, { method: 'POST', body: payload });
    csrfToken = data.csrf_token || csrfToken;
    currentUser = data.user;
    formElement.reset();
    showApp(data.user);
  } catch (error) {
    $('#auth-message').textContent = error.message;
  } finally {
    $('#auth-submit').disabled = false;
  }
});

$('#logout-button').addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST', body: {} }); }
  finally { window.location.reload(); }
});

$$('.nav-item').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));

function switchView(name) {
  $$('.content-view').forEach((view) => view.classList.add('hidden'));
  $(`#${name}-view`)?.classList.remove('hidden');
  $$('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === name));
  if (name === 'history') loadHistory();
  if (name === 'admin') loadAdmin();
}

$('#level-select').addEventListener('change', async (event) => {
  const level = event.currentTarget.value;
  try {
    const data = await api('/api/profile', { method: 'PATCH', body: { display_name: currentUser.display_name, level } });
    currentUser = data.user;
    $('#user-level').textContent = `${capitalize(level)} practice`;
    await loadSentences();
  } catch (error) { showToast(error.message); }
});

async function loadSentences() {
  try {
    const data = await api(`/api/sentences?level=${encodeURIComponent($('#level-select').value)}`);
    const select = $('#sentence-select');
    select.replaceChildren();
    data.sentences.forEach((sentence) => {
      const option = document.createElement('option');
      option.value = sentence.id;
      option.textContent = sentence.text;
      option.dataset.category = sentence.category;
      select.append(option);
    });
    const customOption = document.createElement('option');
    customOption.value = 'custom';
    customOption.textContent = 'Write your own sentence or paragraph';
    customOption.dataset.category = 'Your text';
    select.append(customOption);
    updatePrompt();
  } catch (error) { showToast(error.message); }
}

function updatePrompt() {
  const option = $('#sentence-select').selectedOptions[0];
  if (!option) return;
  const isCustom = option.value === 'custom';
  $('#custom-prompt-field').classList.toggle('hidden', !isCustom);
  const prompt = isCustom ? $('#custom-prompt').value.trim() : option.textContent.trim();
  $('#sentence-prompt').textContent = prompt || 'Enter a sentence or paragraph above.';
  $('#sentence-category').textContent = option.dataset.category;
  const count = prompt ? prompt.split(/\s+/).length : 0;
  $('#prompt-word-count').textContent = `${count} words`;
  $('#record-button').disabled = isCustom && !prompt;
  resetRecording();
}

$('#sentence-select').addEventListener('change', updatePrompt);
$('#custom-prompt').addEventListener('input', updatePrompt);
$('#challenge-button').addEventListener('click', () => {
  const challenge = $('#challenge-text').textContent;
  const match = [...$('#sentence-select').options].find((option) => option.textContent === challenge);
  if (match) {
    $('#sentence-select').value = match.value;
    updatePrompt();
    switchView('practice');
  }
});

async function loadDashboard() {
  try {
    const data = await api('/api/dashboard');
    $('#sessions-count').textContent = data.total_attempts;
    $('#challenge-text').textContent = data.daily_challenge.text;
    const last = data.recent_attempts.at(-1);
    $('#last-transcript').textContent = last?.recognized_text || 'Not yet recorded';
  } catch (error) { showToast(error.message); }
}

$('#record-button').addEventListener('click', startRecording);
$('#stop-button').addEventListener('click', stopRecording);

async function startRecording() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    $('#recording-message').textContent = 'This browser does not support microphone recording.';
    return;
  }
  try {
    audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const preferred = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
    const mimeType = preferred.find((type) => MediaRecorder.isTypeSupported(type));
    mediaRecorder = mimeType ? new MediaRecorder(audioStream, { mimeType }) : new MediaRecorder(audioStream);
    const chunks = [];
    mediaRecorder.addEventListener('dataavailable', (event) => { if (event.data.size) chunks.push(event.data); });
    mediaRecorder.addEventListener('stop', () => {
      recordedBlob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
      $('#audio-preview').src = URL.createObjectURL(recordedBlob);
      $('#audio-preview').classList.remove('hidden');
      $('#analyze-button').disabled = recordedBlob.size === 0;
      $('#recording-state').textContent = 'Recording ready';
      stopVisuals();
    }, { once: true });
    mediaRecorder.start(250);
    $('#sentence-select').disabled = true;
    $('#custom-prompt').disabled = true;
    recordingStarted = Date.now();
    $('#recording-panel')?.classList.add('is-recording');
    $('.recording-panel').classList.add('is-recording');
    $('#recording-message').textContent = '';
    $('#recording-state').textContent = 'Listening';
    $('#record-button').classList.add('hidden');
    $('#stop-button').classList.remove('hidden');
    $('#analyze-button').disabled = true;
    recordingTimer = setInterval(updateRecordingClock, 250);
    startWaveform(audioStream);
  } catch (error) {
    $('#recording-message').textContent = error.name === 'NotAllowedError' ? 'Microphone access was not allowed.' : 'Could not start the microphone. Check its connection and permissions.';
  }
}

function updateRecordingClock() {
  const elapsed = Math.floor((Date.now() - recordingStarted) / 1000);
  $('#recording-time').textContent = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`;
  if (elapsed >= 60) stopRecording();
}

function stopRecording() {
  if (!mediaRecorder || mediaRecorder.state === 'inactive') return;
  mediaRecorder.stop();
  audioStream?.getTracks().forEach((track) => track.stop());
  audioStream = null;
  clearInterval(recordingTimer);
  recordingTimer = null;
  $('#record-button').classList.remove('hidden');
  $('#stop-button').classList.add('hidden');
  $('#sentence-select').disabled = false;
  $('#custom-prompt').disabled = false;
  $('.recording-panel').classList.remove('is-recording');
  $('#recording-time').textContent = formatClock((Date.now() - recordingStarted) / 1000);
}

function formatClock(seconds) {
  const elapsed = Math.floor(seconds);
  return `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`;
}

function resetRecording() {
  if (mediaRecorder?.state === 'recording') stopRecording();
  recordedBlob = null;
  $('#audio-preview').pause();
  $('#audio-preview').removeAttribute('src');
  $('#audio-preview').classList.add('hidden');
  $('#analyze-button').disabled = true;
  $('#recording-time').textContent = '00:00';
  $('#recording-state').textContent = 'Ready when you are';
  $('#recording-message').textContent = '';
  drawIdleWaveform();
}

function startWaveform(stream) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return;
  audioContext = new AudioContextClass();
  const source = audioContext.createMediaStreamSource(stream);
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const samples = new Uint8Array(analyser.frequencyBinCount);
  const canvas = $('#waveform');
  const context = canvas.getContext('2d');
  const draw = () => {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
      canvas.width = width * ratio;
      canvas.height = height * ratio;
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    analyser.getByteTimeDomainData(samples);
    context.strokeStyle = '#1e6b58';
    context.lineWidth = 1.5;
    context.beginPath();
    const step = width / samples.length;
    samples.forEach((sample, index) => {
      const y = (sample / 255) * height;
      if (index === 0) context.moveTo(0, y);
      else context.lineTo(index * step, y);
    });
    context.stroke();
    waveformFrame = requestAnimationFrame(draw);
  };
  draw();
}

function stopVisuals() {
  if (waveformFrame) cancelAnimationFrame(waveformFrame);
  waveformFrame = null;
  audioContext?.close();
  audioContext = null;
  drawIdleWaveform();
}

function drawIdleWaveform() {
  const canvas = $('#waveform');
  if (!canvas) return;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 600;
  const height = canvas.clientHeight || 70;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.strokeStyle = '#b7c8b7';
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(0, height / 2);
  context.lineTo(width, height / 2);
  context.stroke();
}

$('#analyze-button').addEventListener('click', submitRecording);

async function submitRecording() {
  if (!recordedBlob) return;
  const form = new FormData();
  const extension = recordedBlob.type.includes('ogg') ? 'ogg' : recordedBlob.type.includes('mp4') ? 'mp4' : 'webm';
  form.append('audio', recordedBlob, `practice.${extension}`);
  if ($('#sentence-select').value === 'custom') {
    const customText = $('#custom-prompt').value.trim();
    if (!customText) {
      $('#recording-message').textContent = 'Enter a sentence or paragraph before recording.';
      return;
    }
    form.append('custom_text', customText);
  } else {
    form.append('sentence_id', $('#sentence-select').value);
  }
  form.append('store_audio', String($('#store-audio').checked));
  $('#analyze-button').disabled = true;
  $('#analyze-button').textContent = 'Analyzing…';
  $('#recording-message').textContent = 'Running local models. The first run may take longer while weights download.';
  $('#review-status').textContent = 'ANALYZING';
  try {
    const result = await api('/api/analyze', { method: 'POST', body: form });
    latestAttemptId = result.attempt_id;
    renderResults(result);
    await loadDashboard();
    showToast('Practice analysis is ready.');
  } catch (error) {
    $('#recording-message').textContent = error.message;
    $('#review-status').textContent = 'NOT READY';
  } finally {
    $('#analyze-button').disabled = false;
    $('#analyze-button').innerHTML = 'Analyze recording <span aria-hidden="true">→</span>';
  }
}

function renderResults(result) {
  $('#result-empty').classList.add('hidden');
  $('#results-content').classList.remove('hidden');
  $('#review-status').textContent = result.pronunciation.status === 'not_calibrated' ? 'PRACTICE NOTES' : 'REVIEW';
  $('#recognized-text').textContent = result.transcript || 'No words recognized';
  $('#transcript-confidence').textContent = `${Math.round((result.language_confidence || 0) * 100)}%`;
  const comparison = result.word_comparison;
  $('#word-edits').textContent = comparison.word_error_rate === null ? '—' : `${Math.round(comparison.word_error_rate * 100)}%`;
  $('#word-edit-types').textContent = `${comparison.substitutions} sub · ${comparison.deletions} omit · ${comparison.insertions} add`;
  $('#speaking-rate').textContent = result.fluency.speaking_rate_wpm ?? '—';
  $('#longest-pause').textContent = `${result.fluency.longest_pause_seconds.toFixed(1)}s`;
  $('#pause-summary').textContent = `${result.fluency.pause_count} pauses · ${result.fluency.duration_seconds.toFixed(1)}s total`;
  renderWordComparison(comparison);
  const phonemes = result.phonemes;
  $('#expected-phones').textContent = phonemes.expected || 'Not available';
  $('#recognized-phones').textContent = phonemes.recognized || phonemes.reason || 'Not available';
  $('#phone-notice').textContent = phonemes.notice || 'Phone-level analysis is unavailable in this setup.';
  const feedback = $('#feedback-list');
  feedback.replaceChildren();
  result.feedback.forEach((item) => {
    const listItem = document.createElement('li');
    listItem.textContent = item;
    feedback.append(listItem);
  });
  $('#method-note').textContent = result.methodology_note;
  $('#download-report').classList.remove('hidden');
  $('#recording-message').textContent = '';
}

function renderWordComparison(comparison) {
  const container = $('#word-comparison');
  container.replaceChildren();
  comparison.alignment.forEach((item) => {
    const label = item.type === 'match'
      ? item.expected
      : item.type === 'deletion'
        ? `${item.expected} · omitted`
        : item.type === 'insertion'
          ? `+ ${item.recognized}`
          : `${item.expected} → ${item.recognized}`;
    addWordChip(container, label, item.type);
  });
  if (!comparison.alignment.length) addWordChip(container, 'No transcript to compare', 'match');
}

function addWordChip(container, label, type) {
  const chip = document.createElement('span');
  chip.className = `word-chip ${type}`;
  chip.textContent = label;
  container.append(chip);
}

$('#download-report').addEventListener('click', () => {
  if (latestAttemptId) window.location.assign(`/api/attempts/${latestAttemptId}/report`);
});

async function loadHistory() {
  try {
    const data = await api('/api/attempts');
    renderHistory(data.attempts);
  } catch (error) { showToast(error.message); }
}

function renderHistory(attempts) {
  const list = $('#history-list');
  list.replaceChildren();
  if (!attempts.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-history';
    empty.textContent = 'Your completed practices will collect here.';
    list.append(empty);
  }
  attempts.forEach((attempt) => {
    const row = document.createElement('article');
    row.className = 'history-row';
    const title = document.createElement('div');
    const expected = document.createElement('strong');
    expected.textContent = attempt.sentence_text;
    const date = document.createElement('small');
    date.textContent = new Date(attempt.created_at).toLocaleString();
    title.append(expected, date);
    const summary = document.createElement('div');
    const errors = attempt.analysis.word_comparison;
    const result = document.createElement('strong');
    result.textContent = `${errors.substitutions + errors.deletions + errors.insertions} word changes`;
    const recognized = document.createElement('small');
    recognized.textContent = attempt.recognized_text || 'No words recognized';
    summary.append(result, recognized);
    const actions = document.createElement('div');
    actions.className = 'history-actions';
    const report = document.createElement('button');
    report.type = 'button';
    report.textContent = 'PDF';
    report.addEventListener('click', () => window.location.assign(`/api/attempts/${attempt.id}/report`));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = 'Delete';
    remove.addEventListener('click', () => deleteAttempt(attempt.id));
    actions.append(report, remove);
    row.append(title, summary, actions);
    list.append(row);
  });
  renderHistoryChart(attempts.slice(-12));
}

function renderHistoryChart(attempts) {
  const canvas = $('#history-chart');
  if (!canvas || typeof Chart === 'undefined') return;
  historyChart?.destroy();
  historyChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: attempts.map((attempt) => new Date(attempt.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })),
      datasets: [{
        label: 'Transcript word changes',
        data: attempts.map((attempt) => {
          const edit = attempt.analysis.word_comparison;
          return edit.substitutions + edit.deletions + edit.insertions;
        }),
        borderColor: '#1e6b58',
        backgroundColor: 'rgba(30,107,88,.09)',
        pointBackgroundColor: '#d7ed9f',
        pointBorderColor: '#1e6b58',
        pointRadius: 4,
        tension: .28,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: '#e5e9e3' } },
        x: { grid: { display: false } },
      },
    },
  });
}

async function deleteAttempt(id) {
  if (!window.confirm('Delete this practice and any retained audio?')) return;
  try {
    await api(`/api/attempts/${id}`, { method: 'DELETE' });
    await loadHistory();
    await loadDashboard();
  } catch (error) { showToast(error.message); }
}

$('#profile-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await api('/api/profile', { method: 'PATCH', body: { display_name: $('#profile-name').value, level: $('#profile-level').value } });
    currentUser = data.user;
    $('#user-name').textContent = data.user.display_name;
    $('#user-initial').textContent = data.user.display_name.trim().slice(0, 1).toUpperCase();
    $('#user-level').textContent = `${capitalize(data.user.level)} practice`;
    $('#profile-message').textContent = 'Profile updated.';
  } catch (error) { $('#profile-message').textContent = error.message; }
});

$('#delete-history').addEventListener('click', async () => {
  if (!window.confirm('Permanently delete all practice history and retained audio?')) return;
  try {
    await api('/api/attempts', { method: 'DELETE' });
    await loadDashboard();
    await loadHistory();
    $('#profile-message').textContent = 'Practice history deleted.';
  } catch (error) { showToast(error.message); }
});

$('#delete-account').addEventListener('click', async () => {
  if (!window.confirm('Delete your account, practice history, and retained recordings? This cannot be undone.')) return;
  try {
    await api('/api/account', { method: 'DELETE' });
    window.location.reload();
  } catch (error) { showToast(error.message); }
});

async function loadAdmin() {
  try {
    const data = await api('/api/admin/summary');
    const metrics = $('#admin-metrics');
    metrics.replaceChildren();
    [['Student accounts', data.users], ['Practice attempts', data.attempts], ['Retained recordings', data.retained_recordings]].forEach(([label, value]) => {
      const item = document.createElement('div');
      item.className = 'admin-stat';
      const name = document.createElement('span');
      name.textContent = label;
      const count = document.createElement('strong');
      count.textContent = value;
      item.append(name, count);
      metrics.append(item);
    });
  } catch (error) { showToast(error.message); }
}

window.addEventListener('resize', drawIdleWaveform);
drawIdleWaveform();
fetch('/api/me', { credentials: 'same-origin' })
  .then((response) => response.json())
  .then((data) => data.user ? showApp(data.user) : showAuth())
  .catch(() => showAuth());
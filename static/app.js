/* ===== Show Tech Reader — app.js ===== */
'use strict';

// ── State ──────────────────────────────────────────────────────────────────
const state = {
  sessionId: null,
  element: '',
  domain: '',
  smartReduce: false,
  page: 0,
  totalResults: 0,
  loading: false,
};

// ── DOM refs ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const progressContainer = $('progress-container');
const progressBar = $('progress-bar');
const progressText = $('progress-text');
const sessionList = $('session-list');
const elementInput = $('element-input');
const autocompleteList = $('autocomplete-list');
const searchBtn = $('search-btn');
const currentElementEl = $('current-element');
const resultCountEl = $('result-count');
const exportWrap = $('export-wrap');
const cardsContainer = $('cards-container');
const alertsContainer = $('alerts-container');
const timelineContainer = $('timeline-container');
const timelineEvents = $('timeline-events');
const evidenceContainer = $('evidence-container');
const loadMoreBtn = $('load-more-btn');
const emptyState = $('empty-state');

// ── Blue relevance gradient ────────────────────────────────────────────────
function relColor(score) {
  if (score >= 0.80) return { bg: '#0D47A1', text: '#fff' };
  if (score >= 0.50) return { bg: '#1565C0', text: '#fff' };
  if (score >= 0.20) return { bg: '#2196F3', text: '#fff' };
  if (score >= 0.10) return { bg: '#BBDEFB', text: '#1a1a2e' };
  return { bg: '#E3F2FD', text: '#1a1a2e' };
}

// ── Ingest helpers ──────────────────────────────────────────────────────────
async function ingestFromPath(p) {
  if (!p) return;
  progressContainer.style.display = 'block';
  progressBar.style.width = '2%';
  progressText.textContent = `Indexing ${p.split(/[\\/]/).pop()}…`;
  try {
    const res = await fetch('/api/sessions/ingest-path', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: p }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    await waitForIngestion(data.session_id, data.since ?? 0, data.filename);
    progressBar.style.width = '100%';
    refreshSessionList(data.session_id);
    setTimeout(() => { progressContainer.style.display = 'none'; elementInput.focus(); }, 1500);
  } catch (err) {
    progressText.textContent = `Error: ${err.message}`;
  }
}

// ── Browse & Read — file picker → stream to server → index from saved path ────
$('browse-read-btn').addEventListener('click', () => $('file-picker-read').click());

$('file-picker-read').addEventListener('change', async () => {
  const files = Array.from($('file-picker-read').files).slice(0, 3);
  if (!files.length) return;
  $('browse-read-btn').disabled = true;
  progressContainer.style.display = 'block';
  let sharedSessionId = null;  // all files in the batch share one session
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    $('selected-file').textContent = files.length > 1 ? `${i + 1}/${files.length}: ${file.name}` : file.name;
    progressBar.style.width = `${Math.round((i / files.length) * 90 + 2)}%`;
    progressText.textContent = `Sending ${file.name}…`;
    try {
      // First file creates the session; subsequent files join it
      const sidParam = sharedSessionId ? `&session_id=${encodeURIComponent(sharedSessionId)}` : '';
      const res = await fetch(`/api/sessions/ingest-stream?filename=${encodeURIComponent(file.name)}${sidParam}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': file.size },
        body: file,
        duplex: 'half',
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      sharedSessionId = data.session_id;
      progressText.textContent = `Indexing ${file.name} (${i + 1}/${files.length})…`;
      await waitForIngestion(data.session_id, data.since ?? 0, file.name);
      refreshSessionList(data.session_id);
    } catch (err) {
      progressText.textContent = `Error on ${file.name}: ${err.message}`;
      break;
    }
  }
  progressBar.style.width = '100%';
  if (sharedSessionId) setTimeout(() => { progressContainer.style.display = 'none'; elementInput.focus(); }, 1500);
  $('browse-read-btn').disabled = false;
  $('file-picker-read').value = '';
});

// ── Browse & Upload — browser file picker, uploads bytes (remote server use) ─
$('browse-upload-btn').addEventListener('click', () => $('file-picker-upload').click());

$('file-picker-upload').addEventListener('change', async () => {
  const files = Array.from($('file-picker-upload').files).slice(0, 3);
  if (!files.length) return;
  $('browse-upload-btn').disabled = true;
  progressContainer.style.display = 'block';
  let sharedSessionId = null;
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    $('selected-file').textContent = files.length > 1 ? `${i + 1}/${files.length}: ${file.name}` : file.name;
    progressBar.style.width = `${Math.round((i / files.length) * 90 + 2)}%`;
    progressText.textContent = `Uploading ${file.name}…`;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const url = sharedSessionId
        ? `/api/sessions/ingest?session_id=${encodeURIComponent(sharedSessionId)}`
        : '/api/sessions/ingest';
      const res = await fetch(url, { method: 'POST', body: fd });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      sharedSessionId = data.session_id;
      progressText.textContent = `Indexing ${file.name} (${i + 1}/${files.length})…`;
      await waitForIngestion(data.session_id, data.since ?? 0, data.filename);
      refreshSessionList(data.session_id);
    } catch (err) {
      progressText.textContent = `Error on ${file.name}: ${err.message}`;
      break;
    }
  }
  progressBar.style.width = '100%';
  if (sharedSessionId) setTimeout(() => { progressContainer.style.display = 'none'; elementInput.focus(); }, 1500);
  $('browse-upload-btn').disabled = false;
  $('file-picker-upload').value = '';
});

// Returns a Promise that resolves when the ingest SSE stream signals done/error.
function waitForIngestion(sessionId, since, label) {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/sessions/${sessionId}/progress?since=${since}`);
    es.onmessage = e => {
      const data = JSON.parse(e.data);
      const membersDone = data.members_done || 0;
      const membersTotal = Math.max(data.members_total || 1, 1);
      const pct = Math.max(2, Math.min(95, Math.round((membersDone / membersTotal) * 95)));
      progressBar.style.width = pct + '%';
      progressText.textContent =
        `${label} — ${membersDone}/${membersTotal} members — ` +
        `${data.chunks_total || 0} chunks — ${data.current_file || ''}`;
      if (data.status === 'done') {
        progressText.textContent = `Done: ${data.chunks_total} chunks, ${data.entities_found} entities`;
        es.close(); resolve();
      }
      if (data.status === 'error') { es.close(); reject(new Error(data.error || 'ingest error')); }
    };
    es.onerror = () => { es.close(); resolve(); };
  });
}

// ── Sessions ───────────────────────────────────────────────────────────────
async function loadSessions() {
  try {
    const res = await fetch('/api/sessions');
    const sessions = await res.json();
    sessionList.innerHTML = '<option value="">— select session —</option>';
    for (const s of sessions) {
      const opt = document.createElement('option');
      opt.value = s.session_id;
      const date = s.created_at ? s.created_at.replace('T', ' ').slice(0, 16) : '';
      const statusTag = (s.status === 'indexing' || s.status === 'created') ? ' ⏳' : '';
      opt.textContent = `${s.original_filename || s.session_id} [${date}]${statusTag}`;
      if (s.status === 'error') opt.style.color = '#EF9A9A';
      sessionList.appendChild(opt);
    }
    if (sessions.length > 0 && !state.sessionId) {
      const ready = sessions.find(s => s.status === 'ready') || sessions[0];
      sessionList.value = ready.session_id;
      state.sessionId = ready.session_id;
      updateSearchBtn();
    }
  } catch (e) { /* ignore */ }
}

function refreshSessionList(selectId) {
  loadSessions().then(() => {
    if (selectId) {
      sessionList.value = selectId;
      state.sessionId = selectId;
      updateSearchBtn();
    }
  });
}

sessionList.addEventListener('change', () => {
  state.sessionId = sessionList.value || null;
  updateSearchBtn();
  clearResults();
});

// ── Element autocomplete ───────────────────────────────────────────────────
let acDebounce = null;
let acSelected = -1;

elementInput.addEventListener('input', () => {
  clearTimeout(acDebounce);
  acDebounce = setTimeout(fetchAutocomplete, 200);
  updateSearchBtn();
});

elementInput.addEventListener('keydown', e => {
  const items = autocompleteList.querySelectorAll('.ac-item');
  if (e.key === 'ArrowDown') {
    acSelected = Math.min(acSelected + 1, items.length - 1);
    highlightAc(items);
    e.preventDefault();
  } else if (e.key === 'ArrowUp') {
    acSelected = Math.max(acSelected - 1, -1);
    highlightAc(items);
    e.preventDefault();
  } else if (e.key === 'Enter') {
    if (acSelected >= 0 && items[acSelected]) {
      selectAcItem(items[acSelected].dataset.canonical);
    } else {
      hideAutocomplete();
      doSearch();
    }
  } else if (e.key === 'Escape') {
    hideAutocomplete();
  }
});

document.addEventListener('click', e => {
  if (!e.target.closest('#element-wrap')) hideAutocomplete();
});

async function fetchAutocomplete() {
  const q = elementInput.value.trim();
  if (!q || !state.sessionId) { hideAutocomplete(); return; }

  try {
    const res = await fetch(
      `/api/sessions/${state.sessionId}/entities?q=${encodeURIComponent(q)}&limit=15`
    );
    const items = await res.json();
    renderAutocomplete(items);
  } catch (e) { hideAutocomplete(); }
}

function renderAutocomplete(items) {
  if (!items.length) { hideAutocomplete(); return; }
  autocompleteList.innerHTML = '';
  acSelected = -1;
  for (const item of items) {
    const div = document.createElement('div');
    div.className = 'ac-item';
    div.dataset.canonical = item.canonical;
    div.innerHTML = `${escHtml(item.canonical)}<span class="ac-type">${item.type}</span>`;
    div.addEventListener('click', () => selectAcItem(item.canonical));
    autocompleteList.appendChild(div);
  }
  autocompleteList.style.display = 'block';
}

function highlightAc(items) {
  items.forEach((el, i) => el.classList.toggle('selected', i === acSelected));
  if (acSelected >= 0 && items[acSelected]) {
    items[acSelected].scrollIntoView({ block: 'nearest' });
  }
}

function selectAcItem(canonical) {
  elementInput.value = canonical;
  state.element = canonical;
  hideAutocomplete();
  updateSearchBtn();
}

function hideAutocomplete() {
  autocompleteList.style.display = 'none';
}

// ── Domain tabs ────────────────────────────────────────────────────────────
document.querySelectorAll('.domain-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.domain-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    state.domain = tab.dataset.domain;
    if (state.element && state.sessionId) doSearch();
  });
});

// ── Toggles ────────────────────────────────────────────────────────────────
$('toggle-smart').addEventListener('change', e => {
  state.smartReduce = e.target.checked;
  if (state.element && state.sessionId) doSearch();
});
$('toggle-dupes').addEventListener('change', () => {
  if (state.element && state.sessionId) doSearch();
});

// ── Search ────────────────────────────────────────────────────────────────
searchBtn.addEventListener('click', doSearch);

function updateSearchBtn() {
  searchBtn.disabled = !(state.sessionId && elementInput.value.trim());
}

async function doSearch(append = false) {
  const element = elementInput.value.trim();
  if (!element || !state.sessionId) return;

  state.element = element;
  if (!append) {
    state.page = 0;
    clearResults();
  }
  state.loading = true;
  searchBtn.disabled = true;
  currentElementEl.textContent = element;
  showSpinner();

  try {
    const body = {
      element,
      domain: state.domain || null,
      smart_reduce: state.smartReduce,
      show_duplicates: $('toggle-dupes').checked,
      page: state.page,
      limit: 50,
    };

    // Fire query + relationship + graph fetch in parallel
    const [res, relRes, graphRes] = await Promise.all([
      fetch(`/api/sessions/${state.sessionId}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
      fetch(`/api/sessions/${state.sessionId}/relationships?element=${encodeURIComponent(element)}`),
      fetch(`/api/sessions/${state.sessionId}/graph?element=${encodeURIComponent(element)}`),
    ]);

    const data = await res.json();
    const relData = relRes.ok ? await relRes.json() : null;
    const graphData = graphRes.ok ? await graphRes.json() : null;

    if (relData) renderRelationships(relData, element);
    else hideRelationships();

    if (graphData) renderGraphPanels(graphData);
    else hideGraphPanels();

    renderResults(data, append);
    exportWrap.style.display = 'flex';
  } catch (e) {
    evidenceContainer.innerHTML = `<div style="color:#EF9A9A;padding:20px;">Error: ${escHtml(e.message)}</div>`;
  } finally {
    state.loading = false;
    searchBtn.disabled = false;
  }
}

function showSpinner() {
  emptyState && (emptyState.style.display = 'none');
  if (!document.querySelector('.searching-msg')) {
    const msg = document.createElement('div');
    msg.className = 'searching-msg';
    msg.style.cssText = 'text-align:center;padding:30px;color:#9090A8;';
    msg.innerHTML = '<div class="spinner"></div> Searching…';
    evidenceContainer.prepend(msg);
  }
}

// ── Render results ─────────────────────────────────────────────────────────
function renderResults(data, append) {
  // Remove spinner
  evidenceContainer.querySelector('.searching-msg')?.remove();

  if (data.mode === 'smart_reduce') {
    renderSmartReduce(data);
    return;
  }

  const results = data.results || [];
  state.totalResults = data.total || results.length;
  resultCountEl.textContent = `${state.totalResults} results`;

  // Daemon hints available in standard mode too
  if (data.daemon_hints?.length) renderDaemonHints(data.daemon_hints);
  else hideDaemonHints();

  if (!results.length && !append) {
    evidenceContainer.innerHTML = '';
    const es = document.createElement('div');
    es.id = 'empty-state';
    es.innerHTML = '<div class="icon">&#x26A0;</div><div>No results found for this element</div>';
    evidenceContainer.appendChild(es);
    loadMoreBtn.style.display = 'none';
    return;
  }

  if (!append) evidenceContainer.innerHTML = '';

  const existingCount = evidenceContainer.querySelectorAll('.chunk-block').length;
  results.forEach((r, i) => {
    const block = buildChunkBlock(r);
    // Auto-expand top 5 results (highest relevance)
    if (existingCount + i < 5) block.classList.add('expanded');
    evidenceContainer.appendChild(block);
  });

  // Load more
  const hasMore = state.page * 50 + results.length < state.totalResults;
  loadMoreBtn.style.display = hasMore ? 'block' : 'none';
}

function renderSmartReduce(data) {
  resultCountEl.textContent = `${data.evidence?.length || 0} results (Smart Reduce)`;

  // Cards
  if (data.top_cards?.length) {
    cardsContainer.style.display = 'flex';
    cardsContainer.innerHTML = '';
    for (const card of data.top_cards) {
      const div = document.createElement('div');
      const statusClass = card.status ? `status-${card.status}` : '';
      div.className = `card ${statusClass}`;
      div.innerHTML = `<div class="card-key">${escHtml(card.key)}</div><div class="card-value">${escHtml(card.value)}</div>`;
      cardsContainer.appendChild(div);
    }
  }

  // Alerts
  if (data.alerts?.length) {
    alertsContainer.style.display = 'block';
    alertsContainer.innerHTML = data.alerts.map(
      a => `<div class="alert-line">&#x26A0; ${escHtml(a)}</div>`
    ).join('');
  }

  // Timeline
  if (data.timeline?.length) {
    timelineContainer.style.display = 'block';
    timelineEvents.innerHTML = data.timeline.map(
      ev => `<div class="timeline-event"><span class="timeline-ts">${escHtml(ev.timestamp)}</span><span class="timeline-msg">${escHtml(ev.message)}</span></div>`
    ).join('');
  }

  // Daemon hints
  if (data.daemon_hints?.length) renderDaemonHints(data.daemon_hints);
  else hideDaemonHints();

  // Evidence
  evidenceContainer.innerHTML = '';
  (data.evidence || []).forEach((r, i) => {
    const block = buildChunkBlock(r);
    if (i < 5) block.classList.add('expanded');
    evidenceContainer.appendChild(block);
  });
  loadMoreBtn.style.display = 'none';
}

// ── Relationship / ownership banner ───────────────────────────────────────
const relBanner    = $('rel-banner');
const relOwnership = $('rel-ownership');
const relChain     = $('rel-chain');
const relPeer      = $('rel-peer');

function hideRelationships() {
  relBanner.style.display = 'none';
}

function renderRelationships(rel, element) {
  const hasAnything = rel.ownership || rel.port_channels?.length || rel.vpc_ids?.length
                   || rel.is_peer_link || rel.vpc_domain_id || rel.vpc_peer_keepalive;
  if (!hasAnything) { hideRelationships(); return; }

  relBanner.style.display = 'flex';

  // ── Ownership block ──────────────────────────────────────────────────────
  relOwnership.innerHTML = '';
  if (rel.ownership) {
    const hint = rel.ownership.health_filter_hint;
    relOwnership.innerHTML =
      `<span class="rel-label">Ownership</span>` +
      `<span class="rel-value">${escHtml(rel.ownership.member_or_slot)}</span>` +
      `<button class="rel-health-btn" data-hint="${escHtml(hint)}">HW health ↗</button>`;
    relOwnership.querySelector('.rel-health-btn').addEventListener('click', e => {
      // Switch to Hardware domain and search for the member/slot
      elementInput.value = e.target.dataset.hint;
      document.querySelectorAll('.domain-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.domain === 'HARDWARE');
      });
      state.domain = 'HARDWARE';
      doSearch();
    });
  }

  // ── Relationship chain ───────────────────────────────────────────────────
  relChain.innerHTML = '';

  const addNode = (label, cssClass, clickFn) => {
    const n = document.createElement('span');
    n.className = `rel-node ${cssClass}`;
    n.textContent = label;
    if (clickFn) n.addEventListener('click', clickFn);
    relChain.appendChild(n);
  };
  const addArrow = () => {
    const a = document.createElement('span');
    a.className = 'rel-arrow';
    a.textContent = '→';
    relChain.appendChild(a);
  };

  if (rel.is_peer_link) {
    addNode('⚠ Peer-link', 'PEER_LINK');
    addArrow();
  }

  // Interface node
  addNode(element, 'IFACE');

  // Port-channel(s)
  const pos = rel.port_channels || [];
  const vpcs = rel.vpc_ids || [];
  pos.forEach((po, idx) => {
    addArrow();
    addNode(po, 'PO', () => {
      elementInput.value = po;
      doSearch();
    });
    if (vpcs[idx]) {
      addArrow();
      addNode(vpcs[idx], 'VPC', () => {
        elementInput.value = vpcs[idx];
        doSearch();
      });
    }
  });

  // vPC domain + peer-link info (even without PO match)
  if (rel.vpc_domain_id && pos.length === 0) {
    const domainSpan = document.createElement('span');
    domainSpan.style.cssText = 'font-size:11px;color:#CE93D8;margin-left:8px;';
    domainSpan.textContent = `vPC domain ${rel.vpc_domain_id}`;
    if (rel.vpc_peer_link) {
      domainSpan.textContent += ` · peer-link: ${rel.vpc_peer_link}`;
    }
    relChain.appendChild(domainSpan);
  }

  // ── Peer session block ───────────────────────────────────────────────────
  relPeer.innerHTML = '';
  if (rel.vpc_peer_keepalive) {
    if (rel.peer_session) {
      const ps = rel.peer_session;
      relPeer.innerHTML =
        `<span class="peer-found">✅ Peer dump loaded` +
        `${ps.hostname ? ': ' + escHtml(ps.hostname) : ''}</span>` +
        `<button class="peer-jump-btn" data-sid="${escHtml(ps.session_id)}">Open peer ↗</button>`;
      relPeer.querySelector('.peer-jump-btn').addEventListener('click', e => {
        sessionList.value = e.target.dataset.sid;
        state.sessionId = e.target.dataset.sid;
        doSearch();
      });
    } else {
      relPeer.innerHTML =
        `<span class="peer-missing">vPC peer: ${escHtml(rel.vpc_peer_keepalive)} — dump not loaded</span>`;
    }
  }
}

// ── Daemon hint rendering ──────────────────────────────────────────────────
const daemonContainer = $('daemon-container');
const daemonChips = $('daemon-chips');
const daemonSubtitle = $('daemon-subtitle');
const daemonDetail = $('daemon-detail');

let activeDaemonChip = null;   // currently selected chip name
let currentDaemonHints = [];

function hideDaemonHints() {
  daemonContainer.style.display = 'none';
  daemonDetail.style.display = 'none';
}

function renderDaemonHints(hints) {
  currentDaemonHints = hints;
  daemonContainer.style.display = 'block';
  daemonSubtitle.textContent = `${hints.length} process${hints.length !== 1 ? 'es' : ''} identified`;
  daemonChips.innerHTML = '';
  daemonDetail.style.display = 'none';
  activeDaemonChip = null;

  for (const h of hints) {
    const chip = document.createElement('div');
    chip.className = `daemon-chip ${h.confidence}`;
    chip.dataset.name = h.name;
    // Trim "what_it_does" to first clause (up to first em-dash or comma)
    const shortWhat = h.what_it_does.split(/—|,/)[0].trim();
    chip.innerHTML =
      `<span class="daemon-chip-name">${escHtml(h.display)}</span>` +
      `<span class="daemon-chip-what">${escHtml(shortWhat)}</span>` +
      `<span class="daemon-chip-conf">${h.confidence}</span>`;
    chip.addEventListener('click', () => toggleDaemonChip(h, chip));
    daemonChips.appendChild(chip);
  }
}

function toggleDaemonChip(hint, chipEl) {
  const allChips = daemonChips.querySelectorAll('.daemon-chip');

  if (activeDaemonChip === hint.name) {
    // Deselect
    allChips.forEach(c => c.classList.remove('active'));
    daemonDetail.style.display = 'none';
    activeDaemonChip = null;
    return;
  }

  allChips.forEach(c => c.classList.remove('active'));
  chipEl.classList.add('active');
  activeDaemonChip = hint.name;
  renderDaemonDetail(hint);
}

function renderDaemonDetail(h) {
  const confColor = { HIGH: '#EF9A9A', MED: '#FFCC80', LOW: '#90CAF9' }[h.confidence] || '#ccc';

  // Reasons section
  const reasonsHtml = h.reasons.length
    ? h.reasons.map(r => `<span class="reason-pill">${escHtml(r)}</span>`).join('')
    : '<span class="reason-pill">keyword match</span>';

  // Evidence section
  const evidenceHtml = h.evidence.length
    ? h.evidence.map(e =>
        `<div class="daemon-evidence-line" data-chunk="${e.chunk_id}" title="Click to jump to chunk">` +
        `${escHtml(e.line_excerpt)}</div>`
      ).join('')
    : '';

  // Commands section — pick first available platform
  let commandsHtml = '';
  for (const [platform, cmds] of Object.entries(h.useful_commands || {})) {
    if (!cmds.length) continue;
    commandsHtml += `<div class="cmd-block"><div class="cmd-platform">${escHtml(platform)}</div>`;
    for (const cmd of cmds) {
      commandsHtml +=
        `<div class="cmd-row">` +
        `<span class="cmd-text">${escHtml(cmd)}</span>` +
        `<button class="cmd-copy" data-cmd="${escHtml(cmd)}">Copy</button>` +
        `</div>`;
    }
    commandsHtml += '</div>';
  }

  daemonDetail.innerHTML = `
    <div class="daemon-detail-header">
      <span class="daemon-detail-title" style="color:${confColor};">${escHtml(h.display)}</span>
      <span style="font-size:11px;color:${confColor};background:rgba(0,0,0,0.3);padding:2px 8px;border-radius:10px;">${h.confidence} confidence</span>
    </div>
    <div class="daemon-detail-what">${escHtml(h.what_it_does)}</div>
    ${h.reasons.length ? `<div class="daemon-detail-section"><h4>Why we think this</h4>${reasonsHtml}</div>` : ''}
    ${evidenceHtml ? `<div class="daemon-detail-section"><h4>Evidence</h4>${evidenceHtml}</div>` : ''}
    ${h.common_symptoms?.length ? `
      <div class="daemon-detail-section">
        <h4>Common symptoms</h4>
        ${h.common_symptoms.map(s => `<div style="font-size:11px;color:#9090A8;padding:1px 0;">• ${escHtml(s)}</div>`).join('')}
      </div>` : ''}
    ${commandsHtml ? `<div class="daemon-detail-section"><h4>Next commands</h4>${commandsHtml}</div>` : ''}
  `;
  daemonDetail.style.display = 'block';

  // Wire evidence line clicks → jump to chunk
  daemonDetail.querySelectorAll('.daemon-evidence-line[data-chunk]').forEach(el => {
    el.addEventListener('click', () => {
      const cid = parseInt(el.dataset.chunk);
      const block = evidenceContainer.querySelector(`.chunk-block[data-chunk-id="${cid}"]`);
      if (block) {
        block.scrollIntoView({ behavior: 'smooth', block: 'center' });
        block.classList.add('expanded');
        block.style.outline = '2px solid #FFB74D';
        setTimeout(() => block.style.outline = '', 2000);
      } else {
        viewRawChunk(cid);
      }
    });
  });

  // Wire copy buttons
  daemonDetail.querySelectorAll('.cmd-copy').forEach(btn => {
    btn.addEventListener('click', () => {
      navigator.clipboard?.writeText(btn.dataset.cmd).catch(() => {});
      btn.textContent = 'Copied!';
      btn.classList.add('copied');
      setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
    });
  });
}

function buildChunkBlock(r) {
  const colors = relColor(r.relevance_score);
  const block = document.createElement('div');
  block.className = 'chunk-block';
  block.dataset.chunkId = r.chunk_id;

  // Header bar
  const header = document.createElement('div');
  header.className = 'chunk-header-bar';
  header.style.cssText = `background:${colors.bg};color:${colors.text};`;

  const badge = document.createElement('span');
  badge.className = 'chunk-domain-badge';
  badge.textContent = r.domain;

  const title = document.createElement('span');
  title.className = 'chunk-title';
  title.textContent = r.title;
  title.title = r.title;

  const score = document.createElement('span');
  score.className = 'chunk-score';
  score.textContent = `${Math.round(r.relevance_score * 100)}%`;

  const icon = document.createElement('span');
  icon.className = 'chunk-expand-icon';
  icon.textContent = '▼';

  header.appendChild(badge);
  header.appendChild(title);
  header.appendChild(score);
  header.appendChild(icon);

  // Body
  const body = document.createElement('div');
  body.className = 'chunk-body';

  // Health badges
  if (r.health_items?.length) {
    const badges = document.createElement('div');
    badges.className = 'health-badges';
    for (const h of r.health_items) {
      const badge = document.createElement('span');
      badge.className = `health-badge ${h.status.toLowerCase()}${h.heuristic ? ' heuristic' : ''}`;
      badge.title = h.heuristic ? 'Heuristic threshold' : 'Explicit status';
      badge.textContent = `${h.label}: ${h.value}${h.unit}`;
      badges.appendChild(badge);
    }
    body.appendChild(badges);
  }

  const pre = document.createElement('pre');
  pre.innerHTML = highlightElement(r.body_preview, state.element);
  body.appendChild(pre);

  // View raw link
  const rawLink = document.createElement('span');
  rawLink.className = 'raw-link';
  rawLink.textContent = '↗ View full chunk';
  rawLink.addEventListener('click', e => { e.stopPropagation(); viewRawChunk(r.chunk_id); });
  body.appendChild(rawLink);

  block.appendChild(header);
  block.appendChild(body);

  // Toggle expand
  header.addEventListener('click', () => {
    block.classList.toggle('expanded');
  });

  return block;
}

// ── Raw chunk modal ───────────────────────────────────────────────────────
async function viewRawChunk(chunkId) {
  if (!state.sessionId) return;
  try {
    const res = await fetch(`/api/sessions/${state.sessionId}/chunk/${chunkId}`);
    const data = await res.json();
    showRawModal(data);
  } catch (e) { alert('Failed to load chunk: ' + e.message); }
}

function showRawModal(data) {
  const existing = document.getElementById('raw-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'raw-modal';
  modal.style.cssText = `
    position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.85);
    z-index:1000;display:flex;align-items:center;justify-content:center;
  `;
  const inner = document.createElement('div');
  inner.style.cssText = `
    background:#1E1E2E;border:1px solid #3A3A5E;border-radius:8px;
    width:90%;max-height:90vh;overflow:auto;padding:20px;
  `;
  inner.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <strong style="color:#64B5F6;">${escHtml(data.title)}</strong>
      <button onclick="document.getElementById('raw-modal').remove()"
        style="background:#C62828;color:#fff;border:none;border-radius:4px;padding:4px 10px;cursor:pointer;">✕</button>
    </div>
    <div style="font-size:10px;color:#9090A8;margin-bottom:8px;">
      Domain: ${escHtml(data.domain)} | Source: ${escHtml(data.source_name)} | Lines: ${data.start_line}–${data.start_line + data.line_count}
    </div>
    <pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;color:#D0D0E8;line-height:1.5;">${escHtml(data.body)}</pre>
  `;
  modal.appendChild(inner);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

// ── Export ────────────────────────────────────────────────────────────────
document.querySelectorAll('.export-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    if (!state.sessionId || !state.element) return;
    const fmt = btn.dataset.fmt;
    try {
      const res = await fetch(`/api/sessions/${state.sessionId}/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          element: state.element,
          domain: state.domain || null,
          format: fmt,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const cd = res.headers.get('Content-Disposition') || '';
      const fnMatch = cd.match(/filename="([^"]+)"/);
      const filename = fnMatch ? fnMatch[1] : `export.${fmt}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { alert('Export failed: ' + e.message); }
  });
});

// ── Load more ──────────────────────────────────────────────────────────────
loadMoreBtn.addEventListener('click', () => {
  state.page++;
  doSearch(true);
});

// ── Helpers ───────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * HTML-escape text, then highlight occurrences of `element` with a yellow mark.
 * Uses a word-boundary lookahead so Eth1/1 won't highlight inside Eth1/10.
 */
function highlightElement(rawText, element) {
  const escaped = escHtml(rawText);
  if (!element) return escaped;
  const elemEsc = element.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`(${elemEsc})(?=[^0-9/.]|$)`, 'gi');
  return escaped.replace(re, '<mark class="elem-hl">$1</mark>');
}

function clearResults() {
  evidenceContainer.innerHTML = '<div id="empty-state"><div class="icon">&#x1F50D;</div><div>Searching…</div></div>';
  cardsContainer.style.display = 'none';
  cardsContainer.innerHTML = '';
  alertsContainer.style.display = 'none';
  alertsContainer.innerHTML = '';
  timelineContainer.style.display = 'none';
  timelineEvents.innerHTML = '';
  hideRelationships();
  hideDaemonHints();
  hideGraphPanels();
  loadMoreBtn.style.display = 'none';
  resultCountEl.textContent = '';
  exportWrap.style.display = 'none';
}

// ── Graph panels: Traffic Context + Policies ───────────────────────────────

function renderGraphPanels(graphData) {
  const tc = graphData.traffic_context;
  const pol = graphData.policies;

  // ── Traffic Context ──────────────────────────────────────────────────────
  const trafficPanel = $('traffic-context');
  const chainEl = $('traffic-chain');
  const vrfEl = $('traffic-vrf');

  const chain = tc?.chain || [];
  const hasChain = chain.length > 1;  // more than just the element itself
  const hasNeighbors = tc?.neighbors?.length > 0;
  const hasPolicies = (pol?.acls?.length || pol?.pbr?.length || pol?.qos?.length);

  if (!hasChain && !hasNeighbors && !tc?.vrf && !hasPolicies) {
    hideGraphPanels();
    return;
  }

  if (hasChain || hasNeighbors) {
    // Build chain nodes
    chainEl.innerHTML = '';
    chain.forEach((step, i) => {
      const node = document.createElement('span');
      node.className = `graph-node graph-node-${step.node_type}`;
      node.textContent = step.name;
      node.title = step.node_type;
      // Make clickable for navigation
      node.style.cursor = 'pointer';
      node.addEventListener('click', () => {
        elementInput.value = step.name;
        doSearch();
      });
      chainEl.appendChild(node);

      if (i < chain.length - 1) {
        const arrow = document.createElement('span');
        arrow.className = 'graph-arrow';
        const relLabel = step.rel_to_next
          ? step.rel_to_next.replace(/_/g, ' ').toLowerCase()
          : '→';
        arrow.innerHTML = ` <span class="graph-rel-label">${escHtml(relLabel)}</span> &#x2192; `;
        chainEl.appendChild(arrow);
      }
    });

    // VRF info block
    vrfEl.innerHTML = '';
    if (tc.vrf) {
      const vrf = tc.vrf;
      let html = `<div class="vrf-info-block">`;
      html += `<span class="vrf-name-badge">VRF: ${escHtml(vrf.name)}</span>`;
      if (vrf.protocols?.length) {
        html += ` <span class="vrf-proto-badge">${vrf.protocols.join(', ')}</span>`;
      }
      if (vrf.leaks_to?.length) {
        html += `<div class="vrf-leaks">&#x2192; leaks into: ${vrf.leaks_to.map(v => `<span class="vrf-leak-target" title="click to view" onclick="elementInput.value='${escHtml(v)}';doSearch();">${escHtml(v)}</span>`).join(', ')}</div>`;
      }
      if (vrf.leaks_from?.length) {
        html += `<div class="vrf-leaks">&#x2190; receives leaks from: ${vrf.leaks_from.map(v => `<span class="vrf-leak-target">${escHtml(v)}</span>`).join(', ')}</div>`;
      }
      if (vrf.exports_rt?.length) {
        html += `<div class="vrf-rt">export RT: ${vrf.exports_rt.map(r => `<code>${escHtml(r)}</code>`).join(', ')}</div>`;
      }
      if (vrf.imports_rt?.length) {
        html += `<div class="vrf-rt">import RT: ${vrf.imports_rt.map(r => `<code>${escHtml(r)}</code>`).join(', ')}</div>`;
      }
      html += `</div>`;
      vrfEl.innerHTML = html;
    }

    // Neighbors (e.g. interfaces running OSPF when user searched "ospf")
    const neighborsEl = $('traffic-neighbors');
    if (neighborsEl) {
      const allNeighbors = tc.neighbors || [];
      const startType = chain[0]?.node_type;
      const startName = chain[0]?.name || '';
      const ifaceTypes = new Set(['INTERFACE','PORT_CHANNEL','LOOPBACK','VLAN']);
      const ifaceNeighbors = allNeighbors.filter(n => ifaceTypes.has(n.node_type));
      const otherNeighbors = allNeighbors.filter(n => !ifaceTypes.has(n.node_type) && n.node_type !== 'PROTOCOL');

      let html = '';
      if (ifaceNeighbors.length) {
        const label = startType === 'PROTOCOL'
          ? `Interfaces / ports running ${escHtml(startName)}:`
          : 'Related interfaces:';
        html += `<div class="vrf-info-block"><span class="vrf-name-badge">${label}</span> ` +
          ifaceNeighbors.map(n =>
            `<span class="graph-node graph-node-${n.node_type}" style="cursor:pointer"
              title="Click to search ${escHtml(n.node_type)}: ${escHtml(n.name)}"
              onclick="elementInput.value='${escHtml(n.name)}';doSearch();">${escHtml(n.name)}</span>`
          ).join(' ') + '</div>';
      }
      if (otherNeighbors.length) {
        html += `<div class="vrf-info-block"><span class="vrf-name-badge">Also related:</span> ` +
          otherNeighbors.map(n =>
            `<span class="graph-node graph-node-${n.node_type}" style="cursor:pointer"
              title="${escHtml(n.node_type)}: ${escHtml(n.name)}"
              onclick="elementInput.value='${escHtml(n.name)}';doSearch();">${escHtml(n.name)}</span>`
          ).join(' ') + '</div>';
      }
      neighborsEl.innerHTML = html;
    }

    trafficPanel.style.display = 'block';
  } else {
    trafficPanel.style.display = 'none';
  }

  // ── Policies ─────────────────────────────────────────────────────────────
  const policiesPanel = $('policies-panel');
  const policiesContent = $('policies-content');

  if (hasPolicies) {
    let html = '';

    if (pol.acls?.length) {
      html += '<div class="policy-section"><div class="policy-section-title">&#x1F6E1; ACLs</div>';
      for (const acl of pol.acls) {
        const dirColor = acl.direction === 'IN' ? '#1565C0' : '#0D47A1';
        html += `<div class="policy-row">
          <span class="policy-dir-badge" style="background:${dirColor};">${acl.direction}</span>
          <span class="policy-name">${escHtml(acl.name)}</span>
          ${acl.uses_prefix_lists?.length ? `<span class="policy-detail">prefix-lists: ${acl.uses_prefix_lists.map(p => escHtml(p)).join(', ')}</span>` : ''}
        </div>`;
      }
      html += '</div>';
    }

    if (pol.pbr?.length) {
      html += '<div class="policy-section"><div class="policy-section-title">&#x1F500; PBR (Policy-Based Routing)</div>';
      for (const p of pol.pbr) {
        html += `<div class="policy-row">
          <span class="policy-name">route-map ${escHtml(p.route_map)}</span>
          ${p.next_hops?.length ? `<span class="policy-detail">next-hop: <code>${p.next_hops.map(h => escHtml(h)).join(', ')}</code></span>` : ''}
          ${p.uses_acls?.length ? `<span class="policy-detail">match ACL: ${p.uses_acls.map(a => escHtml(a)).join(', ')}</span>` : ''}
          ${p.uses_prefix_lists?.length ? `<span class="policy-detail">match prefix-list: ${p.uses_prefix_lists.map(a => escHtml(a)).join(', ')}</span>` : ''}
        </div>`;
      }
      html += '</div>';
    }

    if (pol.qos?.length) {
      html += '<div class="policy-section"><div class="policy-section-title">&#x23F2; QoS</div>';
      for (const q of pol.qos) {
        const dirColor = q.direction === 'IN' ? '#2E7D32' : '#1B5E20';
        html += `<div class="policy-row">
          <span class="policy-dir-badge" style="background:${dirColor};">${q.direction}</span>
          <span class="policy-name">${escHtml(q.policy_map)}</span>
        </div>`;
      }
      html += '</div>';
    }

    policiesContent.innerHTML = html;
    policiesPanel.style.display = 'block';
  } else {
    policiesPanel.style.display = 'none';
  }
}

function hideGraphPanels() {
  $('traffic-context').style.display = 'none';
  $('policies-panel').style.display = 'none';
}

// ── Session delete / pop ──────────────────────────────────────────────────

$('pop-session-btn').addEventListener('click', async () => {
  const res = await fetch('/api/sessions', { method: 'DELETE' });
  if (res.ok) {
    const d = await res.json();
    const label = d.original_filename || d.deleted;
    showToast(`Popped: ${label}`);
    if (state.sessionId === d.deleted) {
      state.sessionId = null;
      clearResults();
      updateSearchBtn();
    }
    loadSessions();
  } else {
    showToast('No sessions to delete', 'warn');
  }
});

$('del-session-btn').addEventListener('click', async () => {
  if (!state.sessionId) { showToast('No session selected', 'warn'); return; }
  const res = await fetch(`/api/sessions/${state.sessionId}`, { method: 'DELETE' });
  if (res.ok) {
    showToast('Session deleted');
    state.sessionId = null;
    clearResults();
    updateSearchBtn();
    loadSessions();
  } else {
    showToast('Delete failed', 'warn');
  }
});

function showToast(msg, type = 'info') {
  const t = document.createElement('div');
  t.style.cssText = `
    position:fixed;bottom:24px;right:24px;z-index:9999;
    padding:10px 16px;border-radius:6px;font-size:12px;
    background:${type === 'warn' ? '#7B2020' : '#1B5E20'};
    color:#fff;box-shadow:0 2px 8px rgba(0,0,0,0.5);
    transition:opacity 0.4s;
  `;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 400); }, 2500);
}

// ── Init ──────────────────────────────────────────────────────────────────
loadSessions();

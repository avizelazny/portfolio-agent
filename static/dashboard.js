// ── Ticker display names (Hebrew) ─────────────────────────────────────────────
const TICKER_NAMES = {
  '5136544': 'מיטב כספית',
  '5130661': 'הראל מחקה ת"א 35',
  '5142088': 'קסם KTF ביטחוניות',
  '5109418': 'תכלית TTF ת"א 35',
  '5134556': 'תכלית TTF Semiconductor',
  '5141882': 'תכלית TTF ביטחוניות',
  '1235985': 'תכלית סל ביטחוניות',
  '1148907': 'הראל סל ת"א 35',
};

// ── Helpers ──────────────────────────────────────────────────────────────────
function formatPrice(ticker, price) {
  // Numeric-only TASE IDs are funds — prices stored in shekels but displayed
  // in agorot (×100) to match Bank Discount portal convention for fund units.
  const isFund = /^[0-9]+$/.test(ticker);
  const val = parseFloat(price);
  if (isFund) return (val * 100).toFixed(2);
  return '\u20aa' + val.toFixed(2);
}

// ── State ────────────────────────────────────────────────────────────────────
let pollInterval    = null;
let currentRecs     = [];   // currently displayed recs — used by aiAnalyze() lookup
let allBatches      = [];   // last known batch list from /api/batches
let _allDbRecs      = [];   // full DB rec list (with created_at) for batch switching
let selectedBatch   = null; // currently selected batch_ts (null = no filter)

// ── Reset dashboard ───────────────────────────────────────────────────────────
async function resetDashboard(clearDb) {
  const msg = clearDb
    ? 'Reset dashboard AND delete ALL recommendations from DB? This cannot be undone.'
    : 'Reset dashboard display?';
  if (!confirm(msg)) return;
  const res = await fetch('/api/reset', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({clear_db: clearDb})
  });
  const data = await res.json();
  if (clearDb && data.db_cleared) addLog('DB cleared — all recommendations deleted', 'warn');
  if (clearDb && !data.db_cleared) addLog('Dashboard reset (DB offline or error)', 'warn');
  location.reload();
}

// ── File type detection (mirrors server-side detect_file_type) ────────────────
// detectedFiles holds File objects keyed by detected type after SheetJS scan.
var detectedFiles = {portfolio: null, history: null, orders: null, unknown: []};

function detectFileTypeFromHeaders(headers) {
  // Mirror Python detect_file_type() logic.
  // Signatures use columns unique to each file type — order matters:
  // orders must be checked before history because orders also contains
  // סוג פעולה and שער ביצוע.
  var cols = headers.map(function(h) { return String(h).trim(); });
  // Orders (הוראות): unique column הגבלת שער or סטטוס
  if (cols.some(function(c) { return c.includes('\u05d4\u05d2\u05d1\u05dc\u05ea \u05e9\u05e2\u05e8') || c === '\u05e1\u05d8\u05d8\u05d5\u05e1'; })) {
    return 'orders';
  }
  // History (ביצועים): unique column כמות מבוצעת
  if (cols.some(function(c) { return c.includes('\u05db\u05de\u05d5\u05ea \u05de\u05d1\u05d5\u05e6\u05e2\u05ea'); })) {
    return 'history';
  }
  // Portfolio (התיק שלי): unique column אחוז נייר מהתיק or שינוי יומי
  if (cols.some(function(c) { return c.includes('\u05d0\u05d7\u05d5\u05d6 \u05e0\u05d9\u05d9\u05e8') || c.includes('\u05e9\u05d9\u05e0\u05d5\u05d9 \u05d9\u05d5\u05de\u05d9'); })) {
    return 'portfolio';
  }
  return 'unknown';
}

function renderDetectionPreview() {
  var preview = document.getElementById('detectionPreview');
  if (!preview) return;
  var lines = [];
  if (detectedFiles.portfolio) lines.push('<div class="det-ok">&#10003; portfolio \u2014 ' + detectedFiles.portfolio.name + '</div>');
  if (detectedFiles.history)   lines.push('<div class="det-ok">&#10003; history \u2014 ' + detectedFiles.history.name + '</div>');
  if (detectedFiles.orders)    lines.push('<div class="det-ok">&#10003; orders \u2014 ' + detectedFiles.orders.name + '</div>');
  detectedFiles.unknown.forEach(function(f) {
    lines.push('<div class="det-warn">&#9888; unknown \u2014 ' + f.name + ' (skipped)</div>');
  });
  preview.innerHTML = lines.join('');
  preview.style.display = lines.length > 0 ? 'block' : 'none';
}

function readFileHeadersWithSheetJS(file) {
  // Returns a Promise that resolves with the detected file type string.
  return new Promise(function(resolve) {
    if (typeof XLSX === 'undefined') { resolve('unknown'); return; }
    var reader = new FileReader();
    reader.onload = function(e) {
      try {
        var data = new Uint8Array(e.target.result);
        var wb = XLSX.read(data, {type: 'array', sheetRows: 15});
        var ws = wb.Sheets[wb.SheetNames[0]];
        var rows = XLSX.utils.sheet_to_json(ws, {header: 1, defval: ''});
        // Bank Discount files have title rows before actual column headers.
        // Scan all rows: run detection on each row with 3+ cells, return first
        // non-'unknown' hit. This skips title/account rows that don't match
        // any signature even if they have enough non-empty cells.
        for (var i = 0; i < rows.length; i++) {
          var headers = rows[i].filter(function(c) { return String(c).trim() !== ''; });
          if (headers.length >= 3) {
            var type = detectFileTypeFromHeaders(headers);
            if (type !== 'unknown') {
              resolve(type);
              return;
            }
          }
        }
        resolve('unknown');
      } catch(err) { resolve('unknown'); }
    };
    reader.onerror = function() { resolve('unknown'); };
    reader.readAsArrayBuffer(file);
  });
}

// ── Upload & run ─────────────────────────────────────────────────────────────
async function uploadAndRun() {
  if (!detectedFiles.portfolio) {
    alert('No portfolio file detected. Please select the \u05d4\u05ea\u05d9\u05e7 \u05e9\u05dc\u05d9 Excel export.');
    return;
  }

  const uploadBtn = document.getElementById('uploadBtn');
  uploadBtn.disabled = true;
  document.getElementById('statusDot').className = 'status-dot running';

  const form = new FormData();
  form.append('files', detectedFiles.portfolio);
  if (detectedFiles.history) form.append('files', detectedFiles.history);
  if (detectedFiles.orders)  form.append('files', detectedFiles.orders);

  try {
    const res = await fetch('/api/upload', {method: 'POST', body: form});
    const data = await res.json();
    if (data.started) {
      addLog('Files uploaded \u2014 pipeline started...', 'info');
      startPolling();
    } else {
      addLog('Upload failed: ' + (data.reason || 'unknown error'), 'err');
      uploadBtn.disabled = false;
      document.getElementById('statusDot').className = 'status-dot offline';
    }
  } catch(e) {
    addLog('Upload error: ' + e.message, 'err');
    uploadBtn.disabled = false;
    document.getElementById('statusDot').className = 'status-dot offline';
  }
}

// ── Poll for updates ──────────────────────────────────────────────────────────
function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(fetchState, 1500);
}

async function fetchState() {
  try {
    const res = await fetch('/api/state');
    const data = await res.json();
    updateUI(data);
    if (!data.is_running) {
      clearInterval(pollInterval);
      pollInterval = null;
      const upBtn = document.getElementById('uploadBtn');
      if (detectedFiles.portfolio) { upBtn.disabled = false; upBtn.onclick = uploadAndRun; }
      document.getElementById('statusDot').className = data.report ? 'status-dot' : 'status-dot offline';
      fetchTrackRecord();
      if (data.report) fetchBatches();
    }
  } catch(e) {}
}

// ── Update UI from state ──────────────────────────────────────────────────────
function updateUI(data) {
  // Log lines
  if (data.run_log && data.run_log.length) {
    const wrap = document.getElementById('logWrap');
    const existing = wrap.querySelectorAll('.log-line').length;
    if (data.run_log.length > existing - 1) {
      wrap.innerHTML = '';
      data.run_log.forEach(l => {
        const cls = l.type || 'info';
        wrap.innerHTML += `<div class="log-line"><span class="log-time">${l.time}</span><span class="log-msg ${cls}">${l.msg}</span></div>`;
      });
      wrap.scrollTop = wrap.scrollHeight;
    }
  }

  // Portfolio stats
  if (data.portfolio) {
    const p = data.portfolio;
    document.getElementById('statTotal').textContent = '₪' + fmt(p.total_value_ils);
    document.getElementById('statCash').textContent = 'cash: ₪' + fmt(p.cash_ils);
    const pnlEl = document.getElementById('statDayPnl');
    pnlEl.textContent = (p.day_pnl_ils >= 0 ? '+' : '') + '₪' + fmt(p.day_pnl_ils);
    pnlEl.className = 'stat-value ' + (p.day_pnl_ils >= 0 ? 'green' : 'red');
    document.getElementById('statDayPct').textContent = (p.day_pnl_pct >= 0 ? '+' : '') + parseFloat(p.day_pnl_pct).toFixed(2) + '%';
    document.getElementById('statHoldings').textContent = p.holdings.length;
    document.getElementById('holdingsBadge').textContent = p.holdings.length + ' positions';
    renderHoldings(p.holdings);
  }

  // Macro
  if (data.macro) {
    const m = data.macro;
    document.getElementById('m1').textContent = (m.boi_interest_rate || '—') + '%';
    document.getElementById('m2').textContent = m.usd_ils_rate || '—';
    document.getElementById('m3').textContent = (m.cpi_annual_pct || '—') + '%';
    document.getElementById('statUsd').textContent = m.usd_ils_rate || '—';
    document.getElementById('statBoi').textContent = m.boi_interest_rate || '—';
    document.getElementById('statIndex').textContent = m.ta35_close ? 'TA-35: ' + parseFloat(m.ta35_close).toFixed(2) : '—';
    document.getElementById('statIndex2').textContent = m.ta125_close ? 'TA-125: ' + parseFloat(m.ta125_close).toFixed(2) : '—';
  }

  // Signals
  if (data.signals && data.signals.length) {
    renderSignals(data.signals);
    document.getElementById('signalsBadge').textContent = (data.signals ? data.signals.length : 0) + ' tickers';
  }

  // Report
  if (data.report) {
    const r = data.report;
    document.getElementById('statRecs').textContent = r.recommendations.length;
    const buys = r.recommendations.filter(x=>x.action==='BUY').length;
    const sells = r.recommendations.filter(x=>x.action==='SELL').length;
    document.getElementById('statRecSub').textContent = buys + ' buys, ' + sells + ' sells';
    document.getElementById('recsBadge').textContent = r.recommendations.length + ' total';
    document.getElementById('marketSummary').textContent = r.market_summary;
    renderRecs(r.recommendations);
    renderFlags(r.portfolio_risk_flags);
    document.getElementById('lastRunLabel').textContent = 'Last: ' + (data.last_run || '—');
    document.getElementById('statusDot').className = 'status-dot';
  }
}

function fmt(n) {
  return parseFloat(n).toLocaleString('en-IL', {maximumFractionDigits:0});
}

function renderHoldings(holdings) {
  const tbody = document.getElementById('holdingsBody');
  const maxWeight = Math.max(...holdings.map(h => parseFloat(h.weight_pct)));
  tbody.innerHTML = holdings.map(h => {
    const pnl = parseFloat(h.unrealized_pnl_pct);
    const pnlCls = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    const pnlSign = pnl >= 0 ? '+' : '';
    const weight = parseFloat(h.weight_pct);
    const barWidth = maxWeight > 0 ? Math.round((weight / maxWeight) * 100) : 0;
    return `<tr>
      <td title="${TICKER_NAMES[h.ticker] || h.company_name}"><div class="ticker">${(TICKER_NAMES[h.ticker] || h.company_name).substring(0,12)}\u2026</div><div class="name" style="font-size:.68rem;color:var(--muted)">${h.ticker}</div></td>
      <td style="font-family:\"DM Mono\",monospace;font-size:.8rem">${parseFloat(h.quantity).toLocaleString()}</td>
      <td style="font-family:\"DM Mono\",monospace;font-size:.8rem">${formatPrice(h.ticker, h.avg_cost_ils)}</td>
      <td style="font-family:\"DM Mono\",monospace;font-size:.8rem">${formatPrice(h.ticker, h.current_price)}</td>
      <td><span class="${pnlCls}">${pnlSign}${pnl.toFixed(1)}%</span></td>
      <td style="min-width:70px">
        <div style="font-size:.72rem;color:var(--muted);font-family:\"DM Mono\",monospace">${weight.toFixed(1)}%</div>
        <div class="weight-bar"><div class="weight-fill" style="width:${barWidth}%"></div></div>
      </td>
    </tr>`;
  }).join('');
}

function renderSignals(signals) {
  const sorted = [...signals].sort((a,b) => (b.composite_score||0)-(a.composite_score||0));
  const top = sorted.slice(0,12);
  document.getElementById('signalsBody').innerHTML = top.map(s => {
    const score = s.composite_score || 0;
    const pct = Math.min(Math.abs(score) * 100, 100);
    const color = score > 0.3 ? 'var(--green)' : score < -0.3 ? 'var(--red)' : 'var(--warn)';
    const scoreStr = (score >= 0 ? '+' : '') + score.toFixed(3);
    const scoreColor = score > 0 ? 'var(--green)' : 'var(--red)';
    const flags = (s.signal_summary || []).join(' · ') || 'no flags';
    return `<div class="signal-row">
      <span class="signal-ticker">${s.ticker}</span>
      <div class="signal-bar-wrap"><div class="signal-bar" style="width:${pct}%;background:${color}"></div></div>
      <span class="signal-score" style="color:${scoreColor}">${scoreStr}</span>
      <span class="signal-flags">${flags}</span>
    </div>`;
  }).join('');
}

// ── Batch selector ────────────────────────────────────────────────────────────

function dbRecToDisplayRec(r) {
  // Map /api/recommendations DB fields to the format renderRecs() expects.
  return {
    rec_id:               r.id,
    ticker:               r.symbol,
    action:               r.action,
    conviction:           r.conviction,
    thesis:               r.thesis || '',
    key_risk:             r.key_risk || '',
    status:               r.status,
    price_target_ils:     r.price_target || null,
    suggested_position_pct: 0,
    supporting_signals:   [],
    created_at:           r.created_at,
  };
}

async function applyBatchFilter(batch_ts) {
  try {
    const all = await fetch('/api/recommendations').then(r => r.json());
    if (!Array.isArray(all)) return;
    _allDbRecs = all.map(dbRecToDisplayRec);
    currentRecs = [];  // reset cache so renderRecs re-renders
    renderRecs(_allDbRecs);
  } catch(e) {}
}

async function fetchBatches() {
  try {
    const sel = document.getElementById('batchSelect');
    // Only populate once — skip if already loaded to avoid resetting user's selection
    if (sel && sel.options.length > 0) return;
    const batches = await fetch('/api/batches').then(r => r.json());
    if (!Array.isArray(batches) || batches.length === 0) return;
    allBatches = batches;
    if (!sel) return;
    sel.innerHTML = batches.map(b =>
      `<option value="${b.batch_ts}">${b.label}</option>`
    ).join('');
    sel.style.display = '';
    // Auto-select most recent batch and render its recs
    sel.value = batches[0].batch_ts;
    selectedBatch = batches[0].batch_ts;
    await applyBatchFilter(batches[0].batch_ts);
    sel.onchange = function() {
      selectedBatch = this.value;
      currentRecs = [];  // force re-render with new filter
      renderRecs(_allDbRecs);
    };
  } catch(e) {}
}

function renderRecs(recs) {
  // When a batch is active, always render from _allDbRecs (guaranteed created_at),
  // ignoring whatever dataset the caller passed in. This means polling can never
  // overwrite a batch selection — selectedBatch is the authoritative filter.
  const source = (selectedBatch && _allDbRecs.length) ? _allDbRecs : recs;
  const displayRecs = selectedBatch
    ? source.filter(r => r.created_at && r.created_at.slice(0, 16) === selectedBatch)
    : source;

  // Skip re-render if displayed content unchanged — prevents closing open eval/AI panels
  if (JSON.stringify(displayRecs) === JSON.stringify(currentRecs)) return;
  currentRecs = displayRecs;

  document.getElementById('recsBadge').textContent = displayRecs.length + ' total';

  const convColors = {HIGH:'var(--green)',MEDIUM:'var(--warn)',LOW:'var(--muted)'};
  const hasDb = displayRecs.some(r => r.rec_id);
  const dbNote = hasDb ? '' :
    '<div style="padding:8px 20px 0;font-size:.72rem;color:var(--muted);font-family:&quot;DM Mono&quot;,monospace">' +
    '&#9888; DB offline — approve/reject unavailable</div>';
  document.getElementById('recsBody').innerHTML = dbNote + displayRecs.map((r, idx) => {
    const uid = r.rec_id ? String(r.rec_id) : 'r' + idx;
    const sugPrice = r.price_target_ils ? parseFloat(r.price_target_ils) : 0;
    const isPending = !r.status || r.status === 'pending';

    // ── Row 1: action pill + status + rec_id + name + conviction
    const statusBadge = !r.status || r.status === 'pending'
      ? (r.rec_id ? '<span class="status-badge pending">&#9679; Pending</span>' : '')
      : r.status === 'approved'
      ? '<span class="status-badge approved">&#10003; Approved</span>'
      : r.status === 'rejected'
      ? '<span class="status-badge rejected">&#10007; Rejected</span>'
      : '<span class="status-badge superseded">&#8212; Superseded</span>';
    const convColor = convColors[r.conviction] || 'var(--muted)';
    const recIdBadge = r.rec_id ? `<span class="rec-id-badge">#${r.rec_id}</span>` : '';
    const nameHtml = `<span class="rec-ticker">${TICKER_NAMES[r.ticker] || r.ticker}</span>`
      + (TICKER_NAMES[r.ticker] ? `<span class="rec-sub-ticker">(${r.ticker})</span>` : '');
    const convBadge = `<span class="conv-badge" style="color:${convColor};border-color:${convColor}">${r.conviction}</span>`;

    // ── Row 2: meta chips — target, position, signals
    const metaChips = [
      r.price_target_ils ? `<span class="meta-chip">&#127919; &#8362;${parseFloat(r.price_target_ils).toLocaleString('en-IL',{maximumFractionDigits:2})}</span>` : '',
      r.suggested_position_pct > 0 ? `<span class="meta-chip">&#128200; ${r.suggested_position_pct}% portfolio</span>` : '',
      ...(r.supporting_signals||[]).map(s=>`<span class="rec-tag">${s}</span>`),
    ].filter(Boolean).join('');

    // ── Shared note textarea + Approve / Reject bar (pending only)
    const actionsHtml = r.rec_id && isPending ? `
      <textarea id="note-${r.rec_id}" class="rec-note" placeholder="Add a note (optional — applies to approve or reject)..." rows="2"></textarea>
      <div class="rec-actions" id="rec-actions-${r.rec_id}">
        <button class="btn-approve" data-rec-id="${r.rec_id}" data-price="${sugPrice}">&#10003; Approve</button>
        <button class="btn-reject" data-rec-id="${r.rec_id}">&#10007; Reject</button>
      </div>` : '';

    // ── Eval panel (collapsible)
    const evalApproveReject = r.rec_id && isPending
      ? `<button class="eval-approve" onclick="approveFromEval(${r.rec_id},'${uid}',${sugPrice})">&#10003; Approve</button><button class="eval-reject" onclick="rejectFromEval(${r.rec_id},'${uid}')">&#10007; Reject</button>`
      : `<span style="font-size:.7rem;color:var(--muted)">${r.rec_id ? 'Already decided' : 'Run pipeline to enable'}</span>`;
    const evalPanel = `
      <div id="eval-${uid}" class="eval-panel">
        <div class="eval-section-title">&#128203; 6-Step Evaluation</div>
        <div class="eval-row"><span>Signal Strength</span><div class="stars" data-uid="${uid}" data-field="signal"><span class="star" data-val="1">&#9733;</span><span class="star" data-val="2">&#9733;</span><span class="star" data-val="3">&#9733;</span><span class="star" data-val="4">&#9733;</span><span class="star" data-val="5">&#9733;</span></div></div>
        <div class="eval-row"><span>Thesis Quality</span><div class="stars" data-uid="${uid}" data-field="thesis"><span class="star" data-val="1">&#9733;</span><span class="star" data-val="2">&#9733;</span><span class="star" data-val="3">&#9733;</span><span class="star" data-val="4">&#9733;</span><span class="star" data-val="5">&#9733;</span></div></div>
        <div class="eval-row"><span>Portfolio Fit</span><div class="stars" data-uid="${uid}" data-field="fit"><span class="star" data-val="1">&#9733;</span><span class="star" data-val="2">&#9733;</span><span class="star" data-val="3">&#9733;</span><span class="star" data-val="4">&#9733;</span><span class="star" data-val="5">&#9733;</span></div></div>
        <div class="eval-row"><span>Risk Assessment</span><div class="stars" data-uid="${uid}" data-field="risk"><span class="star" data-val="1">&#9733;</span><span class="star" data-val="2">&#9733;</span><span class="star" data-val="3">&#9733;</span><span class="star" data-val="4">&#9733;</span><span class="star" data-val="5">&#9733;</span></div></div>
        <textarea id="note-${uid}" placeholder="Your reasoning (saved with approve/reject)..." rows="2"></textarea>
        <div class="eval-actions">${evalApproveReject}</div>
      </div>`;

    return `<div class="rec-card">
      <div class="rec-header">
        <div class="rec-header-left">
          <span class="action-pill pill-${r.action}">${r.action}</span>
          ${statusBadge}
          ${recIdBadge}
          ${nameHtml}
        </div>
        <div class="rec-header-right">${convBadge}</div>
      </div>
      ${metaChips ? `<div class="rec-meta">${metaChips}</div>` : ''}
      <div class="rec-thesis">${r.thesis}</div>
      <div class="rec-risk"><strong style="color:var(--warn)">&#9888; Risk:</strong> ${r.key_risk}</div>
      ${r.price_actual ? `<div class="rec-executed">&#10003; Executed @ &#8362;${parseFloat(r.price_actual).toLocaleString('he-IL')} &times; ${parseInt(r.qty_actual)} units &middot; ${r.executed_at}</div>` : ''}
      ${actionsHtml}
      <div class="rec-card-btns">
        <button class="eval-btn" id="eval-btn-${uid}" onclick="toggleEval('${uid}')">&#128203; Evaluate <span class="btn-chevron">&#9656;</span></button>
        <button class="ai-btn" id="ai-btn-${uid}" onclick="aiAnalyze('${uid}')">&#129302; AI Analysis <span class="btn-chevron">&#9656;</span></button>
      </div>
      ${evalPanel}
      <div id="ai-${uid}" class="ai-panel"></div>
    </div>`;
  }).join('');
}

function _updateRecStatus(recId, status) {
  // Update a single rec's status in cache and force re-render
  const idx = currentRecs.findIndex(r => r.rec_id === recId);
  if (idx < 0) return;
  const updated = currentRecs.map((r, i) => i === idx ? Object.assign({}, r, {status}) : r);
  currentRecs = [];  // reset cache so renderRecs doesn't skip
  renderRecs(updated);
}

function toggleEval(uid) {
  const panel = document.getElementById('eval-' + uid);
  const btn = document.getElementById('eval-btn-' + uid);
  if (!panel) return;
  const opening = panel.style.display !== 'block';
  panel.style.display = opening ? 'block' : 'none';
  if (btn) btn.classList.toggle('btn-active', opening);
}

async function aiAnalyze(uid) {
  const idx = uid.startsWith('r') ? parseInt(uid.slice(1)) : -1;
  const rec = idx >= 0 ? currentRecs[idx] : currentRecs.find(r => String(r.rec_id) === uid);
  if (!rec) { addLog('AI Analysis: rec not found', 'err'); return; }

  const panel = document.getElementById('ai-' + uid);
  const btn = document.getElementById('ai-btn-' + uid);

  // Toggle: collapse if already showing
  if (panel.style.display === 'block' && panel.innerHTML.trim()) {
    panel.style.display = 'none';
    if (btn) btn.classList.remove('btn-active');
    return;
  }

  panel.style.display = 'block';
  if (btn) btn.classList.add('btn-active');
  panel.innerHTML = '<div class="ai-loading">Analyzing with Claude Sonnet...</div>';

  try {
    const res = await fetch('/api/ai-analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rec}),
    });
    const data = await res.json();
    if (data.ok) {
      panel.innerHTML = '<div class="ai-result">' + data.text.replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>') + '</div>';
    } else {
      panel.innerHTML = '<div class="ai-error">Analysis failed: ' + data.error + '</div>';
    }
  } catch(e) {
    panel.innerHTML = '<div class="ai-error">Analysis failed: ' + e.message + '</div>';
  }
}

async function approveFromEval(recId, uid, sugPrice) {
  const note = document.getElementById('note-' + uid)?.value || '';
  const priceStr = prompt('Enter actual trade price (suggested: ' + (sugPrice || 'n/a') + '):', sugPrice || '');
  if (priceStr === null) return;
  const qtyStr = prompt('Enter quantity (optional):', '');
  if (qtyStr === null) return;
  const actionsDiv = document.getElementById('rec-actions-' + recId);
  if (actionsDiv) actionsDiv.innerHTML = '<span class="btn-approved">✓ Approved</span>';
  try {
    const res = await fetch('/api/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rec_id: recId, approved: true, actual_price: priceStr ? parseFloat(priceStr) : null, quantity: qtyStr ? parseInt(qtyStr) : null, note: note || null}),
    });
    const data = await res.json();
    if (!data.ok) addLog('Approve failed: ' + data.reason, 'err');
    else { addLog('Approved rec #' + recId, 'ok'); _updateRecStatus(recId, 'approved'); }
  } catch(e) { addLog('Approve error: ' + e.message, 'err'); }
}

async function rejectFromEval(recId, uid) {
  const note = document.getElementById('note-' + uid)?.value || '';
  const finalNote = note || prompt('Reason for rejecting (optional):', '') || '';
  const actionsDiv = document.getElementById('rec-actions-' + recId);
  if (actionsDiv) actionsDiv.innerHTML = '<span class="btn-rejected">✕ Rejected</span>';
  try {
    const res = await fetch('/api/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rec_id: recId, approved: false, note: finalNote || null}),
    });
    const data = await res.json();
    if (!data.ok) addLog('Reject failed: ' + data.reason, 'err');
    else { addLog('Rejected rec #' + recId, 'warn'); _updateRecStatus(recId, 'rejected'); }
  } catch(e) { addLog('Reject error: ' + e.message, 'err'); }
}

async function approveRec(recId, suggestedPrice) {
  const priceStr = prompt('Enter actual trade price (suggested: ' + (suggestedPrice || 'n/a') + '):', suggestedPrice || '');
  if (priceStr === null) return;
  const qtyStr = prompt('Enter quantity (optional):', '');
  if (qtyStr === null) return;
  const note = document.getElementById('note-' + recId)?.value || '';

  const actionsDiv = document.getElementById('rec-actions-' + recId);
  if (actionsDiv) actionsDiv.innerHTML = '<span class="btn-approved">✓ Approved</span>';

  try {
    const res = await fetch('/api/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        rec_id: recId,
        approved: true,
        actual_price: priceStr ? parseFloat(priceStr) : null,
        quantity: qtyStr ? parseInt(qtyStr) : null,
        note: note || null,
      }),
    });
    const data = await res.json();
    if (!data.ok) addLog('Approve failed: ' + data.reason, 'err');
    else { addLog('Approved rec #' + recId, 'ok'); _updateRecStatus(recId, 'approved'); }
  } catch(e) {
    addLog('Approve error: ' + e.message, 'err');
  }
}

async function rejectRec(recId) {
  const note = document.getElementById('note-' + recId)?.value || '';

  const actionsDiv = document.getElementById('rec-actions-' + recId);
  if (actionsDiv) actionsDiv.innerHTML = '<span class="btn-rejected">✕ Rejected</span>';

  try {
    const res = await fetch('/api/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        rec_id: recId,
        approved: false,
        note: note || null,
      }),
    });
    const data = await res.json();
    if (!data.ok) addLog('Reject failed: ' + data.reason, 'err');
    else { addLog('Rejected rec #' + recId, 'warn'); _updateRecStatus(recId, 'rejected'); }
  } catch(e) {
    addLog('Reject error: ' + e.message, 'err');
  }
}

function renderFlags(flags) {
  if (!flags || !flags.length) {
    document.getElementById('flagsBody').innerHTML = '<div class="empty" style="padding:14px"><div class="icon">&#x2705;</div>No risk flags</div>';
    return;
  }
  document.getElementById('flagsBody').innerHTML = '<div class="flag-list">' + flags.map(f=>`<span class="flag-chip">&#x26A0; ${f}</span>`).join('') + '</div>';
}

function addLog(msg, type='info') {
  const wrap = document.getElementById('logWrap');
  const now = new Date().toTimeString().slice(0,8);
  wrap.innerHTML += `<div class="log-line"><span class="log-time">${now}</span><span class="log-msg ${type}">${msg}</span></div>`;
  wrap.scrollTop = wrap.scrollHeight;
}

// ── Track Record ──────────────────────────────────────────────────────────────
async function fetchTrackRecord() {
  try {
    const res = await fetch('/api/performance');
    const data = await res.json();
    if (!data.ok) return;

    const s = data.summary;
    const scorecard = data.scorecard || [];
    const hasHistory = s && s.total_closed > 0;
    const hasSnapshots = scorecard.length > 0;
    if (!hasHistory && !hasSnapshots) return;

    let html = '';

    if (hasHistory) {
      const hitRate = s.total_closed > 0 ? Math.round(s.wins / s.total_closed * 100) : 0;
      const avgReturn = s.avg_return_pct != null ? parseFloat(s.avg_return_pct).toFixed(1) : null;
      const avgAlpha  = s.avg_alpha_pct  != null ? parseFloat(s.avg_alpha_pct).toFixed(1)  : null;
      html += `<div class="track-grid">
        <div class="track-stat">
          <div class="val ${hitRate >= 50 ? 'track-pos' : 'track-neg'}">${hitRate}%</div>
          <div class="lbl">Hit Rate</div>
        </div>
        <div class="track-stat">
          <div class="val ${avgReturn != null && parseFloat(avgReturn) >= 0 ? 'track-pos' : 'track-neg'}">${avgReturn != null ? avgReturn + '%' : '—'}</div>
          <div class="lbl">Avg Return</div>
        </div>
        <div class="track-stat">
          <div class="val">${s.total_closed}</div>
          <div class="lbl">Closed Recs</div>
        </div>
        <div class="track-stat">
          <div class="val ${avgAlpha != null && parseFloat(avgAlpha) >= 0 ? 'track-pos' : 'track-neg'}">${avgAlpha != null ? avgAlpha + '%' : '—'}</div>
          <div class="lbl">Avg Alpha</div>
        </div>
      </div>
      <hr class="track-divider">
      <div class="track-breakdown">
        <div class="track-row"><span>HIGH conviction</span><span class="${s.high_conv_success >= 50 ? 'track-pos' : 'track-neg'}">${s.high_conv_success != null ? Math.round(s.high_conv_success) + '%' : '—'}</span></div>
        <div class="track-row"><span>MED conviction</span><span class="${s.med_conv_success >= 50 ? 'track-pos' : 'track-neg'}">${s.med_conv_success != null ? Math.round(s.med_conv_success) + '%' : '—'}</span></div>
        <div class="track-row"><span>Pending decisions</span><span>${s.pending || 0}</span></div>
        <div class="track-row"><span>Open positions</span><span>${s.open_approved || 0}</span></div>
      </div>`;
      document.getElementById('trackBadge').textContent = s.total_closed + ' closed';
    }

    if (hasSnapshots) {
      const horizonRows = scorecard.map(sc => {
        const hr = sc.hit_rate != null ? parseFloat(sc.hit_rate) : null;
        const ret = sc.avg_return != null ? parseFloat(sc.avg_return) : null;
        const alp = sc.avg_alpha != null ? parseFloat(sc.avg_alpha) : null;
        return `<div class="track-row">
          <span style="font-family:\"DM Mono\",monospace">${sc.snapshot_days}d</span>
          <span style="color:var(--muted)">${sc.total} calls</span>
          <span class="${hr != null && hr >= 50 ? 'track-pos' : 'track-neg'}">${hr != null ? Math.round(hr) + '%' : '—'}</span>
          <span class="${ret != null && ret >= 0 ? 'track-pos' : 'track-neg'}">${ret != null ? ret.toFixed(1) + '%' : '—'}</span>
          <span class="${alp != null && alp >= 0 ? 'track-pos' : 'track-neg'}">${alp != null ? alp.toFixed(1) + '% \u03b1' : '—'}</span>
        </div>`;
      }).join('');
      html += `<hr class="track-divider">
      <div class="track-breakdown">
        <div class="track-row" style="font-family:\"DM Mono\",monospace;font-size:.63rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:2px">
          <span>Horizon</span><span>Calls</span><span>Hit%</span><span>Return</span><span>Alpha</span>
        </div>
        ${horizonRows}
      </div>`;
      if (!hasHistory) document.getElementById('trackBadge').textContent = scorecard.reduce((a,s) => a + s.total, 0) + ' scored';
    }

    document.getElementById('trackBody').innerHTML = html;
  } catch(e) {}
}

// ── Quality Tracker ───────────────────────────────────────────────────────────
function hitRateClass(rate) {
  if (rate == null) return '';
  return rate >= 70 ? 'quality-green' : rate >= 50 ? 'quality-amber' : 'quality-red';
}

async function fetchQuality() {
  try {
    const res = await fetch('/api/quality');
    const data = await res.json();
    if (!data.ok) return;

    const o = data.overall || {};
    const byAction = data.by_action || [];
    const byBatch  = data.by_batch  || [];

    if (!o.total) return;

    const hr = o.hit_rate;
    const hrClass = hitRateClass(hr);

    // Overall stats row
    const overallHtml = `<div class="quality-overall">
      <div class="quality-stat">
        <div class="val ${hrClass}">${hr != null ? hr + '%' : '—'}</div>
        <div class="lbl">Hit Rate</div>
      </div>
      <div class="quality-stat">
        <div class="val ${o.avg_net_return != null && o.avg_net_return >= 0 ? 'quality-green' : 'quality-red'}">${o.avg_net_return != null ? (o.avg_net_return >= 0 ? '+' : '') + o.avg_net_return.toFixed(1) + '%' : '—'}</div>
        <div class="lbl">Avg Net</div>
      </div>
      <div class="quality-stat">
        <div class="val">${o.total}</div>
        <div class="lbl">Scored</div>
      </div>
    </div>`;

    // Per-action table
    const actionRows = byAction.map(a => {
      const rc = hitRateClass(a.hit_rate);
      return `<tr>
        <td>${a.action}</td>
        <td class="num">${a.total}</td>
        <td class="num ${rc}">${a.hit_rate != null ? a.hit_rate + '%' : '—'}</td>
        <td class="num ${a.avg_net_return != null && a.avg_net_return >= 0 ? 'quality-green' : 'quality-red'}">${a.avg_net_return != null ? (a.avg_net_return >= 0 ? '+' : '') + a.avg_net_return.toFixed(1) + '%' : '—'}</td>
      </tr>`;
    }).join('');

    const actionHtml = `<div class="quality-section">
      <div class="quality-section-title">By Action <span style="font-weight:400;color:var(--muted);font-size:.63rem;letter-spacing:0">— HOLD: ✅ flat/up | ❌ drops &gt;2% &nbsp;·&nbsp; WATCH: not scored</span></div>
      <table class="quality-table">
        <thead><tr><th>Action</th><th class="num">Total</th><th class="num">Hit%</th><th class="num">Net Ret</th></tr></thead>
        <tbody>${actionRows}</tbody>
      </table>
    </div>`;

    // Per-batch table
    const batchRows = byBatch.map(b => {
      const rc = hitRateClass(b.hit_rate);
      return `<tr>
        <td style="font-family:'DM Mono',monospace;font-size:.7rem">${b.batch}</td>
        <td class="num">${b.total}</td>
        <td class="num ${rc}">${b.hit_rate != null ? b.hit_rate + '%' : '—'}</td>
        <td class="num ${b.avg_net_return != null && b.avg_net_return >= 0 ? 'quality-green' : 'quality-red'}">${b.avg_net_return != null ? (b.avg_net_return >= 0 ? '+' : '') + b.avg_net_return.toFixed(1) + '%' : '—'}</td>
      </tr>`;
    }).join('');

    const batchHtml = byBatch.length ? `<div class="quality-section">
      <div class="quality-section-title">By Batch</div>
      <table class="quality-table">
        <thead><tr><th>Batch</th><th class="num">Recs</th><th class="num">Hit%</th><th class="num">Net Ret</th></tr></thead>
        <tbody>${batchRows}</tbody>
      </table>
    </div>` : '';

    const watchHtml = renderWatchStats(data.watch_stats);
    document.getElementById('qualityBody').innerHTML = overallHtml + actionHtml + batchHtml + watchHtml;
    document.getElementById('qualityBadge').textContent = o.total + ' scored';
  } catch(e) {}
}

function renderWatchStats(ws) {
  if (!ws || ws.total === 0) return '';
  const hitColor = ws.hit_rate >= 70 ? 'var(--green)' : ws.hit_rate >= 50 ? 'var(--warn)' : 'var(--red)';
  const recRows = (ws.recs || []).map(r => {
    const ret = r.current_return_pct != null ? r.current_return_pct : 0;
    const retStr = (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%';
    // Return color: red if price rose (missed opportunity), green if fell (right to watch)
    const retColor = ret > 0 ? 'var(--red)' : 'var(--green)';
    const isCorrect = r.direction_correct === 1;
    return `<div class="watch-row">
      <span class="watch-ticker">${r.symbol}</span>
      <span class="watch-return" style="color:${retColor}">${retStr}</span>
      <span class="watch-verdict" style="color:${isCorrect ? 'var(--green)' : 'var(--warn)'}">
        ${isCorrect ? '✅ Right to watch' : '⚠️ Missed opportunity'}
      </span>
    </div>`;
  }).join('');
  return `<div class="watch-section">
    <div class="watch-header">
      <span>&#128065; WATCH — Missed Opportunity Tracker</span>
      <span class="watch-rate" style="color:${hitColor}">${ws.correct}/${ws.total} right (${ws.hit_rate}%)</span>
    </div>
    <div class="watch-subheader">Scored as hypothetical BUY — ✅ right to watch if price fell &nbsp;|&nbsp; ⚠️ missed if price rose</div>
    ${recRows}
  </div>`;
}

// ── TA-35 Red Day DCA widget ──────────────────────────────────────────────────
async function fetchTA35() {
  try {
    const r = await fetch('/api/ta35');
    const d = await r.json();
    if (!d.ok) return;

    const changeEl = document.getElementById('statTA35Change');
    const signalEl = document.getElementById('statTA35Signal');
    const cardEl   = document.getElementById('ta35Card');
    if (!changeEl || !signalEl || !cardEl) return;

    const sign = d.change_pct >= 0 ? '+' : '';
    changeEl.textContent = sign + d.change_pct.toFixed(2) + '%';
    changeEl.title = `TA-35: ₪${d.current} | Session open: ₪${d.prev_close} | ${d.updated_at}`;

    if (d.is_red_day) {
      changeEl.className = 'stat-value red';
      cardEl.style.borderColor = 'rgba(239,68,68,.6)';
      cardEl.style.boxShadow   = '0 0 12px rgba(239,68,68,.25)';
      signalEl.innerHTML = '🔴 Deploy ₪10k → הראל מחקה';
      signalEl.style.color = 'var(--red)';
    } else if (d.is_green_day) {
      changeEl.className = 'stat-value green';
      cardEl.style.borderColor = '';
      cardEl.style.boxShadow   = '';
      signalEl.textContent = '🟢 Green day — wait';
      signalEl.style.color = 'var(--green)';
    } else {
      changeEl.className = 'stat-value';
      cardEl.style.borderColor = '';
      cardEl.style.boxShadow   = '';
      signalEl.textContent = '⚪ Flat — wait';
      signalEl.style.color = 'var(--muted)';
    }
  } catch(e) { /* silent */ }
}

// ── Pending Orders ────────────────────────────────────────────────────────────
async function fetchPendingOrders() {
  try {
    const r = await fetch('/api/pending_orders');
    const d = await r.json();
    const panel = document.getElementById('pendingOrdersPanel');
    if (!panel) return;

    if (!d.ok || !d.orders || d.orders.length === 0) {
      panel.style.display = 'none';
      return;
    }

    panel.style.display = 'block';
    const rows = d.orders.map(function(o) {
      const actionClass = o.action === 'BUY' ? 'order-buy' : 'order-sell';
      const price = typeof o.limit_price === 'number'
        ? o.limit_price.toLocaleString('he-IL', {minimumFractionDigits: 2, maximumFractionDigits: 2})
        : o.limit_price;
      return '<div class="order-row">' +
        '<span class="order-action ' + actionClass + '">' + o.action + '</span>' +
        '<span class="order-name">' + o.name + ' <span style="color:var(--muted);font-weight:400;font-size:.72rem">(' + o.security_id + ')</span></span>' +
        '<span class="order-detail">' + o.quantity + ' units @ \u20aa' + price + '</span>' +
        '<span class="order-date">since ' + o.placed_date + '</span>' +
        '</div>';
    }).join('');

    document.getElementById('pendingOrdersBody').innerHTML = rows;
    document.getElementById('pendingOrdersCount').textContent = d.count + ' open';
  } catch(e) {
    console.error('Pending orders fetch failed:', e);
  }
}

// ── Hit Rate panel ────────────────────────────────────────────────────────────
async function fetchHitRate() {
  try {
    const res = await fetch('/api/hitrate');
    const data = await res.json();
    if (!data.ok || !data.windows) return;

    const windows = data.windows;
    const hurdle  = data.hurdle_rate_pct != null ? data.hurdle_rate_pct.toFixed(1) : '10.0';

    const cards = windows.map(function(w) {
      const rate = w.total_hit_rate;
      const valColor = rate == null ? 'var(--muted)' : rate >= 50 ? 'var(--green)' : 'var(--red)';
      const valText  = rate != null ? Math.round(rate) + '%' : '—';
      const sub = w.total_scored > 0
        ? w.acted_scored + ' acted · ' + w.unacted_scored + ' unacted'
        : 'no data';
      return '<div class="hitrate-card">' +
        '<div class="hr-val" style="color:' + valColor + '">' + valText + '</div>' +
        '<div class="hr-lbl">' + w.label + '</div>' +
        '<div class="hr-sub">' + sub + '</div>' +
        '</div>';
    }).join('');

    document.getElementById('hitrateBody').innerHTML =
      '<div class="hitrate-grid">' + cards + '</div>';
    document.getElementById('hitrateBadge').textContent = 'hurdle ' + hurdle + '%';
  } catch(e) {}
}

// ── Initial state polling ─────────────────────────────────────────────────────
fetchState();
fetchTrackRecord();
fetchQuality();
fetchHitRate();
fetchTA35();
fetchPendingOrders();
fetchBatches();
setInterval(fetchState, 5000);
setInterval(fetchHitRate, 30 * 1000);  // refresh every 30 seconds
setInterval(fetchTA35, 60 * 1000);  // refresh every 60 seconds

// ── Wire all event handlers after functions are defined ───────────────────────
document.getElementById('resetBtn').addEventListener('click', function() { resetDashboard(false); });
document.getElementById('resetFullBtn').addEventListener('click', function() { resetDashboard(true); });

// File input wiring — SheetJS header-based detection preview
var _fileInput = document.getElementById('fileInput');
var _uploadBtn = document.getElementById('uploadBtn');
if (_fileInput && _uploadBtn) {
  _fileInput.onchange = async function(e) {
    var files = Array.from(e.target.files || []);
    _uploadBtn.disabled = true;
    _uploadBtn.onclick = null;
    detectedFiles = {portfolio: null, history: null, orders: null, unknown: []};

    if (files.length === 0) {
      var p = document.getElementById('detectionPreview');
      if (p) p.style.display = 'none';
      return;
    }

    var preview = document.getElementById('detectionPreview');
    if (preview) {
      preview.innerHTML = '<div class="det-info">Analyzing files\u2026</div>';
      preview.style.display = 'block';
    }

    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      var type = await readFileHeadersWithSheetJS(f);
      if      (type === 'portfolio' && !detectedFiles.portfolio) detectedFiles.portfolio = f;
      else if (type === 'history'   && !detectedFiles.history)   detectedFiles.history   = f;
      else if (type === 'orders'    && !detectedFiles.orders)    detectedFiles.orders    = f;
      else detectedFiles.unknown.push(f);
    }

    renderDetectionPreview();
    if (detectedFiles.portfolio) {
      _uploadBtn.disabled = false;
      _uploadBtn.onclick = uploadAndRun;
    }
  };
}
// ── 📥 Log trades — standalone history upload ─────────────────────────────────
document.getElementById('historyInput').addEventListener('change', async function() {
  const file = this.files[0];
  if (!file) return;
  addLog('Matching executed trades from history...', 'info');
  const form = new FormData();
  form.append('tx_file', file);
  try {
    const r = await fetch('/api/upload_history', {method: 'POST', body: form});
    const d = await r.json();
    if (d.ok) {
      if (d.matched > 0) {
        addLog('\u2705 Auto-logged ' + d.matched + ' executed trades', 'ok');
        (d.log_lines || []).forEach(l => addLog(l, 'ok'));
        fetchState();
      } else {
        addLog('No new trades matched to approved recs', 'info');
      }
    } else {
      addLog('Match failed: ' + d.error, 'err');
    }
  } catch(e) {
    addLog('Error: ' + e.message, 'err');
  }
  this.value = '';
});

// Event delegation for dynamically rendered approve/reject buttons and stars
document.addEventListener('click', function(e) {
  if (e.target.classList.contains('btn-approve')) {
    var id = parseInt(e.target.dataset.recId);
    var price = parseFloat(e.target.dataset.price) || 0;
    approveRec(id, price);
  } else if (e.target.classList.contains('btn-reject')) {
    rejectRec(parseInt(e.target.dataset.recId));
  } else if (e.target.classList.contains('star')) {
    // Star click: highlight stars 1..val in this group
    var starsDiv = e.target.closest('.stars');
    if (!starsDiv) return;
    var val = parseInt(e.target.dataset.val);
    starsDiv.querySelectorAll('.star').forEach(function(s) {
      s.classList.toggle('s-on', parseInt(s.dataset.val) <= val);
    });
    starsDiv.dataset.rating = val;
  }
});

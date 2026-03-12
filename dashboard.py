"""
Portfolio Agent — Web Dashboard
================================
A Flask web server that serves a live dashboard.
Run with:  py -3.12 dashboard.py
Then open: http://localhost:5000
"""
import logging
import sys
import threading
import traceback
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request

from demo_run import make_macro, make_ohlcv, make_portfolio
from src.agent_core import PortfolioAgent
from src.quant_engine import QuantEngine
from src.report_renderer import render_html_report, save_report_locally, send_email_report

sys.path.insert(0, str(Path(__file__).parent))

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── In-memory state (populated by demo_run or the live agent) ─────────────────
state: dict[str, Any] = {
    "portfolio":  None,
    "signals":    [],
    "report":     None,
    "last_run":   None,
    "is_running": False,
    "run_log":    [],
    "macro":      None,
}

# ── Dashboard HTML ─────────────────────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio Agent</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg:       #0a0e17;
  --surface:  #111827;
  --border:   #1e2d40;
  --accent:   #00d4aa;
  --accent2:  #3b82f6;
  --danger:   #f43f5e;
  --warn:     #f59e0b;
  --text:     #e2e8f0;
  --muted:    #64748b;
  --card:     #131c2e;
  --green:    #10b981;
  --red:      #ef4444;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;min-height:100vh;overflow-x:hidden}

/* ── Background grid ── */
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(var(--border) 1px,transparent 1px),linear-gradient(90deg,var(--border) 1px,transparent 1px);background-size:40px 40px;opacity:.3;pointer-events:none}

/* ── Header ── */
.header{position:sticky;top:0;z-index:100;background:rgba(10,14,23,.9);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:0 32px;display:flex;align-items:center;gap:20px;height:60px}
.logo{font-family:'Syne',sans-serif;font-weight:800;font-size:1.1rem;color:var(--accent);letter-spacing:-.02em;display:flex;align-items:center;gap:8px}
.logo span{color:var(--text)}
.header-right{margin-left:auto;display:flex;align-items:center;gap:16px}
.status-dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
.status-dot.offline{background:var(--muted);box-shadow:none;animation:none}
.status-dot.running{background:var(--warn);box-shadow:0 0 8px var(--warn);animation:spin-pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes spin-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.6;transform:scale(1.3)}}
.last-run{font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted)}

/* ── Run button ── */
.run-btn{padding:8px 20px;background:var(--accent);color:#0a0e17;border:none;border-radius:6px;font-family:'Syne',sans-serif;font-weight:700;font-size:.82rem;cursor:pointer;letter-spacing:.05em;transition:all .2s;position:relative;overflow:hidden}
.run-btn:hover{background:#00f0c0;transform:translateY(-1px);box-shadow:0 4px 20px rgba(0,212,170,.3)}
.run-btn:disabled{background:var(--muted);cursor:not-allowed;transform:none;box-shadow:none}
.run-btn .spinner{display:none;width:12px;height:12px;border:2px solid rgba(10,14,23,.3);border-top-color:#0a0e17;border-radius:50%;animation:spin .6s linear infinite;margin-right:6px}
.run-btn.loading .spinner{display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── Main layout ── */
.main{padding:28px 32px;max-width:1400px;margin:0 auto}

/* ── Stat cards row ── */
.stats-row{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:24px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;position:relative;overflow:hidden;transition:border-color .2s}
.stat:hover{border-color:var(--accent)}
.stat::after{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(0,212,170,.04),transparent);pointer-events:none}
.stat-label{font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;font-family:'DM Mono',monospace}
.stat-value{font-family:'Syne',sans-serif;font-size:1.4rem;font-weight:700;color:var(--text)}
.stat-value.green{color:var(--green)}
.stat-value.red{color:var(--red)}
.stat-value.accent{color:var(--accent)}
.stat-sub{font-size:.72rem;color:var(--muted);margin-top:3px;font-family:'DM Mono',monospace}

/* ── Grid ── */
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.grid-3{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:16px;margin-bottom:16px}

/* ── Panel ── */
.panel{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.panel-header{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.panel-title{font-family:'Syne',sans-serif;font-weight:700;font-size:.88rem;letter-spacing:.03em;color:var(--text)}
.panel-badge{font-family:'DM Mono',monospace;font-size:.68rem;background:rgba(0,212,170,.15);color:var(--accent);padding:2px 8px;border-radius:20px;border:1px solid rgba(0,212,170,.2)}
.panel-body{padding:0}

/* ── Holdings table ── */
.table{width:100%;border-collapse:collapse}
.table th{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;padding:10px 16px;text-align:left;border-bottom:1px solid var(--border)}
.table td{padding:10px 16px;border-bottom:1px solid rgba(30,45,64,.5);font-size:.83rem;transition:background .15s}
.table tr:last-child td{border-bottom:none}
.table tr:hover td{background:rgba(0,212,170,.03)}
.table .ticker{font-family:'Syne',sans-serif;font-weight:700;color:var(--accent);font-size:.88rem}
.table .name{color:var(--muted);font-size:.78rem;margin-top:1px}
.pnl-pos{color:var(--green);font-family:'DM Mono',monospace;font-size:.8rem}
.pnl-neg{color:var(--red);font-family:'DM Mono',monospace;font-size:.8rem}
.weight-bar{height:4px;background:rgba(0,212,170,.15);border-radius:2px;margin-top:4px;overflow:hidden}
.weight-fill{height:100%;background:var(--accent);border-radius:2px;transition:width .5s ease}

/* ── Signals ── */
.signal-row{display:flex;align-items:center;padding:10px 16px;border-bottom:1px solid rgba(30,45,64,.5);gap:12px;transition:background .15s}
.signal-row:last-child{border-bottom:none}
.signal-row:hover{background:rgba(0,212,170,.03)}
.signal-ticker{font-family:'Syne',sans-serif;font-weight:700;font-size:.88rem;color:var(--accent);width:56px;flex-shrink:0}
.signal-bar-wrap{flex:1;height:6px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden}
.signal-bar{height:100%;border-radius:3px;transition:width .6s ease}
.signal-score{font-family:'DM Mono',monospace;font-size:.75rem;width:48px;text-align:right;flex-shrink:0}
.signal-flags{font-size:.72rem;color:var(--muted);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ── Recommendations ── */
.rec-card{padding:16px 20px;border-bottom:1px solid rgba(30,45,64,.5);transition:background .15s}
.rec-card:last-child{border-bottom:none}
.rec-card:hover{background:rgba(0,212,170,.02)}
.rec-top{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.action-pill{padding:3px 10px;border-radius:20px;font-family:'DM Mono',monospace;font-size:.7rem;font-weight:500;letter-spacing:.05em}
.pill-BUY{background:rgba(16,185,129,.2);color:#34d399;border:1px solid rgba(16,185,129,.3)}
.pill-SELL{background:rgba(239,68,68,.2);color:#f87171;border:1px solid rgba(239,68,68,.3)}
.pill-HOLD{background:rgba(245,158,11,.2);color:#fbbf24;border:1px solid rgba(245,158,11,.3)}
.pill-WATCH{background:rgba(59,130,246,.2);color:#60a5fa;border:1px solid rgba(59,130,246,.3)}
.conv-pip{width:7px;height:7px;border-radius:50%}
.rec-ticker{font-family:'Syne',sans-serif;font-weight:800;font-size:1rem;color:var(--text)}
.rec-pos{margin-left:auto;font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted)}
.rec-thesis{font-size:.82rem;color:#94a3b8;line-height:1.55;margin-bottom:6px}
.rec-risk{font-size:.78rem;color:#78716c;border-left:2px solid var(--warn);padding-left:8px;margin-bottom:6px}
.rec-tags{display:flex;flex-wrap:wrap;gap:4px}
.rec-tag{font-size:.68rem;background:rgba(255,255,255,.05);color:var(--muted);padding:2px 7px;border-radius:10px;border:1px solid var(--border)}
.rec-target{font-family:'DM Mono',monospace;font-size:.72rem;color:var(--green);margin-left:auto}

/* ── Log ── */
.log-wrap{padding:12px 16px;font-family:'DM Mono',monospace;font-size:.75rem;color:var(--muted);height:180px;overflow-y:auto;display:flex;flex-direction:column;gap:3px}
.log-line{display:flex;gap:10px}
.log-time{color:var(--border);flex-shrink:0}
.log-msg{color:var(--muted)}
.log-msg.ok{color:var(--green)}
.log-msg.err{color:var(--red)}
.log-msg.info{color:var(--accent)}
.log-msg.warn{color:var(--warn)}

/* ── Macro strip ── */
.macro-strip{display:flex;gap:0;border-bottom:1px solid var(--border)}
.macro-item{flex:1;padding:14px 16px;border-right:1px solid var(--border);last-child:border-none}
.macro-item:last-child{border-right:none}
.macro-lbl{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}
.macro-val{font-family:'Syne',sans-serif;font-weight:700;font-size:1rem;color:var(--text)}

/* ── Risk flags ── */
.flag-list{padding:14px 16px;display:flex;flex-wrap:wrap;gap:6px}
.flag-chip{font-size:.75rem;background:rgba(245,158,11,.1);color:var(--warn);border:1px solid rgba(245,158,11,.2);padding:4px 10px;border-radius:20px}

/* ── Empty state ── */
.empty{padding:40px 20px;text-align:center;color:var(--muted);font-size:.85rem}
.empty .icon{font-size:2rem;margin-bottom:8px;opacity:.4}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* ── Responsive ── */
@media(max-width:900px){
  .stats-row{grid-template-columns:repeat(3,1fr)}
  .grid-2,.grid-3{grid-template-columns:1fr}
}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="logo">◈ <span>Portfolio</span> Agent</div>
  <div id="statusDot" class="status-dot offline"></div>
  <span id="lastRunLabel" class="last-run">Never run</span>
  <div class="header-right">
    <button class="run-btn" id="runBtn" onclick="runDemo()">
      <span class="spinner"></span>
      ▶ RUN DEMO
    </button>
  </div>
</div>

<!-- Main -->
<div class="main">

  <!-- Stats row -->
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Portfolio Value</div><div class="stat-value accent" id="statTotal">—</div><div class="stat-sub" id="statCash">cash: —</div></div>
    <div class="stat"><div class="stat-label">Day P&L</div><div class="stat-value" id="statDayPnl">—</div><div class="stat-sub" id="statDayPct">—</div></div>
    <div class="stat"><div class="stat-label">Holdings</div><div class="stat-value" id="statHoldings">—</div><div class="stat-sub">positions</div></div>
    <div class="stat"><div class="stat-label">Recommendations</div><div class="stat-value" id="statRecs">—</div><div class="stat-sub" id="statRecSub">—</div></div>
    <div class="stat"><div class="stat-label">USD / ILS</div><div class="stat-value" id="statUsd">—</div><div class="stat-sub">BOI rate: <span id="statBoi">—</span>%</div></div>
    <div class="stat"><div class="stat-label">TA-35 / TA-125</div><div class="stat-value" id="statIndex">—</div><div class="stat-sub" id="statIndex2">—</div></div>
  </div>

  <!-- Row 1: Holdings + Signals -->
  <div class="grid-2">

    <!-- Holdings -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">📊 Portfolio Holdings</div>
        <div class="panel-badge" id="holdingsBadge">0 positions</div>
      </div>
      <div class="panel-body">
        <table class="table">
          <thead><tr>
            <th>Stock</th><th>Qty</th><th>Avg Cost</th><th>Current</th><th>P&L</th><th>Weight</th>
          </tr></thead>
          <tbody id="holdingsBody">
            <tr><td colspan="6"><div class="empty"><div class="icon">📭</div>Run the demo to load portfolio</div></td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Quant Signals -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">🔢 Quant Signals</div>
        <div class="panel-badge" id="signalsBadge">TA-125 universe</div>
      </div>
      <div class="panel-body" id="signalsBody">
        <div class="empty"><div class="icon">📡</div>Signals will appear after demo run</div>
      </div>
    </div>

  </div>

  <!-- Row 2: Recommendations + Macro + Log -->
  <div class="grid-3">

    <!-- Recommendations -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">🤖 AI Recommendations</div>
        <div class="panel-badge" id="recsBadge">Claude Opus 4</div>
      </div>
      <div class="panel-body" id="recsBody">
        <div class="empty"><div class="icon">💡</div>Recommendations will appear after demo run</div>
      </div>
    </div>

    <!-- Macro + Risk -->
    <div class="panel">
      <div class="panel-header"><div class="panel-title">🌍 Macro Environment</div></div>
      <div class="macro-strip" id="macroStrip">
        <div class="macro-item"><div class="macro-lbl">BOI Rate</div><div class="macro-val" id="m1">—</div></div>
        <div class="macro-item"><div class="macro-lbl">USD/ILS</div><div class="macro-val" id="m2">—</div></div>
        <div class="macro-item"><div class="macro-lbl">CPI</div><div class="macro-val" id="m3">—</div></div>
      </div>
      <div class="panel-header" style="border-top:1px solid var(--border)"><div class="panel-title">⚠️ Risk Flags</div></div>
      <div id="flagsBody"><div class="empty" style="padding:20px"><div class="icon">🛡️</div>No flags yet</div></div>
      <div class="panel-header" style="border-top:1px solid var(--border)"><div class="panel-title">📊 Market Summary</div></div>
      <div id="marketSummary" style="padding:14px 16px;font-size:.82rem;color:#94a3b8;line-height:1.6">
        Run the demo to see market analysis from Claude.
      </div>
    </div>

    <!-- Run Log -->
    <div class="panel">
      <div class="panel-header"><div class="panel-title">📋 Run Log</div><div class="panel-badge" id="logBadge">live</div></div>
      <div class="log-wrap" id="logWrap">
        <div class="log-line"><span class="log-time">--:--:--</span><span class="log-msg">Waiting for demo run...</span></div>
      </div>
    </div>

  </div>

</div>

<script>
// ── State ────────────────────────────────────────────────────────────────────
let pollInterval = null;

// ── Run demo ─────────────────────────────────────────────────────────────────
async function runDemo() {
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  btn.classList.add('loading');
  btn.innerHTML = '<span class="spinner"></span>RUNNING...';
  document.getElementById('statusDot').className = 'status-dot running';
  addLog('Starting demo run...', 'info');

  try {
    const res = await fetch('/api/run', {method:'POST'});
    const data = await res.json();
    if (data.started) {
      addLog('Pipeline started — calling Claude Opus 4...', 'info');
      startPolling();
    } else {
      addLog('Already running, please wait...', 'warn');
      btn.disabled = false; btn.classList.remove('loading');
      btn.innerHTML = '▶ RUN DEMO';
    }
  } catch(e) {
    addLog('Failed to start: ' + e.message, 'err');
    btn.disabled = false; btn.classList.remove('loading');
    btn.innerHTML = '▶ RUN DEMO';
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
      const btn = document.getElementById('runBtn');
      btn.disabled = false; btn.classList.remove('loading');
      btn.innerHTML = '▶ RUN DEMO';
      document.getElementById('statusDot').className = data.report ? 'status-dot' : 'status-dot offline';
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
    document.getElementById('statIndex').textContent = m.ta35_close ? '₪' + m.ta35_close : '—';
    document.getElementById('statIndex2').textContent = m.ta125_close ? 'TA-125: ₪' + m.ta125_close : '—';
  }

  // Signals
  if (data.signals && data.signals.length) {
    renderSignals(data.signals);
    document.getElementById('signalsBadge').textContent = data.signals.length + ' tickers';
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
  tbody.innerHTML = holdings.map(h => {
    const pnl = parseFloat(h.unrealized_pnl_pct);
    const pnlCls = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    const pnlSign = pnl >= 0 ? '+' : '';
    const weight = parseFloat(h.weight_pct);
    return `<tr>
      <td><div class="ticker">${h.ticker}</div><div class="name">${h.company_name}</div></td>
      <td style="font-family:'DM Mono',monospace;font-size:.8rem">${parseFloat(h.quantity).toLocaleString()}</td>
      <td style="font-family:'DM Mono',monospace;font-size:.8rem">₪${parseFloat(h.avg_cost_ils).toFixed(2)}</td>
      <td style="font-family:'DM Mono',monospace;font-size:.8rem">₪${parseFloat(h.current_price).toFixed(2)}</td>
      <td><span class="${pnlCls}">${pnlSign}${pnl.toFixed(1)}%</span></td>
      <td style="min-width:70px">
        <div style="font-size:.72rem;color:var(--muted);font-family:'DM Mono',monospace">${weight.toFixed(1)}%</div>
        <div class="weight-bar"><div class="weight-fill" style="width:${Math.min(weight*3,100)}%"></div></div>
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

function renderRecs(recs) {
  const convColors = {HIGH:'var(--green)',MEDIUM:'var(--warn)',LOW:'var(--muted)'};
  document.getElementById('recsBody').innerHTML = recs.map(r => {
    const target = r.price_target_ils ? `<span class="rec-target">🎯 ₪${parseFloat(r.price_target_ils).toFixed(2)}</span>` : '';
    const tags = (r.supporting_signals||[]).map(s=>`<span class="rec-tag">${s}</span>`).join('');
    const pos = r.suggested_position_pct > 0 ? `${r.suggested_position_pct}% portfolio` : '';
    return `<div class="rec-card">
      <div class="rec-top">
        <span class="action-pill pill-${r.action}">${r.action}</span>
        <span class="rec-ticker">${r.ticker}</span>
        <span class="conv-pip" style="background:${convColors[r.conviction]||'var(--muted)'}"></span>
        <span style="font-size:.72rem;color:var(--muted)">${r.conviction}</span>
        ${pos ? `<span class="rec-pos">${pos}</span>` : ''}
        ${target}
      </div>
      <div class="rec-thesis">${r.thesis}</div>
      <div class="rec-risk"><strong style="color:var(--warn)">Risk:</strong> ${r.key_risk}</div>
      ${tags ? `<div class="rec-tags">${tags}</div>` : ''}
    </div>`;
  }).join('');
}

function renderFlags(flags) {
  if (!flags || !flags.length) {
    document.getElementById('flagsBody').innerHTML = '<div class="empty" style="padding:14px"><div class="icon">✅</div>No risk flags</div>';
    return;
  }
  document.getElementById('flagsBody').innerHTML = '<div class="flag-list">' + flags.map(f=>`<span class="flag-chip">⚠ ${f}</span>`).join('') + '</div>';
}

function addLog(msg, type='info') {
  const wrap = document.getElementById('logWrap');
  const now = new Date().toTimeString().slice(0,8);
  wrap.innerHTML += `<div class="log-line"><span class="log-time">${now}</span><span class="log-msg ${type}">${msg}</span></div>`;
  wrap.scrollTop = wrap.scrollHeight;
}

// ── Initial load ──────────────────────────────────────────────────────────────
fetchState();
setInterval(fetchState, 5000);
</script>
</body>
</html>"""


# ── API routes ────────────────────────────────────────────────────────────────

def add_log(msg: str, typ: str = "info") -> None:
    """Append a timestamped entry to the in-memory run log and emit to logger.

    Args:
        msg: Human-readable log message.
        typ: Log level tag shown in the dashboard UI ('info', 'ok', 'warn', 'err').
    """
    now = datetime.now().strftime("%H:%M:%S")
    state["run_log"].append({"time": now, "msg": msg, "type": typ})
    logger.info("[%s] %s", now, msg)


def run_demo_background() -> None:
    """Run the full demo pipeline in a background thread.

    Builds a mock Israeli portfolio, generates OHLCV data for the TA-125
    universe, computes quant signals, calls the Claude API for recommendations,
    renders the HTML report, and attempts email delivery via MailHog.
    Updates the shared `state` dict throughout so the dashboard can poll progress.
    """
    try:
        state["is_running"] = True
        state["run_log"] = []

        add_log("Building mock Israeli portfolio...", "info")
        portfolio = make_portfolio()
        state["portfolio"] = portfolio.model_dump(mode="json")
        add_log(
            f"Portfolio loaded — ₪{portfolio.total_value_ils:,.0f} | {len(portfolio.holdings)} holdings",
            "ok",
        )

        add_log("Generating market data for TA-125 universe...", "info")
        all_tickers = [
            "TEVA", "NICE", "CHKP", "LUMI", "ICL", "ESLT", "BEZQ", "POLI", "BRMG",
            "SPNS", "KCHD", "SANO", "AZRG", "AMOT", "IGLD", "ENLT", "NWRL", "MFON",
            "MTRX", "BIDI", "FIBI", "MISH", "ORION", "RDWR", "PMCN", "CEVA", "GILT",
        ]
        ohlcv_data = {t: make_ohlcv(t, base=50 + hash(t) % 400, days=60) for t in all_tickers}
        add_log(f"60 days × {len(all_tickers)} tickers generated", "ok")

        add_log("Computing quant signals (RSI, MACD, momentum)...", "info")
        sector_pes = {
            "Pharma": 18.5, "Technology": 25.0, "Banks": 10.0,
            "Materials": 14.0, "Telecom": 12.0, "Defense": 22.0,
        }
        engine = QuantEngine(sector_pe_medians=sector_pes)
        tickers_data = {t: {"bars": ohlcv_data[t], "info": None} for t in all_tickers}
        signals = engine.compute_all(tickers_data)
        state["signals"] = [s.model_dump(mode="json") for s in signals]
        bullish = sum(1 for s in signals if (s.composite_score or 0) > 0.2)
        add_log(f"{bullish} bullish signals, {len(signals) - bullish} neutral/bearish", "ok")

        macro = make_macro()
        state["macro"] = macro.model_dump(mode="json")
        add_log(f"Macro: BOI {macro.boi_interest_rate}% | USD/ILS {macro.usd_ils_rate}", "ok")

        add_log("Calling Claude Opus 4 — this takes ~20 seconds...", "warn")
        index_perf = {"ta35": {"change_pct": 0.82}, "ta125": {"change_pct": 0.61}}
        news = [
            {
                "source": "Globes",
                "title": "Teva signs $2.3B biosimilar licensing deal",
                "body": "Teva Pharmaceutical announced a landmark licensing agreement for its biosimilar portfolio, expected to generate $400M annually by 2026.",
                "published_at": datetime.now().isoformat(),
                "tickers_mentioned": ["TEVA"],
            },
            {
                "source": "TheMarker",
                "title": "Bank of Israel signals possible rate cut Q2",
                "body": "BOI Governor hinted at potential rate reduction if inflation continues moderating. Banking stocks led gains.",
                "published_at": datetime.now().isoformat(),
                "tickers_mentioned": ["LUMI", "POLI", "FIBI"],
            },
            {
                "source": "Calcalist",
                "title": "Elbit Systems wins $1.2B IDF drone contract",
                "body": "Elbit secured a major multi-year contract for next-generation drone surveillance systems through 2027.",
                "published_at": datetime.now().isoformat(),
                "tickers_mentioned": ["ESLT"],
            },
        ]
        agent = PortfolioAgent()
        report, usage = agent.generate_report(
            portfolio=portfolio, signals=signals, macro=macro,
            index_perf=index_perf, news_chunks=news, run_type="morning",
        )
        state["report"] = report.model_dump(mode="json")
        add_log(
            f"Claude done! {len(report.recommendations)} recommendations | "
            f"{usage['prompt_tokens'] + usage['completion_tokens']:,} tokens | {usage['duration_s']}s",
            "ok",
        )

        add_log("Rendering HTML report...", "info")
        html = render_html_report(report, "dashboard-run")
        local_path = save_report_locally(html, report)
        add_log(f"Report saved: {local_path}", "ok")
        try:
            send_email_report(html, report)
            add_log("Email sent to MailHog → http://localhost:8025", "ok")
        except Exception:
            add_log("MailHog not running — email skipped (report saved to file)", "warn")

        state["last_run"] = datetime.now().strftime("%H:%M:%S")
        add_log("✅ Demo complete! Dashboard updated.", "ok")

    except Exception as e:
        add_log(f"Error: {e}", "err")
        logger.exception("Unhandled error in demo pipeline")
    finally:
        state["is_running"] = False


@app.route("/")
def index() -> str:
    """Serve the main dashboard HTML page."""
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/run", methods=["POST"])
def api_run() -> Response:
    """Start the demo pipeline in a background thread if not already running.

    Returns:
        JSON with 'started' bool and optional 'reason' if not started.
    """
    if state["is_running"]:
        return jsonify({"started": False, "reason": "already running"})
    t = threading.Thread(target=run_demo_background, daemon=True)
    t.start()
    return jsonify({"started": True})


@app.route("/health")
def health() -> tuple[Response, int]:
    """Return a simple health-check response for load balancer probes."""
    return jsonify({"status": "ok"}), 200


@app.route("/api/state")
def api_state() -> Response:
    """Return the current in-memory agent state as JSON for dashboard polling."""
    return jsonify({
        "is_running": state["is_running"],
        "portfolio":  state["portfolio"],
        "signals":    state["signals"],
        "report":     state["report"],
        "macro":      state["macro"],
        "last_run":   state["last_run"],
        "run_log":    state["run_log"],
    })


if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  📊  Portfolio Agent — Web Dashboard")
    print("=" * 55)
    print()
    print("  Opening at: http://localhost:5000")
    print()
    print("  1. Click '▶ RUN DEMO' in the dashboard")
    print("  2. Watch the live log as Claude thinks")
    print("  3. See recommendations populate in real-time")
    print()
    print("  Press Ctrl+C to stop the server")
    print("=" * 55 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)

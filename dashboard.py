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

from demo_run import make_ohlcv
from src.models.market import PortfolioSnapshot
from src.utils.portfolio_loader import load_portfolio
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
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
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
.header-right{margin-left:auto;display:flex;align-items:center;gap:16px;position:relative}
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

/* ── Upload button ── */
.upload-btn{padding:8px 20px;background:transparent;color:var(--accent);border:1px solid var(--accent);border-radius:6px;font-family:'Syne',sans-serif;font-weight:700;font-size:.82rem;cursor:pointer;letter-spacing:.05em;transition:all .2s}
.upload-btn:hover{background:rgba(0,212,170,.1);transform:translateY(-1px)}
.upload-btn:disabled{color:var(--muted);border-color:var(--muted);cursor:not-allowed;transform:none}
.file-input-native{color:var(--text);background:rgba(255,255,255,.07);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:.8rem;cursor:pointer;font-family:'DM Sans',sans-serif}
.file-input-native::file-selector-button{background:var(--accent);color:#0a0e17;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:.78rem;margin-right:8px;font-family:'DM Sans',sans-serif}
#detectionPreview{display:none;position:absolute;top:calc(100% + 4px);right:0;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:8px 12px;min-width:260px;z-index:200;box-shadow:0 4px 16px rgba(0,0,0,.4)}
.det-ok{color:var(--green);font-size:.72rem;padding:2px 0;font-family:'DM Mono',monospace}
.det-warn{color:var(--warn);font-size:.72rem;padding:2px 0;font-family:'DM Mono',monospace}
.det-info{color:var(--muted);font-size:.72rem;padding:2px 0;font-family:'DM Mono',monospace}
.reset-wrap{display:flex;gap:2px}
.reset-btn{padding:8px 14px;background:transparent;color:var(--muted);border:1px solid var(--border);border-radius:6px;font-family:'Syne',sans-serif;font-weight:600;font-size:.78rem;cursor:pointer;transition:all .2s}
.reset-btn:hover{border-color:var(--danger);color:var(--danger)}
.reset-full{padding:8px 10px;border-radius:6px}
.workflow-hint{background:rgba(0,212,170,.06);border-bottom:1px solid rgba(0,212,170,.15);padding:6px 32px;font-size:.75rem;color:var(--muted);font-family:'DM Mono',monospace}

/* ── Main layout ── */
.main{padding:28px 32px;max-width:1400px;margin:0 auto}

/* ── Stat cards row ── */
.stats-row{display:grid;grid-template-columns:repeat(7,1fr);gap:12px;margin-bottom:24px}
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
.batch-select{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:4px;font-size:.72rem;padding:3px 8px;font-family:'DM Mono',monospace;cursor:pointer;max-width:260px;flex-shrink:0}
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
/* header row */
.rec-header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.rec-header-left{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.rec-header-right{flex-shrink:0}
.action-pill{padding:3px 10px;border-radius:20px;font-family:'DM Mono',monospace;font-size:.7rem;font-weight:500;letter-spacing:.05em}
.pill-BUY{background:rgba(16,185,129,.2);color:#34d399;border:1px solid rgba(16,185,129,.3)}
.pill-SELL{background:rgba(239,68,68,.2);color:#f87171;border:1px solid rgba(239,68,68,.3)}
.pill-HOLD{background:rgba(245,158,11,.2);color:#fbbf24;border:1px solid rgba(245,158,11,.3)}
.pill-WATCH{background:rgba(59,130,246,.2);color:#60a5fa;border:1px solid rgba(59,130,246,.3)}
.pill-TRIM{background:rgba(124,58,237,.2);color:#a78bfa;border:1px solid rgba(124,58,237,.3)}
.rec-id-badge{font-family:'DM Mono',monospace;font-size:.68rem;color:var(--muted);opacity:.6;letter-spacing:.02em}
.rec-ticker{font-family:'Syne',sans-serif;font-weight:800;font-size:.95rem;color:var(--text)}
.rec-sub-ticker{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);opacity:.7}
.conv-badge{font-family:'DM Mono',monospace;font-size:.65rem;font-weight:600;padding:2px 8px;border-radius:10px;border:1px solid;letter-spacing:.05em;opacity:.85}
/* meta row */
.rec-meta{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin-bottom:8px}
.meta-chip{font-family:'DM Mono',monospace;font-size:.68rem;color:var(--accent);background:rgba(0,212,170,.07);border:1px solid rgba(0,212,170,.15);padding:2px 8px;border-radius:10px}
.rec-tag{font-size:.68rem;background:rgba(255,255,255,.05);color:var(--muted);padding:2px 7px;border-radius:10px;border:1px solid var(--border)}
/* body */
.rec-thesis{font-size:.82rem;color:#94a3b8;line-height:1.55;margin-bottom:8px}
.rec-risk{font-size:.78rem;color:#78716c;border-left:2px solid var(--warn);padding-left:8px;margin-bottom:8px}
.rec-executed{font-size:.76rem;color:var(--green);background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);border-radius:6px;padding:5px 10px;margin-top:6px;font-family:'DM Mono',monospace;}

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

/* ── Track Record panel ── */
.track-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:14px 16px}
.track-stat{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center}
.track-stat .val{font-family:'Syne',sans-serif;font-weight:700;font-size:1.3rem}
.track-stat .lbl{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-top:3px}
.track-divider{border:none;border-top:1px solid var(--border);margin:0 16px}
.track-breakdown{padding:12px 16px;font-size:.8rem;color:#94a3b8;line-height:1.8}
.track-row{display:flex;justify-content:space-between}
.track-pos{color:var(--green)}
.track-neg{color:var(--red)}

/* ── Quality Tracker panel ── */
.quality-overall{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;padding:14px 16px}
.quality-stat{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:8px;padding:10px;text-align:center}
.quality-stat .val{font-family:'Syne',sans-serif;font-weight:700;font-size:1.2rem}
.quality-stat .lbl{font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-top:2px}
.quality-green{color:#10b981}
.quality-amber{color:#f59e0b}
.quality-red{color:#ef4444}
.quality-section{padding:0 16px 14px}
.quality-section-title{font-size:.65rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:10px 0 6px}
.quality-table{width:100%;border-collapse:collapse;font-size:.75rem}
.quality-table th{color:var(--muted);font-weight:400;text-align:left;padding:2px 6px 4px;font-size:.65rem;text-transform:uppercase}
.quality-table td{padding:4px 6px;border-top:1px solid rgba(255,255,255,.04)}
.quality-table .num{text-align:right;font-family:'DM Mono',monospace}

/* ── Shared rec-card note field ── */
.rec-note{width:100%;margin-top:8px;font-size:.78rem;background:rgba(255,255,255,.04);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 8px;resize:vertical;font-family:'DM Mono',monospace;box-sizing:border-box}
.rec-note:focus{outline:none;border-color:rgba(99,102,241,.5)}

/* ── WATCH missed-opportunity tracker ── */
.watch-section{margin:0 16px 14px;border:1px solid rgba(99,102,241,.2);border-radius:8px;padding:10px 12px;background:rgba(99,102,241,.04)}
.watch-header{display:flex;justify-content:space-between;align-items:center;font-size:.78rem;font-weight:600;color:var(--text);margin-bottom:3px}
.watch-rate{font-family:'DM Mono',monospace;font-size:.75rem}
.watch-subheader{font-size:.65rem;color:var(--muted);margin-bottom:8px}
.watch-row{display:flex;align-items:center;gap:10px;padding:4px 0;border-top:1px solid rgba(255,255,255,.04);font-size:.75rem}
.watch-ticker{font-family:'DM Mono',monospace;font-weight:600;min-width:56px}
.watch-return{font-family:'DM Mono',monospace;min-width:58px;text-align:right}
.watch-verdict{flex:1;font-size:.72rem}

/* ── Approve / Reject buttons ── */
.rec-actions{display:flex;gap:8px;margin-top:8px}
.btn-approve{padding:4px 14px;background:rgba(16,185,129,.15);color:var(--green);border:1px solid rgba(16,185,129,.3);border-radius:20px;font-family:'DM Mono',monospace;font-size:.7rem;cursor:pointer;transition:all .2s}
.btn-approve:hover{background:rgba(16,185,129,.3)}
.btn-reject{padding:4px 14px;background:rgba(239,68,68,.15);color:var(--red);border:1px solid rgba(239,68,68,.3);border-radius:20px;font-family:'DM Mono',monospace;font-size:.7rem;cursor:pointer;transition:all .2s}
.btn-reject:hover{background:rgba(239,68,68,.3)}
.btn-approved{padding:4px 14px;background:rgba(16,185,129,.1);color:var(--green);border:1px solid rgba(16,185,129,.2);border-radius:20px;font-family:'DM Mono',monospace;font-size:.7rem;opacity:.7}
.btn-rejected{padding:4px 14px;background:rgba(239,68,68,.1);color:var(--red);border:1px solid rgba(239,68,68,.2);border-radius:20px;font-family:'DM Mono',monospace;font-size:.7rem;opacity:.7}

/* ── Empty state ── */
.empty{padding:40px 20px;text-align:center;color:var(--muted);font-size:.85rem}
.empty .icon{font-size:2rem;margin-bottom:8px;opacity:.4}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* ── Responsive ── */
@media(max-width:900px){
  .stats-row{grid-template-columns:repeat(4,1fr)}
  .grid-2,.grid-3{grid-template-columns:1fr}
}

/* ── Tool buttons + collapsible panels ── */
.rec-card-btns{display:flex;gap:6px;margin-top:10px}
.eval-btn,.ai-btn{display:flex;align-items:center;gap:5px;padding:4px 12px;border-radius:20px;font-size:.68rem;cursor:pointer;transition:all .2s;font-family:'DM Mono',monospace}
.eval-btn{background:rgba(99,102,241,.12);color:#818cf8;border:1px solid rgba(99,102,241,.25)}
.eval-btn:hover,.eval-btn.btn-active{background:rgba(99,102,241,.28);border-color:rgba(99,102,241,.5)}
.ai-btn{background:rgba(0,212,170,.08);color:var(--accent);border:1px solid rgba(0,212,170,.18)}
.ai-btn:hover,.ai-btn.btn-active{background:rgba(0,212,170,.22);border-color:rgba(0,212,170,.4)}
.btn-chevron{font-size:.6rem;transition:transform .2s;display:inline-block}
.btn-active .btn-chevron{transform:rotate(90deg)}
.eval-panel{background:rgba(0,0,0,.25);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-top:8px;display:none}
.eval-section-title{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;opacity:.7}
.eval-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:.78rem;color:var(--muted)}
.stars{cursor:pointer;letter-spacing:3px;font-size:1rem;color:rgba(255,255,255,.15);user-select:none}
.stars .s-on{color:#f59e0b}
.eval-panel textarea{width:100%;background:rgba(0,0,0,.3);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:8px;font-size:.78rem;resize:none;margin:10px 0 8px;box-sizing:border-box}
.eval-panel textarea::placeholder{color:var(--muted)}
.eval-actions{display:flex;gap:8px}
.eval-approve{padding:4px 14px;background:rgba(16,185,129,.15);color:var(--green);border:1px solid rgba(16,185,129,.3);border-radius:20px;font-size:.7rem;cursor:pointer;transition:background .2s}
.eval-approve:hover{background:rgba(16,185,129,.3)}
.eval-reject{padding:4px 14px;background:rgba(239,68,68,.12);color:var(--red);border:1px solid rgba(239,68,68,.25);border-radius:20px;font-size:.7rem;cursor:pointer;transition:background .2s}
.eval-reject:hover{background:rgba(239,68,68,.25)}
/* ── AI analysis panel ── */
.ai-panel{background:rgba(0,212,170,.04);border:1px solid rgba(0,212,170,.15);border-radius:8px;padding:14px 16px;margin-top:8px;display:none}
/* ── Status badges ── */
.status-badge{font-family:"DM Mono",monospace;font-size:.68rem;padding:2px 8px;border-radius:10px;font-weight:500}
.status-badge.approved{background:rgba(16,185,129,.15);color:#34d399;border:1px solid rgba(16,185,129,.3)}
.status-badge.rejected{background:rgba(239,68,68,.15);color:#f87171;border:1px solid rgba(239,68,68,.3)}
.status-badge.pending{background:rgba(245,158,11,.15);color:#fbbf24;border:1px solid rgba(245,158,11,.3)}
.status-badge.superseded{background:rgba(100,116,139,.15);color:#94a3b8;border:1px solid rgba(100,116,139,.3)}
.ai-loading{color:var(--muted);font-size:.78rem;font-style:italic}
.ai-result{color:var(--text);font-size:.8rem;line-height:1.65;white-space:pre-wrap}
.ai-error{color:var(--red);font-size:.78rem}

/* ── Pending Orders panel ── */
.order-row{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid rgba(30,45,64,.5);flex-wrap:wrap}
.order-row:last-child{border-bottom:none}
.order-action{font-family:'DM Mono',monospace;font-size:.72rem;font-weight:600;padding:2px 10px;border-radius:20px;flex-shrink:0}
.order-buy{background:rgba(16,185,129,.18);color:#34d399;border:1px solid rgba(16,185,129,.3)}
.order-sell{background:rgba(239,68,68,.18);color:#f87171;border:1px solid rgba(239,68,68,.3)}
.order-name{font-family:'Syne',sans-serif;font-size:.83rem;font-weight:600;color:var(--text);flex:1;min-width:0}
.order-detail{font-family:'DM Mono',monospace;font-size:.78rem;color:var(--accent);flex-shrink:0}
.order-date{font-family:'DM Mono',monospace;font-size:.7rem;color:var(--muted);flex-shrink:0}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="logo">◈ <span>Portfolio</span> Agent</div>
  <div id="statusDot" class="status-dot offline"></div>
  <span id="lastRunLabel" class="last-run">Never run</span>
  <div class="header-right">
    <input type="file" id="fileInput" accept=".xlsx" multiple class="file-input-native"
      title="Select up to 3 Bank Discount xlsx files — portfolio, transaction history, open orders. Type is auto-detected from headers.">
    <div id="detectionPreview"></div>
    <button class="upload-btn" id="uploadBtn" disabled
      title="Upload portfolio + optional transaction history + optional orders for enriched recommendations">
      📤 UPLOAD &amp; RUN
    </button>
    <input type="file" id="historyInput" accept=".xlsx" style="display:none">
    <button class="run-btn" style="background:var(--accent2);font-size:.75rem"
      onclick="document.getElementById('historyInput').click()"
      title="Monday evening: upload fresh ביצועים היסטוריים to auto-log today's executed trades">
      📥 Log trades
    </button>
    <div class="reset-wrap">
      <button class="reset-btn" id="resetBtn"
        title="Clear the dashboard display. Your DB history is kept.">🔄 Reset</button>
      <button class="reset-btn reset-full" id="resetFullBtn"
        title="Clear dashboard AND delete ALL recommendations from the database. Cannot be undone.">🗑️</button>
    </div>
  </div>
</div>
<div class="workflow-hint">
  💡 Sunday routine: Export 3 files from Bank Discount → select all 3 at once in the file picker → UPLOAD &amp; RUN → Review → Approve or Reject &nbsp;|&nbsp; (1) התיק שלי → Excel &nbsp;|&nbsp; (2) ביצועים היסטוריים → Excel &nbsp;|&nbsp; (3) הוראות → טאב הוראות → Excel
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
    <div class="stat" id="ta35Card" style="transition:border-color .3s,box-shadow .3s"><div class="stat-label">TA-35 Today</div><div class="stat-value" id="statTA35Change">—</div><div class="stat-sub" id="statTA35Signal" style="font-size:.68rem">Loading...</div></div>
  </div>

  <!-- Pending Limit Orders panel -->
  <div id="pendingOrdersPanel" class="panel" style="margin-bottom:16px;display:none">
    <div class="panel-header">
      <div class="panel-title">⏳ Open Limit Orders</div>
      <div class="panel-badge" id="pendingOrdersCount">0 open</div>
      <span style="font-size:.72rem;color:var(--muted);margin-left:8px">Update portfolio.yaml before running if orders executed this week</span>
    </div>
    <div class="panel-body" id="pendingOrdersBody"></div>
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
        <select id="batchSelect" class="batch-select" style="display:none" title="Switch between past recommendation runs"></select>
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

    <!-- Track Record + Run Log -->
    <div style="display:flex;flex-direction:column;gap:16px">

      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">📈 Track Record</div>
          <div class="panel-badge" id="trackBadge">30 days</div>
        </div>
        <div id="trackBody">
          <div class="empty" style="padding:20px">
            <div class="icon">📊</div>No history yet — approve/reject recommendations to build track record
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">🎯 Quality Tracker</div>
          <div class="panel-badge" id="qualityBadge">all recs</div>
        </div>
        <div id="qualityBody">
          <div class="empty" style="padding:20px">
            <div class="icon">🎯</div>No scored recommendations yet — run the pipeline to populate
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header"><div class="panel-title">📋 Run Log</div><div class="panel-badge" id="logBadge">live</div></div>
        <div class="log-wrap" id="logWrap">
          <div class="log-line"><span class="log-time">--:--:--</span><span class="log-msg">Waiting for demo run...</span></div>
        </div>
      </div>

    </div>

  </div>

</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<script src="/static/dashboard.js?v=1.4.10"></script>
</body>
</html>"""


# ── API routes ────────────────────────────────────────────────────────────────


def _read_xlsx_headers(path: str) -> list[str]:
    """Read column headers from the first substantive row of an xlsx file.

    Scans up to the first 15 rows and returns the headers from the first row
    that contains at least 3 non-empty cells.

    Args:
        path: Absolute or relative path to the xlsx file.

    Returns:
        List of header strings, or empty list if none found.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    # Bank Discount files have 1-2 title rows before the actual headers. Return
    # the row with the most non-empty cells within the first 10 rows — that row
    # is always the actual header row, not a title or account-number row.
    best: list[str] = []
    for row in ws.iter_rows(max_row=10, values_only=True):
        headers = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if len(headers) > len(best):
            best = headers
    wb.close()
    return best


def detect_file_type(columns: list[str]) -> str:
    """Detect Bank Discount export type from column headers.

    Args:
        columns: List of header strings from the first sheet.

    Returns:
        One of: 'portfolio', 'history', 'orders', 'unknown'.
    """
    # Order matters: orders must be checked before history because the orders
    # file also contains 'סוג פעולה' and 'שער ביצוע'. Use columns unique to
    # each file type only.
    cols = set(c.strip() for c in columns)
    if any("הגבלת שער" in c or c == "סטטוס" for c in cols):
        return "orders"
    if any("כמות מבוצעת" in c for c in cols):
        return "history"
    if any("אחוז נייר מהתיק" in c or "שינוי יומי" in c for c in cols):
        return "portfolio"
    return "unknown"


def add_log(msg: str, typ: str = "info") -> None:
    """Append a timestamped entry to the in-memory run log and emit to logger.

    Args:
        msg: Human-readable log message.
        typ: Log level tag shown in the dashboard UI ('info', 'ok', 'warn', 'err').
    """
    now = datetime.now().strftime("%H:%M:%S")
    state["run_log"].append({"time": now, "msg": msg, "type": typ})
    logger.info("[%s] %s", now, msg)


def run_demo_background(
    portfolio: PortfolioSnapshot | None = None,
    tx_path: str | None = None,
    orders_path: str | None = None,
    uploaded_names: dict[str, str] | None = None,
) -> None:
    """Run the full demo pipeline in a background thread.

    Generates OHLCV data for the TA-125 universe, computes quant signals,
    calls the Claude API for recommendations, renders the HTML report, and
    attempts email delivery. Updates the shared `state` dict throughout so
    the dashboard can poll progress.

    Args:
        portfolio: Pre-loaded portfolio snapshot (e.g. from a Bank Discount
            upload). If None, loads from portfolio.yaml via load_portfolio().
        tx_path: Optional path to a Bank Discount transaction history xlsx.
            If provided, transaction context is injected into Claude's prompt.
        orders_path: Optional path to a Bank Discount open orders xlsx
            (הוראות וביצועים → טאב הוראות). If provided, pending orders are
            parsed and written to portfolio.yaml before the run, so Claude sees
            the current open limit orders.
        uploaded_names: Optional dict mapping file type to original filename
            (e.g. {'portfolio': 'התיק_שלי.xlsx', 'history': 'ביצועים.xlsx'}).
            Used to show detected filenames in the run log.
    """
    try:
        state["is_running"] = True
        state["run_log"] = []

        if portfolio is None:
            add_log("Loading portfolio from portfolio.yaml...", "info")
            portfolio = load_portfolio()
            state["portfolio"] = portfolio.model_dump(mode="json")
            add_log(
                f"Portfolio loaded — ₪{portfolio.total_value_ils:,.0f} | {len(portfolio.holdings)} holdings",
                "ok",
            )
        else:
            state["portfolio"] = portfolio.model_dump(mode="json")
            portfolio_label = (uploaded_names or {}).get("portfolio", "uploaded file")
            add_log(
                f"Portfolio detected: {portfolio_label} ({len(portfolio.holdings)} holdings)",
                "ok",
            )

        tx_context = ""
        if tx_path:
            try:
                from src.utils.transaction_parser import (
                    parse_transaction_history,
                    format_transactions_for_prompt,
                )
                transactions = parse_transaction_history(tx_path)
                tx_context = format_transactions_for_prompt(transactions)
                history_label = (uploaded_names or {}).get("history") or Path(tx_path).name
                add_log(f"History detected: {history_label} ({len(transactions)} transactions)", "ok")
                try:
                    from src.utils.trade_matcher import match_and_log_trades
                    match_result = match_and_log_trades(transactions, lookback_days=14)
                    if match_result["matched"] > 0:
                        add_log(f"Auto-logged {match_result['matched']} executed trades from history", "ok")
                        for line in match_result["log_lines"]:
                            add_log(line, "ok")
                    else:
                        add_log("Trade matching: no new executed trades found", "info")
                except Exception as me:
                    add_log(f"Trade matching skipped: {me}", "warn")
            except Exception as e:
                add_log(f"Transaction history skipped: {e}", "warn")

        if orders_path:
            try:
                from src.utils.orders_parser import parse_open_orders
                import yaml as _yaml
                import tempfile
                parsed_orders = parse_open_orders(orders_path)
                _yaml_path = Path(__file__).resolve().parent / "portfolio.yaml"
                with open(_yaml_path, encoding="utf-8") as _f:
                    _yaml_data = _yaml.safe_load(_f)
                _yaml_data["pending_orders"] = parsed_orders
                _tmp_fd, _tmp_path = tempfile.mkstemp(dir=_yaml_path.parent, suffix=".tmp")
                try:
                    with os.fdopen(_tmp_fd, "w", encoding="utf-8") as _f:
                        _yaml.dump(_yaml_data, _f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                    os.replace(_tmp_path, str(_yaml_path))
                except Exception:
                    os.unlink(_tmp_path)
                    raise
                orders_label = (uploaded_names or {}).get("orders") or Path(orders_path).name
                if parsed_orders:
                    add_log(
                        f"Orders detected: {orders_label} ({len(parsed_orders)} open orders)",
                        "ok",
                    )
                else:
                    add_log(f"Orders detected: {orders_label} — no pending orders found", "info")
            except Exception as e:
                add_log(f"Open orders parse skipped: {e}", "warn")

        live_prices_context = ""
        add_log("Fetching live prices for TA-125 universe...", "info")
        try:
            from src.connectors.live_prices import fetch_live_prices, format_live_prices_for_prompt
            from demo_run import load_universes
            stock_tickers = load_universes().get("ta125", [])
            live_prices = fetch_live_prices(stock_tickers)
            live_prices_context = format_live_prices_for_prompt(live_prices, portfolio.holdings)
            add_log(f"Live prices: {len(live_prices)}/{len(stock_tickers)} tickers fetched", "ok")
        except Exception as e:
            live_prices_context = ""
            add_log(f"Live prices skipped: {e}", "warn")

        add_log("Fetching real OHLCV data for TA-125 universe...", "info")
        from demo_run import ALL_TICKERS, WATCHLIST_TICKERS, fetch_real_ohlcv
        all_tickers = ALL_TICKERS + WATCHLIST_TICKERS
        ohlcv_data: dict = {}
        real_count = 0
        for t in all_tickers:
            bars = fetch_real_ohlcv(t)
            ohlcv_data[t] = bars
            if bars and bars[0].get("source") != "mock":
                real_count += 1
        add_log(f"{real_count}/{len(all_tickers)} tickers with real OHLCV data", "ok")

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

        from src.connectors.macro_connector import fetch_live_macro
        macro = fetch_live_macro()
        state["macro"] = macro.model_dump(mode="json")
        add_log(f"Macro: BOI {macro.boi_interest_rate}% | USD/ILS {macro.usd_ils_rate}", "ok")

        # Inject performance history into Claude context
        performance_text = ""
        try:
            from src.db.recommendations_db import (
                init_recommendations_table,
                get_performance_summary,
                format_for_prompt,
            )
            init_recommendations_table()
            perf_summary = get_performance_summary(days=30)
            performance_text = format_for_prompt(perf_summary)
            if perf_summary:
                add_log(f"Performance history: {perf_summary.total_recs} past recs, {perf_summary.success_rate}% success", "ok")
        except Exception:
            pass  # silent — first run or DB unavailable

        add_log("Calling Claude Opus 4 — this takes ~20 seconds...", "warn")
        index_perf = {"ta35": {"change_pct": 0.82}, "ta125": {"change_pct": 0.61}}
        # No hardcoded news — placeholder items with today's timestamp were
        # biasing Claude toward TEVA/ESLT/banks on every run regardless of date.
        # Real news integration is backlog item #6 (Maya API earnings calendar).
        news: list = []
        # Pre-fetch macro extras for log visibility (generate_report fetches them too)
        try:
            from src.connectors.macro_connector import fetch_dividend_calendar, fetch_usdils_momentum
            _fx = fetch_usdils_momentum()
            if _fx:
                add_log(
                    f"USD/ILS 30d trend: {_fx.get('trend','N/A')} ({_fx.get('change_30d_pct',0):+.2f}%)",
                    "ok",
                )
            _stock_tickers = [h.ticker for h in portfolio.holdings if not h.ticker.isdigit()]
            _divs = fetch_dividend_calendar(_stock_tickers)
            add_log(f"Dividend calendar: {len(_divs)} ex-div events in next 30 days", "ok")
        except Exception as _me:
            add_log(f"Macro extras pre-fetch skipped: {_me}", "warn")

        agent = PortfolioAgent()
        report, usage = agent.generate_report(
            portfolio=portfolio, signals=signals, macro=macro,
            index_perf=index_perf, news_chunks=news, run_type="morning",
            performance_text=performance_text,
            transaction_context=tx_context,
            live_prices_context=live_prices_context,
        )
        state["report"] = report.model_dump(mode="json")
        add_log(
            f"Claude done! {len(report.recommendations)} recommendations | "
            f"{usage['prompt_tokens'] + usage['completion_tokens']:,} tokens | {usage['duration_s']}s",
            "ok",
        )

        # Step 5b — save recommendations to DB and embed rec_ids into state for approve/reject
        add_log("Attempting DB save...", "info")
        try:
            from src.db.recommendations_db import init_recommendations_table, save_recommendation
            from src.models.recommendation import RecommendationRecord
            init_recommendations_table()
            held_prices = {h.ticker: h.current_price for h in portfolio.holdings}
            saved_count = 0
            for i, rec in enumerate(report.recommendations):
                entry_price = held_prices.get(rec.ticker)
                record = RecommendationRecord(
                    symbol=rec.ticker,
                    action=rec.action.value,
                    conviction=rec.conviction.value,
                    thesis=rec.thesis,
                    key_risk=rec.key_risk,
                    price_entry=entry_price,
                    price_target=Decimal(str(rec.price_target_ils)) if rec.price_target_ils else None,
                    run_type="morning",
                )
                rec_id = save_recommendation(record)
                logger.debug("save_recommendation(%s) → rec_id=%s", rec.ticker, rec_id)
                if rec_id:
                    saved_count += 1
                    state["report"]["recommendations"][i]["rec_id"] = rec_id
                    state["report"]["recommendations"][i]["status"] = "pending"
            add_log(f"{saved_count} recommendations saved to DB", "ok")
        except Exception as e:
            add_log(f"DB save skipped: {e}", "warn")

        # Step 5c — run snapshots
        try:
            from src.snapshot_runner import run_snapshots
            result = run_snapshots()
            if result["processed"] > 0:
                add_log(f"{result['processed']} snapshots recorded", "ok")
        except Exception:
            pass  # silent — DB may not be running

        # Step 5d — score all recommendations for the Quality Tracker
        try:
            from src.price_updater import update_all_prices, update_fund_prices_from_portfolio
            qresult = update_all_prices(verbose=False)
            add_log(
                f"Quality scorer: {qresult['scored']} recs scored, {qresult['failed']} failed",
                "ok" if qresult["failed"] == 0 else "warn",
            )
            if qresult.get("retry", 0) > 0:
                add_log(f"Price retry: {qresult['retry']} tickers retried", "info")
            fund_updates = update_fund_prices_from_portfolio(portfolio.holdings)
            add_log(f"Fund prices scored: {fund_updates} recs updated from portfolio upload", "ok")
        except Exception as qe:
            add_log(f"Quality scorer skipped: {qe}", "warn")

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
def index():
    """Serve the main dashboard HTML page."""
    import time
    from flask import make_response

    html = render_template_string(DASHBOARD_HTML)
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "-1"
    resp.headers["ETag"] = "v-tx-history-1"
    return resp


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


@app.route("/api/upload", methods=["POST"])
def api_upload() -> Response:
    """Accept Bank Discount xlsx uploads, auto-detect type, and start the pipeline.

    Accepts either a multi-file ``files`` field (new path) or legacy separate
    fields ``file`` / ``tx_file`` / ``orders_file`` (backward-compat).  For
    multi-file uploads each file is typed via ``detect_file_type()`` before
    being routed to the correct parser.

    Returns:
        JSON with 'started' bool, optional 'reason' on failure, and
        'detection' summary dict mapping type → original filename.
    """
    if state["is_running"]:
        return jsonify({"started": False, "reason": "already running"})

    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)

    portfolio_path: str | None = None
    tx_path: str | None = None
    orders_path: str | None = None
    detection_summary: dict[str, str] = {}
    unknown_files: list[str] = []

    multi_files = request.files.getlist("files")
    if multi_files:
        # New path: detect type from xlsx headers for each uploaded file.
        from werkzeug.utils import secure_filename
        for f in multi_files:
            if not f.filename:
                continue
            safe_name = secure_filename(f.filename)
            if not safe_name:
                unknown_files.append(f.filename)
                continue
            save_path = str(uploads_dir / safe_name)
            f.save(save_path)
            try:
                headers = _read_xlsx_headers(save_path)
                file_type = detect_file_type(headers)
            except Exception:
                file_type = "unknown"

            if file_type == "portfolio" and portfolio_path is None:
                portfolio_path = save_path
                detection_summary["portfolio"] = f.filename
            elif file_type == "history" and tx_path is None:
                tx_path = save_path
                detection_summary["history"] = f.filename
            elif file_type == "orders" and orders_path is None:
                orders_path = save_path
                detection_summary["orders"] = f.filename
            else:
                unknown_files.append(f.filename)
    else:
        # Legacy path: separate named fields from older client versions.
        lf = request.files.get("file")
        if lf and lf.filename:
            save_path = str(uploads_dir / lf.filename)
            lf.save(save_path)
            portfolio_path = save_path
            detection_summary["portfolio"] = lf.filename

        tf = request.files.get("tx_file")
        if tf and tf.filename:
            tx_save_path = str(uploads_dir / f"tx_{tf.filename}")
            tf.save(tx_save_path)
            tx_path = tx_save_path
            detection_summary["history"] = tf.filename

        of = request.files.get("orders_file")
        if of and of.filename:
            orders_save_path = str(uploads_dir / f"orders_{of.filename}")
            of.save(orders_save_path)
            orders_path = orders_save_path
            detection_summary["orders"] = of.filename

    if not portfolio_path:
        return jsonify({
            "started": False,
            "reason": (
                "No portfolio file detected. "
                "Upload the Bank Discount 'התיק שלי' Excel export."
            ),
            "detection": detection_summary,
            "unknown": unknown_files,
        }), 400

    try:
        from src.utils.discount_parser import parse_discount_export
        portfolio = parse_discount_export(portfolio_path)
    except Exception as exc:
        return jsonify({"started": False, "reason": str(exc)}), 400

    t = threading.Thread(
        target=run_demo_background,
        kwargs={
            "portfolio": portfolio,
            "tx_path": tx_path,
            "orders_path": orders_path,
            "uploaded_names": detection_summary,
        },
        daemon=True,
    )
    t.start()
    return jsonify({
        "started": True,
        "detection": detection_summary,
        "unknown": unknown_files,
    })


@app.route("/api/approve", methods=["POST"])
def api_approve() -> Response:
    """Record the user's approve or reject decision for a recommendation.

    Expects JSON body with:
        rec_id (int): DB id of the recommendation.
        approved (bool): True to approve, False to reject.
        actual_price (float | None): Price the trade was executed at.
        quantity (int | None): Units bought/sold.
        note (str | None): Optional free-text note.

    Returns:
        JSON with 'ok' bool and optional 'reason' on failure.
    """
    body = request.get_json(silent=True) or {}
    rec_id = body.get("rec_id")
    approved = body.get("approved")
    if rec_id is None or approved is None:
        return jsonify({"ok": False, "reason": "missing rec_id or approved"}), 400
    try:
        from src.db.recommendations_db import update_approval
        from src.models.recommendation import ApprovalUpdate
        update_approval(ApprovalUpdate(
            rec_id=rec_id,
            approved=approved,
            actual_price=body.get("actual_price"),
            quantity=body.get("quantity"),
            note=body.get("note") or "",
        ))
        return jsonify({"ok": True})
    except Exception as e:
        logger.warning("api_approve failed: %s", e)
        return jsonify({"ok": False, "reason": str(e)}), 500


@app.route("/api/pending_orders")
def api_pending_orders() -> Response:
    """Return pending limit orders from portfolio.yaml.

    Returns:
        JSON with keys: ok (bool), orders (list), count (int).
    """
    try:
        from src.utils.portfolio_loader import load_pending_orders
        orders = load_pending_orders()
        return jsonify({"ok": True, "orders": orders, "count": len(orders)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "orders": []})


@app.route("/api/recommendations")
def api_recommendations() -> Response:
    """Return all recommendations from the SQLite DB ordered by id ascending.

    Returns:
        JSON list of dicts with keys: id, symbol, action, conviction,
        run_type, created_at, price_entry.
    """
    try:
        import sqlite3
        from src.db.recommendations_db import get_connection
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT id, symbol, action, conviction, thesis, key_risk,
                       run_type, created_at, price_entry, price_target,
                       approved, approval_note
                FROM recommendations
                ORDER BY id ASC
            """)
            rows = []
            for r in cur.fetchall():
                row = dict(r)
                approved_val = row.pop("approved")
                note = row.pop("approval_note") or ""
                if approved_val is None:
                    row["status"] = "pending"
                elif approved_val == 1:
                    row["status"] = "approved"
                elif "superseded" in note:
                    row["status"] = "superseded"
                else:
                    row["status"] = "rejected"
                rows.append(row)
        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/batches")
def api_batches() -> Response:
    """Return the last 5 distinct recommendation batches grouped by minute.

    Groups recommendations by minute-truncated created_at and returns the 5
    most recent batches with count and a human-readable label.

    Returns:
        JSON list of dicts with keys: batch_ts (str), count (int), label (str).
    """
    try:
        import sqlite3
        from src.db.recommendations_db import get_connection
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT strftime('%Y-%m-%d %H:%M', created_at) AS batch_ts,
                       COUNT(*) AS count
                FROM recommendations
                GROUP BY batch_ts
                ORDER BY batch_ts DESC
                LIMIT 5
            """)
            rows = []
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            for r in cur.fetchall():
                batch_ts = r["batch_ts"]
                count = r["count"]
                dt = datetime.strptime(batch_ts, "%Y-%m-%d %H:%M")
                label = (
                    f"{days[dt.weekday()]} {dt.day} {months[dt.month - 1]} "
                    f"{batch_ts[11:16]} \u2014 {count} recs"
                )
                rows.append({"batch_ts": batch_ts, "count": count, "label": label})
        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/performance")
def api_performance() -> Response:
    """Return a normalized performance summary for the Track Record panel.

    Queries the last 30 days of closed recommendations and maps the
    PerformanceSummary fields to the names expected by the dashboard JS.

    Returns:
        JSON with 'ok' bool and 'summary' dict (or None if no data).
    """
    try:
        from src.db.recommendations_db import get_performance_summary, get_snapshot_scorecard
        summary = get_performance_summary(days=30)
        scorecard = get_snapshot_scorecard()
        if not summary:
            return jsonify({"ok": True, "summary": None, "scorecard": scorecard})
        closed = max((summary.approved_recs or 0) - (summary.open_positions or 0), 0)
        pending = max((summary.total_recs or 0) - (summary.approved_recs or 0), 0)
        return jsonify({"ok": True, "scorecard": scorecard, "summary": {
            "total_closed":      closed,
            "wins":              summary.successful_recs or 0,
            "avg_return_pct":    summary.avg_return_pct,
            "avg_alpha_pct":     summary.avg_alpha,
            "high_conv_success": summary.high_conv_success,
            "med_conv_success":  summary.med_conv_success,
            "low_conv_success":  summary.low_conv_success,
            "pending":           pending,
            "open_approved":     summary.open_positions or 0,
        }})
    except Exception as e:
        logger.warning("api_performance failed: %s", e)
        return jsonify({"ok": False, "summary": None, "scorecard": [], "error": str(e)})


@app.route("/health")
def health() -> tuple[Response, int]:
    """Return a simple health-check response for load balancer probes."""
    return jsonify({"status": "ok"}), 200


@app.route("/api/reset", methods=["POST"])
def api_reset() -> Response:
    """Reset in-memory state and optionally clear DB recommendations."""
    clear_db = request.json.get("clear_db", False) if request.json else False
    state["portfolio"] = None
    state["signals"] = []
    state["report"] = None
    state["last_run"] = None
    state["is_running"] = False
    state["run_log"] = []
    state["macro"] = None
    if clear_db:
        try:
            from src.db.recommendations_db import get_connection
            conn = get_connection()
            with conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM recommendation_snapshots")
                cur.execute("DELETE FROM recommendations")
            conn.close()
        except Exception as e:
            return jsonify({"ok": True, "db_cleared": False, "db_error": str(e)})
        return jsonify({"ok": True, "db_cleared": True})
    return jsonify({"ok": True, "db_cleared": False})


@app.route("/api/quality")
def api_quality() -> Response:
    """Return quality-scoring stats aggregated for the Quality Tracker panel.

    Returns per-batch hit rates, per-action breakdown, and overall totals
    for all recommendations that have been scored (last_scored_at IS NOT NULL).

    Returns:
        JSON with keys: overall, by_action, by_batch, scored_at.
    """
    try:
        import sqlite3
        from src.db.recommendations_db import get_connection
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Overall totals — all scored recs
            cur.execute("""
                SELECT
                    COUNT(*)                                                    AS total,
                    SUM(CASE WHEN direction_correct = 1 THEN 1 ELSE 0 END)     AS correct,
                    SUM(CASE WHEN direction_correct = 0 THEN 1 ELSE 0 END)     AS wrong,
                    ROUND(AVG(current_return_pct), 2)                          AS avg_return,
                    ROUND(AVG(net_return_pct), 2)                              AS avg_net_return,
                    ROUND(AVG(transaction_cost_pct), 2)                        AS avg_tx_cost,
                    MAX(last_scored_at)                                        AS last_scored_at
                FROM recommendations
                WHERE last_scored_at IS NOT NULL
                  AND direction_correct IS NOT NULL
            """)
            overall_row = dict(cur.fetchone() or {})
            total    = overall_row.get("total") or 0
            correct  = overall_row.get("correct") or 0
            hit_rate = round(correct / total * 100, 1) if total > 0 else None
            overall = {
                "total":          total,
                "correct":        correct,
                "wrong":          overall_row.get("wrong") or 0,
                "hit_rate":       hit_rate,
                "avg_return":     overall_row.get("avg_return"),
                "avg_net_return": overall_row.get("avg_net_return"),
                "avg_tx_cost":    overall_row.get("avg_tx_cost"),
                "last_scored_at": overall_row.get("last_scored_at"),
            }

            # Per-action breakdown
            cur.execute("""
                SELECT
                    action,
                    COUNT(*)                                                AS total,
                    SUM(CASE WHEN direction_correct = 1 THEN 1 ELSE 0 END) AS correct,
                    ROUND(AVG(current_return_pct), 2)                      AS avg_return,
                    ROUND(AVG(net_return_pct), 2)                          AS avg_net_return
                FROM recommendations
                WHERE last_scored_at IS NOT NULL
                  AND direction_correct IS NOT NULL
                GROUP BY action
                ORDER BY action
            """)
            by_action = []
            for row in cur.fetchall():
                row = dict(row)
                n = row["total"] or 0
                c = row["correct"] or 0
                row["hit_rate"] = round(c / n * 100, 1) if n > 0 else None
                by_action.append(row)

            # Per-batch breakdown (group by minute prefix of created_at)
            cur.execute("""
                SELECT
                    SUBSTR(created_at, 1, 16)                               AS batch,
                    COUNT(*)                                                 AS total,
                    SUM(CASE WHEN direction_correct = 1 THEN 1 ELSE 0 END)  AS correct,
                    ROUND(AVG(current_return_pct), 2)                       AS avg_return,
                    ROUND(AVG(net_return_pct), 2)                           AS avg_net_return
                FROM recommendations
                WHERE last_scored_at IS NOT NULL
                  AND direction_correct IS NOT NULL
                GROUP BY SUBSTR(created_at, 1, 16)
                ORDER BY batch DESC
                LIMIT 10
            """)
            by_batch = []
            for row in cur.fetchall():
                row = dict(row)
                n = row["total"] or 0
                c = row["correct"] or 0
                row["hit_rate"] = round(c / n * 100, 1) if n > 0 else None
                by_batch.append(row)

            # WATCH — hypothetical BUY missed opportunity tracker (separate from main hit rate)
            cur.execute("""
                SELECT symbol, action, conviction, price_entry,
                       current_price, current_return_pct, direction_correct,
                       CASE
                           WHEN approved IS NULL THEN 'pending'
                           WHEN approved = 1     THEN 'approved'
                           WHEN approval_note LIKE '%superseded%' THEN 'superseded'
                           ELSE 'rejected'
                       END AS status
                FROM recommendations
                WHERE action = 'WATCH'
                  AND direction_correct IS NOT NULL
                ORDER BY id ASC
            """)
            watch_recs = [dict(r) for r in cur.fetchall()]
            watch_total   = len(watch_recs)
            watch_correct = sum(1 for r in watch_recs if r["direction_correct"] == 1)
            watch_stats = {
                "total":    watch_total,
                "correct":  watch_correct,
                "wrong":    watch_total - watch_correct,
                "hit_rate": round(watch_correct / watch_total * 100, 1) if watch_total > 0 else 0,
                "recs":     watch_recs,
            }

        return jsonify({
            "ok": True,
            "overall":     overall,
            "by_action":   by_action,
            "by_batch":    by_batch,
            "watch_stats": watch_stats,
        })
    except Exception as exc:
        logger.warning("api_quality failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/ta35")
def api_ta35() -> Response:
    """Return live TA-35 % change with Red Day DCA signal.

    Fetches the last 5 trading days for TA35.TA from Yahoo Finance and computes
    the return as previous close vs current close. Using the last two actual
    TASE trading sessions is correct regardless of Sunday/holiday gaps — it
    never compares against a stale open price from a prior session.

    Returns:
        JSON with current, prev_close, change_pct, is_red_day,
        is_green_day, is_open, signal, and updated_at fields.
    """
    try:
        import pytz
        import yfinance as yf

        ist = pytz.timezone("Asia/Jerusalem")
        now_ist = datetime.now(ist)
        weekday = now_ist.weekday()   # 0=Mon … 6=Sun
        hour = now_ist.hour + now_ist.minute / 60.0

        if weekday < 4:        # Mon–Thu
            is_open = 9.98 <= hour <= 17.23
        elif weekday == 4:     # Fri
            is_open = 9.98 <= hour <= 13.57
        else:                  # Sat–Sun
            is_open = False

        hist = yf.Ticker("TA35.TA").history(period="5d")
        if hist.empty or len(hist) < 2:
            return jsonify({"ok": False, "error": "No data"})

        # Compare last two actual TASE trading sessions (prev close → current
        # close). This is correct regardless of Sunday/holiday gaps — the open
        # price approach was wrong because Yahoo Finance returns Friday's open
        # on Monday, inflating the apparent gain by ~2%.
        current    = round(float(hist["Close"].iloc[-1]), 2)
        prev_close = round(float(hist["Close"].iloc[-2]), 2)
        change_pct = round((current - prev_close) / prev_close * 100, 2)

        is_red_day   = change_pct <= -0.3
        is_green_day = change_pct >=  0.3

        if is_red_day:
            signal = "🔴 RED DAY — Deploy ₪10,000 into הראל מחקה (5130661)"
        elif is_green_day:
            signal = "🟢 Green day — wait"
        else:
            signal = "⚪ Flat — wait"

        return jsonify({
            "ok":           True,
            "current":      current,
            "prev_close":   prev_close,
            "change_pct":   change_pct,
            "is_red_day":   is_red_day,
            "is_green_day": is_green_day,
            "is_open":      is_open,
            "signal":       signal,
            "updated_at":   now_ist.strftime("%H:%M IST"),
        })
    except Exception as exc:
        logger.warning("api_ta35 failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/state")
def api_state() -> Response:
    """Return the current in-memory agent state as JSON for dashboard polling.

    Enriches recommendation status live from DB so approve/reject actions
    made outside the dashboard (e.g. via approve.py CLI) are reflected.
    """
    import copy
    report = copy.deepcopy(state["report"]) if state["report"] else None
    if report and report.get("recommendations"):
        try:
            import sqlite3
            from src.db.recommendations_db import get_connection
            with get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT id,
                        CASE
                            WHEN approved IS NULL THEN 'pending'
                            WHEN approved = 1     THEN 'approved'
                            WHEN approval_note LIKE '%superseded%' THEN 'superseded'
                            ELSE 'rejected'
                        END AS status,
                        price_actual, qty_actual, executed_at
                    FROM recommendations
                """)
                db_rows = {
                    row["id"]: {
                        "status":       row["status"],
                        "price_actual": row["price_actual"],
                        "qty_actual":   row["qty_actual"],
                        "executed_at":  row["executed_at"],
                    }
                    for row in cur.fetchall()
                }
            for rec in report["recommendations"]:
                rec_id = rec.get("rec_id")
                if rec_id and rec_id in db_rows:
                    row_data = db_rows[rec_id]
                    rec["status"]       = row_data["status"]
                    rec["price_actual"] = row_data["price_actual"]
                    rec["qty_actual"]   = row_data["qty_actual"]
                    rec["executed_at"]  = row_data["executed_at"]
                elif not rec.get("status"):
                    rec["status"] = "pending"
        except Exception as exc:
            logger.warning("Status enrichment failed: %s", exc)
    return jsonify({
        "is_running": state["is_running"],
        "portfolio":  state["portfolio"],
        "signals":    state["signals"],
        "report":     report,
        "macro":      state["macro"],
        "last_run":   state["last_run"],
        "run_log":    state["run_log"],
    })


@app.route("/api/upload_history", methods=["POST"])
def api_upload_history() -> Response:
    """Standalone Monday-evening endpoint — upload ביצועים היסטוריים to auto-log executed trades.

    Accepts a single xlsx file, parses it with transaction_parser, runs
    trade_matcher against approved recommendations, and returns a JSON summary.
    No full pipeline run is triggered — purely for execution reconciliation.

    Returns:
        JSON with ``ok`` bool, plus ``matched``, ``skipped``, ``log_lines``
        on success, or ``error`` on failure.
    """
    try:
        f = request.files.get("tx_file")
        if not f:
            return jsonify({"ok": False, "error": "No file provided"})
        Path("uploads").mkdir(exist_ok=True)
        path = f"uploads/tx_autolog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        f.save(path)
        from src.utils.transaction_parser import parse_transaction_history
        from src.utils.trade_matcher import match_and_log_trades
        transactions = parse_transaction_history(path)
        result = match_and_log_trades(transactions, lookback_days=14)
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning("api_upload_history failed: %s", e)
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/ai-analyze", methods=["POST"])
def api_ai_analyze() -> Response:
    """Run a 6-step Claude analysis on a single recommendation.

    Accepts a JSON body with a ``rec`` dict containing the recommendation
    fields. Calls the Claude API server-side (API key never exposed to client)
    and returns the structured analysis text.

    Returns:
        JSON with ``ok`` bool and either ``text`` (analysis) or ``error``.
    """
    rec = (request.json or {}).get("rec", {})
    ticker_label = rec.get("ticker", "?")
    action = rec.get("action", "?")
    conviction = rec.get("conviction", "?")
    thesis = rec.get("thesis", "")
    key_risk = rec.get("key_risk", "")
    pos_pct = rec.get("suggested_position_pct", 0)
    signals = ", ".join(rec.get("supporting_signals") or []) or "none"
    target = rec.get("price_target_ils")
    target_str = f"₪{float(target):.2f}" if target else "not specified"

    prompt = (
        f"You are a senior Israeli portfolio manager. Evaluate this AI-generated "
        f"recommendation using a concise 6-step framework.\n\n"
        f"Recommendation: {action} {ticker_label} ({conviction} conviction)\n"
        f"Thesis: {thesis}\n"
        f"Key Risk: {key_risk}\n"
        f"Suggested Position: {pos_pct}% of portfolio\n"
        f"Price Target: {target_str}\n"
        f"Supporting Signals: {signals}\n\n"
        f"Provide a structured 6-step evaluation (max 2 sentences each):\n"
        f"1. Signal Strength (1-5): rate and explain\n"
        f"2. Thesis Quality (1-5): rate and explain\n"
        f"3. Portfolio Fit (1-5): rate and explain\n"
        f"4. Risk Assessment (1-5): rate and explain\n"
        f"5. Conviction Calibration: is {conviction} appropriate given the data?\n"
        f"6. Final Recommendation: APPROVE / REJECT / WATCH with one-sentence rationale"
    )
    try:
        import anthropic
        from src.utils.config import get_config
        client = anthropic.Anthropic(api_key=get_config().anthropic_api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return jsonify({"ok": True, "text": msg.content[0].text})
    except Exception as exc:
        logger.warning("api_ai_analyze failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)})


def preload_last_state() -> None:
    """Pre-populate state with the last saved recommendation batch from the DB.

    Reads the most recent batch via get_latest_batch() and reconstructs a
    minimal report dict so the dashboard shows recommendations immediately on
    startup without requiring a new run.
    """
    try:
        from src.db.recommendations_db import get_latest_batch
        batch_prefix, rows = get_latest_batch()
        if not rows:
            return
        # Reconstruct a report dict compatible with the dashboard renderer.
        recs = []
        for r in rows:
            approved_val = r.get("approved")
            note = r.get("approval_note") or ""
            if approved_val is None:
                status = "pending"
            elif approved_val == 1:
                status = "approved"
            elif "superseded" in note:
                status = "superseded"
            else:
                status = "rejected"
            recs.append({
                "rec_id": r.get("id"),
                "status": status,
                "ticker": r.get("symbol", ""),
                "action": r.get("action", ""),
                "conviction": r.get("conviction", ""),
                "thesis": r.get("thesis", ""),
                "key_risk": r.get("key_risk", ""),
                "suggested_position_pct": 0.0,
                "supporting_signals": [],
                "price_target_ils": r.get("price_target"),
            })
        state["report"] = {
            "report_time": batch_prefix,
            "run_type": rows[0].get("run_type", "morning") if rows else "morning",
            "market_summary": f"Loaded from last run ({batch_prefix}). Run again for a fresh analysis.",
            "macro_outlook": "",
            "portfolio_risk_flags": [],
            "recommendations": recs,
        }
        state["last_run"] = batch_prefix
        state["run_log"] = [{
            "time": batch_prefix[11:16],
            "msg": f"Auto-loaded {len(rows)} recommendations from last run ({batch_prefix})",
            "type": "ok",
        }]
        print(f"  [OK] Pre-loaded {len(rows)} recommendations from {batch_prefix}")
    except Exception as exc:
        print(f"  [WARN] Could not pre-load last run: {exc}")


if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  Portfolio Agent - Web Dashboard")
    print("=" * 55)
    print()
    from src.db.recommendations_db import init_recommendations_table, init_snapshots_table
    from src.recommendation_scorer import score_recommendations
    init_recommendations_table()   # also runs _migrate_quality_columns() + _migrate_scorer_columns()
    init_snapshots_table()
    preload_last_state()
    scorer_result = score_recommendations()
    print(
        f"  [Scorer] 7d: {scorer_result['scored_7d']} scored | "
        f"30d: {scorer_result['scored_30d']} scored | "
        f"skipped: {scorer_result['skipped']} | errors: {scorer_result['errors']}"
    )
    print()
    print("  Opening at: http://localhost:5000")
    print()
    print("  1. Click 'RUN DEMO' in the dashboard")
    print("  2. Watch the live log as Claude thinks")
    print("  3. See recommendations populate in real-time")
    print()
    print("  Press Ctrl+C to stop the server")
    print("=" * 55 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)

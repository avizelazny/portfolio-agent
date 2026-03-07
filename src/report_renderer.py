import smtplib, os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from jinja2 import Template
from src.models.report import RecommendationReport, Action, Conviction
from src.utils.config import get_config

ACTION_COLORS={Action.BUY:"#16a34a",Action.SELL:"#dc2626",Action.HOLD:"#d97706",Action.WATCH:"#2563eb"}
CONVICTION_COLORS={Conviction.HIGH:"#16a34a",Conviction.MEDIUM:"#d97706",Conviction.LOW:"#6b7280"}

TMPL="""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Portfolio Report</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;color:#1e293b;line-height:1.6}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px}
.hdr{background:linear-gradient(135deg,#1e3a5f,#2563eb);color:#fff;padding:28px;border-radius:12px;margin-bottom:20px}
.hdr h1{font-size:1.5rem;font-weight:700;margin-bottom:4px}
.hdr .sub{opacity:.85;font-size:.9rem}
.local{display:inline-block;background:#f0fdf4;border:1px solid #86efac;color:#166534;padding:4px 12px;border-radius:20px;font-size:.75rem;margin-bottom:16px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.card{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);text-align:center}
.card .n{font-size:2rem;font-weight:700}.card .l{font-size:.75rem;color:#64748b;text-transform:uppercase}
.g .n{color:#16a34a}.r .n{color:#dc2626}.b .n{color:#2563eb}.a .n{color:#d97706}
.sec{background:#fff;border-radius:10px;padding:22px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:18px}
.sec h2{font-size:1rem;font-weight:700;color:#1e3a5f;border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-bottom:14px}
.flag{display:inline-block;background:#fef3c7;color:#92400e;border:1px solid #fcd34d;padding:3px 9px;border-radius:20px;font-size:.78rem;margin:3px}
.rec{border:1px solid #e2e8f0;border-radius:10px;padding:18px;margin-bottom:14px}
.rh{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.badge{padding:4px 12px;border-radius:20px;font-size:.8rem;font-weight:700;color:#fff}
.tkr{font-size:1.15rem;font-weight:800;color:#1e3a5f}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.clbl{font-size:.76rem;color:#64748b}
.ppct{margin-left:auto;font-size:.88rem;color:#475569;font-weight:600}
.thesis{color:#334155;font-size:.9rem;margin-bottom:8px}
.risk{background:#fff7ed;border-left:3px solid #f97316;padding:7px 11px;border-radius:0 6px 6px 0;font-size:.83rem;color:#9a3412;margin-bottom:8px}
.sigs{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.stag{background:#f1f5f9;color:#475569;padding:2px 8px;border-radius:12px;font-size:.74rem}
.tgt{font-size:.8rem;color:#16a34a;font-weight:600;margin-top:5px}
.foot{text-align:center;color:#94a3b8;font-size:.76rem;margin-top:20px;padding:14px}
</style></head><body><div class="wrap">
<div class="hdr"><h1>📊 Portfolio Analysis Report</h1>
<div class="sub">{{ report.run_type|title }} · {{ date_str }} · Claude Opus 4 · Local Mode</div></div>
<div class="local">🖥️ Running locally — view emails at <strong>http://localhost:8025</strong></div>
<div class="cards">
<div class="card g"><div class="n">{{ report.buys()|length }}</div><div class="l">Buys</div></div>
<div class="card r"><div class="n">{{ report.sells()|length }}</div><div class="l">Sells</div></div>
<div class="card a"><div class="n">{{ report.holds()|length }}</div><div class="l">Holds</div></div>
<div class="card b"><div class="n">{{ report.high_conviction()|length }}</div><div class="l">High Conv.</div></div>
</div>
<div class="sec"><h2>🌍 Market & Macro</h2>
<p style="color:#334155;font-size:.93rem"><strong>Market:</strong> {{ report.market_summary }}</p><br>
<p style="color:#334155;font-size:.93rem"><strong>Macro:</strong> {{ report.macro_outlook }}</p></div>
{% if report.portfolio_risk_flags %}
<div class="sec"><h2>⚠️ Risk Flags</h2>
{% for f in report.portfolio_risk_flags %}<span class="flag">{{ f }}</span>{% endfor %}</div>{% endif %}
<div class="sec"><h2>📋 Recommendations ({{ report.recommendations|length }})</h2>
{% for rec in report.recommendations %}
<div class="rec">
<div class="rh">
<span class="badge" style="background:{{ ac[rec.action] }}">{{ rec.action.value }}</span>
<span class="tkr">{{ rec.ticker }}</span>
<span class="dot" style="background:{{ cc[rec.conviction] }}"></span>
<span class="clbl">{{ rec.conviction.value }}</span>
{% if rec.suggested_position_pct > 0 %}<span class="ppct">{{ rec.suggested_position_pct }}% of portfolio</span>{% endif %}
</div>
<p class="thesis">{{ rec.thesis }}</p>
<div class="risk"><strong>Key Risk:</strong> {{ rec.key_risk }}</div>
{% if rec.price_target_ils %}<div class="tgt">🎯 Target: ₪{{ "%.2f"|format(rec.price_target_ils) }}</div>{% endif %}
{% if rec.supporting_signals %}<div class="sigs">{% for s in rec.supporting_signals %}<span class="stag">{{ s }}</span>{% endfor %}</div>{% endif %}
</div>{% endfor %}</div>
<div class="foot">AI-generated · Not financial advice · Review before acting · {{ date_str }}</div>
</div></body></html>"""

def render_html_report(report: RecommendationReport, report_id: str) -> str:
    return Template(TMPL).render(
        report=report, date_str=report.report_time.strftime("%Y-%m-%d %H:%M"),
        ac=ACTION_COLORS, cc=CONVICTION_COLORS, report_id=report_id,
    )

def save_report_locally(html: str, report: RecommendationReport) -> str:
    date = report.report_time.strftime("%Y-%m-%d")
    time = report.report_time.strftime("%H%M")
    out = Path("reports") / date
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{report.run_type}_{time}.html"
    path.write_text(html, encoding="utf-8")
    print(f"\n  📄 Report saved: {path.resolve()}")
    print(f"     Open that file in your browser to view the full report!\n")
    return str(path.resolve())

def send_email_report(html: str, report: RecommendationReport):
    cfg = get_config()
    subject = (f"{'🌅' if report.run_type=='morning' else '🌆'} Portfolio Report "
               f"{report.report_time.strftime('%d %b %Y')} — "
               f"{len(report.buys())} Buys, {len(report.sells())} Sells")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.ses_sender_email
    msg["To"] = cfg.report_recipient_email
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP(cfg.email_host, cfg.email_port) as s:
            s.sendmail(cfg.ses_sender_email, [cfg.report_recipient_email], msg.as_string())
        print(f"  📧 Email sent! View at: http://localhost:8025")
    except Exception as e:
        print(f"  ⚠️  Email skipped (MailHog not running): {e}")

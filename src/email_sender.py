"""
Portfolio Report Email Sender
==============================
Sends HTML portfolio reports via Gmail SMTP using App Password stored in AWS Secrets Manager.
Run standalone or called from demo_run.py / agent_core.py.

Requirements:
- Gmail App Password stored in Secrets Manager as portfolio-agent/gmail-app-password
- REPORT_EMAIL env var set to your Gmail address
- IAM role with secretsmanager:GetSecretValue permission
"""

import os
import boto3
import logging
import smtplib
import json
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
AWS_REGION                = os.getenv("AWS_REGION", "il-central-1")
REPORT_EMAIL              = os.getenv("REPORT_EMAIL", "")
MOCK_MODE                 = os.getenv("EMAIL_MOCK", "true").lower() == "true"
GMAIL_APP_PASSWORD_SECRET = "portfolio-agent/gmail-app-password"


def _get_gmail_password() -> str:
    """Fetch Gmail App Password from AWS Secrets Manager."""
    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    response = client.get_secret_value(SecretId=GMAIL_APP_PASSWORD_SECRET)
    secret = response.get("SecretString", "")
    try:
        return json.loads(secret).get("password", secret)
    except Exception:
        return secret.strip()


def send_report(
    subject: str,
    html_body: str,
    to_email: str = None,
) -> bool:
    """
    Send an HTML portfolio report via Gmail SMTP.
    Returns True on success, False on failure.
    """
    recipient = to_email or REPORT_EMAIL
    if not recipient:
        logger.error("No recipient email configured. Set REPORT_EMAIL env var")
        return False

    if MOCK_MODE:
        logger.info(f"[MOCK] Would send email to {recipient}: {subject}")
        print(f"\n📧 [MOCK EMAIL]")
        print(f"   To:      {recipient}")
        print(f"   Subject: {subject}")
        print(f"   Body:    {len(html_body)} chars of HTML")
        return True

    try:
        app_password = _get_gmail_password()

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = REPORT_EMAIL
        msg["To"]      = recipient
        msg.attach(MIMEText(f"Portfolio Report — {subject}\n\nPlease view in HTML email client.", "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(REPORT_EMAIL, app_password)
            server.sendmail(REPORT_EMAIL, recipient, msg.as_string())

        logger.info(f"Email sent to {recipient} via Gmail SMTP")
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        print(f"   ❌ Email failed: {e}")
        return False


def build_report_html(portfolio, signals, run_time: str = None) -> str:
    """Build a clean HTML email body from portfolio and signals data."""
    run_time = run_time or datetime.now().strftime("%Y-%m-%d %H:%M")

    signal_rows = ""
    if signals:
        for s in signals:
            action    = s.get("action", "HOLD")
            symbol    = s.get("symbol", "")
            name      = s.get("name", "")
            conf      = s.get("confidence", "")
            rationale = s.get("rationale", "")[:120]

            color = {"BUY": "#10b981", "SELL": "#ef4444", "HOLD": "#64748b"}.get(action, "#64748b")
            signal_rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #1e2d40">
                    <span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold">{action}</span>
                </td>
                <td style="padding:8px;border-bottom:1px solid #1e2d40;font-weight:bold">{symbol}</td>
                <td style="padding:8px;border-bottom:1px solid #1e2d40;color:#94a3b8">{name}</td>
                <td style="padding:8px;border-bottom:1px solid #1e2d40">{conf}</td>
                <td style="padding:8px;border-bottom:1px solid #1e2d40;color:#94a3b8;font-size:13px">{rationale}...</td>
            </tr>"""
    else:
        signal_rows = '<tr><td colspan="5" style="padding:16px;text-align:center;color:#64748b">No signals generated</td></tr>'

    total_val = f"₪{portfolio.get('total_value', 0):,.0f}"   if portfolio else "N/A"
    total_pnl = f"₪{portfolio.get('total_pnl', 0):+,.0f}"   if portfolio else "N/A"
    pnl_pct   = f"{portfolio.get('total_pnl_pct', 0):+.2f}%" if portfolio else "N/A"
    cash      = f"₪{portfolio.get('cash_balance', 0):,.0f}"  if portfolio else "N/A"

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0a0e17;font-family:Arial,sans-serif;color:#e2e8f0">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:700px;margin:0 auto">
    <tr><td style="background:#111827;padding:24px 32px;border-bottom:2px solid #00d4aa">
      <h1 style="margin:0;color:#00d4aa;font-size:22px">📊 Portfolio Agent Report</h1>
      <p style="margin:4px 0 0;color:#64748b;font-size:13px">{run_time} IST</p>
    </td></tr>
    <tr><td style="padding:24px 32px">
      <h2 style="color:#e2e8f0;font-size:16px;margin:0 0 16px">Portfolio Summary</h2>
      <table width="100%" cellpadding="0" cellspacing="8">
        <tr>
          <td style="background:#131c2e;border-radius:8px;padding:16px;text-align:center;width:25%">
            <div style="color:#64748b;font-size:11px;text-transform:uppercase">Total Value</div>
            <div style="color:#00d4aa;font-size:20px;font-weight:bold;margin-top:4px">{total_val}</div>
          </td>
          <td width="8"></td>
          <td style="background:#131c2e;border-radius:8px;padding:16px;text-align:center;width:25%">
            <div style="color:#64748b;font-size:11px;text-transform:uppercase">Unrealized P&L</div>
            <div style="color:#10b981;font-size:20px;font-weight:bold;margin-top:4px">{total_pnl}</div>
          </td>
          <td width="8"></td>
          <td style="background:#131c2e;border-radius:8px;padding:16px;text-align:center;width:25%">
            <div style="color:#64748b;font-size:11px;text-transform:uppercase">Return</div>
            <div style="color:#10b981;font-size:20px;font-weight:bold;margin-top:4px">{pnl_pct}</div>
          </td>
          <td width="8"></td>
          <td style="background:#131c2e;border-radius:8px;padding:16px;text-align:center;width:25%">
            <div style="color:#64748b;font-size:11px;text-transform:uppercase">Cash</div>
            <div style="color:#e2e8f0;font-size:20px;font-weight:bold;margin-top:4px">{cash}</div>
          </td>
        </tr>
      </table>
    </td></tr>
    <tr><td style="padding:0 32px 24px">
      <h2 style="color:#e2e8f0;font-size:16px;margin:0 0 16px">AI Recommendations</h2>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:8px;overflow:hidden;background:#131c2e">
        <tr style="background:#1e2d40">
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:12px;text-transform:uppercase">Action</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:12px;text-transform:uppercase">Symbol</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:12px;text-transform:uppercase">Name</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:12px;text-transform:uppercase">Confidence</th>
          <th style="padding:10px 8px;text-align:left;color:#64748b;font-size:12px;text-transform:uppercase">Rationale</th>
        </tr>
        {signal_rows}
      </table>
    </td></tr>
    <tr><td style="padding:16px 32px 32px;border-top:1px solid #1e2d40">
      <p style="margin:0;color:#475569;font-size:12px;text-align:center">
        Portfolio Agent • Powered by Claude Opus • All trades require your manual approval<br>
        This is not financial advice. Past performance does not guarantee future results.
      </p>
    </td></tr>
  </table>
</body>
</html>"""


if __name__ == "__main__":
    print("Testing email sender (MOCK MODE)...")
    mock_portfolio = {
        "total_value":   136430,
        "total_pnl":     6800,
        "total_pnl_pct": 5.61,
        "cash_balance":  8450,
    }
    mock_signals = [
        {"action": "BUY",  "symbol": "TEVA", "name": "Teva Pharmaceutical", "confidence": "High",   "rationale": "RSI oversold, strong volume surge"},
        {"action": "HOLD", "symbol": "NICE", "name": "NICE Systems",         "confidence": "Medium", "rationale": "Momentum positive but approaching 52-week high"},
        {"action": "SELL", "symbol": "MGDL", "name": "Migdal Insurance",     "confidence": "Medium", "rationale": "MACD bearish crossover, volume declining"},
    ]
    html = build_report_html(mock_portfolio, mock_signals)
    result = send_report(
        subject   = f"Portfolio Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        html_body = html,
        to_email  = "test@example.com",
    )
    print(f"\n✅ Email sender test {'passed' if result else 'failed'}!")

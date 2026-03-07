# 📊 Portfolio Agent

An autonomous AI-powered Israeli stock portfolio management agent built with **Claude Opus 4**, TASE data, and Bank Discount API.

Runs twice daily (9:30 AM + 4:00 PM IST, Mon–Fri), generates buy/sell/hold recommendations with full reasoning. You approve all trades.

---

## 🚀 Quick Start

### Prerequisites
- Python 3.12
- Anthropic API key from [console.anthropic.com](https://console.anthropic.com)

### 1. Clone and configure
```bash
git clone https://github.com/avizelazny/portfolio-agent.git
cd portfolio-agent
cp .env.example .env
# Set ANTHROPIC_API_KEY in .env
```

### 2. Install dependencies
```bash
py -3.12 -m pip install -r requirements.txt
```

### 3. Launch dashboard
```bash
py -3.12 dashboard.py
```

Open **http://localhost:5000** and click **▶ RUN DEMO**

---

## 🏗️ Architecture

EventBridge (9:30AM + 4:00PM IST) triggers ECS Fargate task which:
1. Fetches TASE live quotes + OHLCV for all TA-125 stocks
2. Fetches Bank Discount portfolio positions + cash balance
3. Pulls BOI macro data (rate, CPI, USD/ILS)
4. Scrapes news (Globes, Calcalist, TheMarker) → embeds with Voyage AI
5. Runs Quant Engine (RSI, MACD, momentum, P/E vs sector, 52w position)
6. Calls Claude Opus 4 with full context package
7. Renders HTML report → sends via SES + updates dashboard

---

## 📁 Project Structure

```
portfolio-agent/
├── dashboard.py          # Flask web dashboard (main entry)
├── demo_run.py           # Headless CLI demo
├── docker-compose.yml    # Local: Postgres, Redis, MinIO, MailHog
├── requirements.txt
├── .env.example
└── src/
    ├── agent_core.py     # Claude Opus 4 integration
    ├── quant_engine.py   # RSI, MACD, momentum signals
    ├── report_renderer.py
    ├── models/
    │   ├── market.py
    │   └── report.py
    └── utils/
        └── config.py
```

---

## 🔢 Quant Signals (Composite Score -1 to +1)

| Signal | Weight |
|---|---|
| RSI-14 | 20% |
| MACD(12/26/9) | 20% |
| Momentum-20d | 20% |
| Volume z-score | 15% |
| P/E vs sector | 15% |
| 52-week position | 10% |

---

## ⚠️ Disclaimer

AI-generated analysis only. Not financial advice. Always review before acting.

## 📄 License

MIT

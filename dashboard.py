"""
dashboard.py — Polymarket Arbitrage Bot Dashboard
Open Chrome: http://localhost:8050
Auto-refreshes every 10 seconds.
"""

import json
import os
import yaml
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def load_portfolio():
    path = "logs/paper_portfolio.json"
    if not os.path.exists(path):
        return {
            "balance_usdc":     20.0,
            "starting_balance": 20.0,
            "open_bets":        [],
            "closed_bets":      [],
            "total_pnl":        0.0,
            "wins":             0,
            "losses":           0,
            "bets_placed":      0,
        }
    with open(path) as f:
        try:
            return json.load(f)
        except Exception:
            return load_portfolio.__wrapped__()   # shouldn't happen


def load_decisions(n=10):
    path = "logs/decisions.json"
    if not os.path.exists(path):
        return []
    with open(path) as f:
        try:
            decisions = json.load(f)
            return list(reversed(decisions))[:n]
        except Exception:
            return []


def load_log_lines(n=30):
    path = "logs/trades.log"
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.readlines()
    return [l.strip() for l in lines[-n:]]


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_graph(history, start_bal, ceiling):
    if len(history) < 2:
        return '<div style="color:#555;text-align:center;padding:40px;font-size:13px">Graph appears after first trade...</div>'

    W, H, PAD = 800, 200, 30
    balances = [h["b"] for h in history]
    times    = [h["t"] for h in history]

    min_b = min(balances) * 0.97
    max_b = max(max(balances) * 1.03, ceiling)

    def sx(i):
        return PAD + (i / max(1, len(balances) - 1)) * (W - PAD * 2)

    def sy(b):
        return H - PAD - ((b - min_b) / max(0.01, max_b - min_b)) * (H - PAD * 2)

    # Build polyline points
    points = " ".join(f"{sx(i):.1f},{sy(b):.1f}" for i, b in enumerate(balances))

    # Fill path (area under line)
    fill_path = (
        f"M {sx(0):.1f},{sy(balances[0]):.1f} "
        + " ".join(f"L {sx(i):.1f},{sy(b):.1f}" for i, b in enumerate(balances))
        + f" L {sx(len(balances)-1):.1f},{H-PAD} L {sx(0):.1f},{H-PAD} Z"
    )

    # Ceiling and start lines
    ceil_y  = sy(ceiling)
    start_y = sy(start_bal)

    # Current value label
    cur_bal = balances[-1]
    cur_color = "#2ecc71" if cur_bal >= start_bal else "#e74c3c"
    cur_x = sx(len(balances) - 1)
    cur_y = sy(cur_bal)

    # First/last time labels
    t_start = times[0][11:16] if len(times[0]) > 11 else ""
    t_end   = times[-1][11:16] if len(times[-1]) > 11 else ""

    return f"""
<svg viewBox="0 0 {W} {H}" style="width:100%;height:200px;display:block">
  <defs>
    <linearGradient id="fillGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{cur_color}" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="{cur_color}" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  <!-- Grid lines -->
  <line x1="{PAD}" y1="{ceil_y:.1f}"  x2="{W-PAD}" y2="{ceil_y:.1f}"  stroke="#e74c3c" stroke-width="1" stroke-dasharray="4,4" opacity="0.5"/>
  <line x1="{PAD}" y1="{start_y:.1f}" x2="{W-PAD}" y2="{start_y:.1f}" stroke="#f1c40f" stroke-width="1" stroke-dasharray="4,4" opacity="0.5"/>
  <!-- Labels -->
  <text x="{W-PAD+4}" y="{ceil_y+4:.1f}"  fill="#e74c3c" font-size="10">${ceiling:.0f}</text>
  <text x="{W-PAD+4}" y="{start_y+4:.1f}" fill="#f1c40f" font-size="10">${start_bal:.0f}</text>
  <!-- Area fill -->
  <path d="{fill_path}" fill="url(#fillGrad)"/>
  <!-- Line -->
  <polyline points="{points}" fill="none" stroke="{cur_color}" stroke-width="2"/>
  <!-- Current dot -->
  <circle cx="{cur_x:.1f}" cy="{cur_y:.1f}" r="4" fill="{cur_color}"/>
  <text x="{min(cur_x+6, W-60):.1f}" y="{cur_y-6:.1f}" fill="{cur_color}" font-size="12" font-weight="bold">${cur_bal:.2f}</text>
  <!-- Time labels -->
  <text x="{PAD}" y="{H-4}" fill="#444" font-size="10">{t_start}</text>
  <text x="{W-PAD-20}" y="{H-4}" fill="#444" font-size="10">{t_end}</text>
</svg>"""


def build_html():
    config    = load_config()
    portfolio = load_portfolio()
    decisions = load_decisions()
    logs      = load_log_lines()
    now       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    mode       = "PAPER TRADING" if config.get("paper_trading") else "LIVE TRADING"
    mode_color = "#f1c40f" if config.get("paper_trading") else "#e74c3c"

    balance   = portfolio["balance_usdc"]
    start_bal = portfolio["starting_balance"]
    pnl       = round(portfolio["total_pnl"], 2)
    pnl_pct   = round(pnl / start_bal * 100, 1) if start_bal else 0
    pnl_color = "#2ecc71" if pnl >= 0 else "#e74c3c"

    wins      = portfolio["wins"]
    losses    = portfolio["losses"]
    win_rate  = round(wins / max(1, wins + losses) * 100, 1)
    wr_color  = "#2ecc71" if win_rate >= 50 else "#e74c3c"

    ceiling   = config.get("ceiling_balance", 100)
    kill      = config.get("kill_switch_balance", 10)
    ceil_pct  = min((balance / ceiling) * 100, 100)
    ceil_color = "#2ecc71" if balance < ceiling * 0.8 else "#f1c40f"

    open_bets   = portfolio.get("open_bets", [])
    closed_bets = list(reversed(portfolio.get("closed_bets", [])))[:8]

    # --- Build SVG graph data ---
    history = portfolio.get("balance_history", [])
    graph_svg = _build_graph(history, start_bal, config.get("ceiling_balance", 120))

    # --- Open bets rows ---
    open_rows = ""
    for bet in open_bets:
        d_color = "#2ecc71" if bet["direction"] == "YES" else "#e74c3c"
        open_rows += f"""
        <tr>
          <td style="color:{d_color};font-weight:600">{bet['direction']}</td>
          <td style="color:#00d4ff">${bet['amount_usdc']:.2f}</td>
          <td>{bet['entry_odds']*100:.1f}%</td>
          <td style="color:#f1c40f">${bet['potential_payout']:.2f}</td>
          <td style="color:#888">{bet['opened_at'][:16]}</td>
          <td style="color:#aaa;font-size:11px">{bet['market_question'][:50]}</td>
        </tr>"""
    if not open_rows:
        open_rows = '<tr><td colspan="6" style="color:#555;text-align:center;padding:12px">No open bets</td></tr>'

    # --- Closed bets rows ---
    closed_rows = ""
    for bet in closed_bets:
        won       = bet.get("status") == "WIN"
        pnl_b     = bet.get("pnl_usdc", 0)
        pnl_c_b   = "#2ecc71" if won else "#e74c3c"
        d_color   = "#2ecc71" if bet["direction"] == "YES" else "#e74c3c"
        result_lbl = "WIN" if won else "LOSS"
        closed_rows += f"""
        <tr>
          <td style="color:#888">{bet.get('closed_at','')[:16]}</td>
          <td style="color:{d_color};font-weight:600">{bet['direction']}</td>
          <td style="color:#00d4ff">${bet['amount_usdc']:.2f}</td>
          <td>{bet['entry_odds']*100:.1f}%</td>
          <td style="color:{pnl_c_b};font-weight:600">{result_lbl} {'+' if pnl_b>=0 else ''}{pnl_b:.2f}</td>
          <td style="color:#666;font-size:11px">{bet['market_question'][:45]}</td>
        </tr>"""
    if not closed_rows:
        closed_rows = '<tr><td colspan="6" style="color:#555;text-align:center;padding:12px">No closed bets yet</td></tr>'

    # --- Decision rows ---
    dec_rows = ""
    for d in decisions[:8]:
        act = d.get("action", "?")
        act_color = (
            "#2ecc71" if act in ("BET_YES",)
            else "#e74c3c" if act in ("BET_NO",)
            else "#f1c40f"
        )
        btc = d.get("btc_price", 0)
        edge = d.get("edge", 0)
        conf = d.get("confidence", 0)
        dec_rows += f"""
        <tr>
          <td style="color:#888">{d.get('timestamp','')[:16]}</td>
          <td><span style="color:{act_color};font-weight:600">{act}</span></td>
          <td style="color:#00d4ff">${btc:,.0f}</td>
          <td>{f'{edge*100:+.1f}%' if edge else '-'}</td>
          <td>{f'{conf*100:.0f}%' if conf else '-'}</td>
          <td style="color:#aaa;font-size:11px">{d.get('reasoning', d.get('reason',''))[:50]}</td>
        </tr>"""

    # --- Log rows ---
    log_rows = ""
    for line in reversed(logs):
        if "BET YES" in line or "WIN" in line:
            lc = "#2ecc71"
        elif "BET NO" in line or "LOSS" in line:
            lc = "#e74c3c"
        elif "KILL" in line or "CEILING" in line:
            lc = "#e74c3c"
        elif "PASS" in line or "HOLD" in line:
            lc = "#f1c40f"
        elif "RESOLVED" in line:
            lc = "#a855f7"
        else:
            lc = "#555"
        log_rows += (
            f'<div style="color:{lc};font-family:monospace;font-size:11px;'
            f'padding:3px 0;border-bottom:1px solid #111">'
            f'{line[:130]}</div>'
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<title>Polymarket Arb Bot</title>
<meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0a0a0a; color:#ccc; font-family:-apple-system,sans-serif; padding:20px; }}
  .topbar {{ display:flex; align-items:center; justify-content:space-between;
             margin-bottom:20px; padding-bottom:16px; border-bottom:1px solid #1e1e1e; }}
  h1 {{ font-size:22px; font-weight:500; color:#fff; }}
  .badge {{ padding:4px 12px; border-radius:20px; font-size:12px; font-weight:500; }}
  .running {{ background:#0d3320; color:#2ecc71; border:1px solid #2ecc71; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
            gap:12px; margin-bottom:20px; }}
  .card {{ background:#111; border:1px solid #1e1e1e; border-radius:10px; padding:14px; }}
  .card-label {{ font-size:11px; color:#555; text-transform:uppercase;
                 letter-spacing:0.08em; margin-bottom:6px; }}
  .card-value {{ font-size:24px; font-weight:600; color:#fff; }}
  .card-sub {{ font-size:12px; color:#666; margin-top:4px; }}
  .section {{ background:#111; border:1px solid #1e1e1e; border-radius:10px;
              padding:16px; margin-bottom:16px; }}
  .section-title {{ font-size:11px; font-weight:500; color:#555;
                    text-transform:uppercase; letter-spacing:0.08em; margin-bottom:12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; color:#444; font-size:11px; text-transform:uppercase;
        padding:6px 10px; border-bottom:1px solid #1a1a1a; }}
  td {{ padding:8px 10px; border-bottom:1px solid #141414; color:#bbb; vertical-align:top; }}
  .progress-bg {{ background:#1a1a1a; border-radius:4px; height:6px; margin-top:8px; }}
  .progress-fill {{ height:6px; border-radius:4px; transition:width 0.5s; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
</style>
</head>
<body>

<div class="topbar">
  <div>
    <h1>Polymarket Latency Arb Bot</h1>
    <div style="font-size:12px;color:#555;margin-top:4px">Last updated: {now}</div>
  </div>
  <div style="display:flex;gap:10px;align-items:center">
    <span class="badge running">RUNNING</span>
    <span class="badge" style="background:#1a1400;color:{mode_color};border:1px solid {mode_color}">{mode}</span>
  </div>
</div>

<div class="cards">
  <div class="card">
    <div class="card-label">Balance</div>
    <div class="card-value" style="color:#2ecc71">${balance:.2f}</div>
    <div class="card-sub">started at ${start_bal:.2f}</div>
    <div class="progress-bg">
      <div class="progress-fill" style="width:{ceil_pct:.1f}%;background:{ceil_color}"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Total P&L</div>
    <div class="card-value" style="color:{pnl_color}">{'+' if pnl>=0 else ''}{pnl:.2f}</div>
    <div class="card-sub" style="color:{pnl_color}">{pnl_pct:+.1f}%</div>
  </div>
  <div class="card">
    <div class="card-label">Win Rate</div>
    <div class="card-value" style="color:{wr_color}">{win_rate}%</div>
    <div class="card-sub">{wins}W / {losses}L</div>
  </div>
  <div class="card">
    <div class="card-label">Open Bets</div>
    <div class="card-value">{len(open_bets)}</div>
    <div class="card-sub">total placed: {portfolio['bets_placed']}</div>
  </div>
  <div class="card">
    <div class="card-label">Kill / Ceiling</div>
    <div class="card-value" style="font-size:16px;color:#f1c40f">
      ${kill:.0f} / <span style="color:#e74c3c">${ceiling:.0f}</span>
    </div>
    <div class="card-sub">floor / profit lock</div>
  </div>
</div>

<div class="section" style="margin-bottom:16px">
  <div class="section-title">Portfolio Value — Live Chart
    <span style="float:right;color:#444;font-weight:normal">
      <span style="color:#e74c3c">— ceiling $120</span> &nbsp;
      <span style="color:#f1c40f">— start $50</span>
    </span>
  </div>
  {graph_svg}
</div>

<div class="grid2">
  <div class="section">
    <div class="section-title">Open Bets</div>
    <table>
      <tr><th>Dir</th><th>Amount</th><th>Odds</th><th>Payout</th><th>Opened</th><th>Market</th></tr>
      {open_rows}
    </table>
  </div>
  <div class="section">
    <div class="section-title">Closed Bets (last 8)</div>
    <table>
      <tr><th>Closed</th><th>Dir</th><th>Bet</th><th>Odds</th><th>Result</th><th>Market</th></tr>
      {closed_rows}
    </table>
  </div>
</div>

<div class="section">
  <div class="section-title">Claude AI Decisions (last 8)</div>
  <table>
    <tr><th>Time</th><th>Action</th><th>BTC Price</th><th>Edge</th><th>Conf</th><th>Reasoning</th></tr>
    {dec_rows}
  </table>
</div>

<div class="section">
  <div class="section-title">Trade Log</div>
  {log_rows if log_rows else '<div style="color:#555;font-size:13px">No activity yet...</div>'}
</div>

<div style="text-align:center;color:#333;font-size:12px;margin-top:16px">
  Auto-refreshes every 10 seconds | Kill: ${kill} | Ceiling: ${ceiling} | Max bet: ${config.get('max_bet_usdc',6)}
</div>

</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = build_html()
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass   # suppress default request logging


def main():
    port   = 8050
    server = HTTPServer(("localhost", port), Handler)
    print(f"""
  Dashboard running!
  Open Chrome: http://localhost:{port}
  Ctrl+C to stop.
""")
    server.serve_forever()


if __name__ == "__main__":
    main()

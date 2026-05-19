#!/usr/bin/env python3
"""
daily_report.py - Freqtrade 일일 운용 리포트

수집:
  - Freqtrade API: /profit, /status, /balance, /daily?timescale=N
  - fee_reconciliation: 백테스트 가정 vs 실제 수수료 차이
  - cost_tracker: Claude API 일일 누적 비용

출력:
  - stdout: 사람이 읽기 좋은 요약
  - file:   user_data/daily_reports/YYYY-MM-DD.md (--no-write 으로 비활성)

Exit code:
  0 - 정상 (알림 없음)
  1 - 알림 임계 위반 (fee 5%↑ / 일일 손실 5%↑ / Claude 하드캡)
  2 - Freqtrade API 접속 실패 (봇이 안 떠있을 가능성)

환경변수:
  FT_API_BASE, FREQTRADE_USERNAME, FREQTRADE_PASSWORD,
  DAILY_LOSS_ALERT_PCT (default 5.0), FEE_DIFF_ALERT_PCT (default 5.0)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

from fee_reconciliation import compute_reconciliation  # noqa: E402

try:
    import telegram_notify  # type: ignore
except Exception:
    telegram_notify = None  # type: ignore

# cost_tracker는 user_data/llm 경로에 있고, 컨테이너 안에서 절대경로로 접근 가능
try:
    from user_data.llm import cost_tracker  # type: ignore
except Exception:
    cost_tracker = None  # type: ignore

API_BASE = os.getenv("FT_API_BASE", "http://localhost:8080/api/v1")
API_USER = os.getenv("FREQTRADE_USERNAME", "")
API_PASS = os.getenv("FREQTRADE_PASSWORD", "")

DAILY_LOSS_ALERT_PCT = float(os.getenv("DAILY_LOSS_ALERT_PCT", "5.0"))
FEE_DIFF_ALERT_PCT = float(os.getenv("FEE_DIFF_ALERT_PCT", "5.0"))


def _api_get(path: str, timeout: float = 5.0) -> Any | None:
    """Freqtrade REST API GET. 실패 시 None."""
    url = f"{API_BASE}{path}"
    headers = {"Accept": "application/json"}
    if API_USER and API_PASS:
        token = b64encode(f"{API_USER}:{API_PASS}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[daily_report] API {path} failed: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"[daily_report] API {path} JSON invalid: {e}", file=sys.stderr)
        return None


def _f(x: Any, n: int = 2) -> str:
    try:
        return f"{float(x):.{n}f}"
    except (TypeError, ValueError):
        return "—"


def _pct(x: Any, scale: str = "ratio", n: int = 2) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if scale == "percent":
        return f"{v:+.{n}f}%"
    return f"{v * 100:+.{n}f}%"


def _build_report(profit, status, balance, daily, fee_recon, cost_state) -> tuple[str, list[str]]:
    """returns (markdown_body, alerts)"""
    alerts: list[str] = []
    L: list[str] = []
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    L.append(f"# Daily Report — {now}")
    L.append("")

    # ---- Profit ----
    L.append("## Profit")
    L.append("")
    if profit:
        prof_today = profit.get("profit_today_abs")
        prof_today_ratio = profit.get("profit_today")  # ratio
        prof_total_abs = profit.get("profit_all_abs", profit.get("profit_closed_coin"))
        prof_total_ratio = profit.get("profit_all", profit.get("profit_closed_ratio_mean"))
        closed_count = profit.get("closed_trade_count")
        latest_dt = profit.get("latest_trade_date")
        L.append("| Metric | Value |")
        L.append("|---|---|")
        L.append(f"| Today P/L | {_pct(prof_today_ratio)} ({_f(prof_today)}) |")
        L.append(f"| Cumulative P/L | {_pct(prof_total_ratio)} ({_f(prof_total_abs)}) |")
        L.append(
            f"| Closed trades (all-time) | {closed_count if closed_count is not None else '—'} |"
        )
        L.append(f"| Latest trade | {latest_dt or '—'} |")
        L.append("")
        # Alert: 일일 손실
        try:
            today_pct = float(prof_today_ratio) * 100  # ratio → percent
            if today_pct < -DAILY_LOSS_ALERT_PCT:
                alerts.append(
                    f"Daily loss exceeded threshold: {today_pct:.2f}% < -{DAILY_LOSS_ALERT_PCT:.1f}%"
                )
        except (TypeError, ValueError):
            pass
    else:
        L.append("_API unreachable — profit data unavailable._")
        L.append("")

    # ---- Open trades ----
    L.append("## Open Trades")
    L.append("")
    if status:
        if not status:
            L.append("_None._")
        else:
            L.append("| Pair | Side | Entry | Current | P/L | Stake |")
            L.append("|---|---|---:|---:|---:|---:|")
            for t in status:
                pair = t.get("pair", "—")
                side = (
                    ("short" if t.get("is_short") else "long")
                    if "is_short" in t
                    else t.get("trade_direction", "—")
                )
                entry = t.get("open_rate", "—")
                cur = t.get("current_rate", "—")
                pl_ratio = t.get("profit_ratio", t.get("current_profit"))
                stake = t.get("stake_amount", "—")
                L.append(
                    f"| {pair} | {side} | {_f(entry, 4)} | {_f(cur, 4)} | {_pct(pl_ratio)} | {_f(stake, 2)} |"
                )
        L.append("")
    else:
        L.append("_API unreachable._")
        L.append("")

    # ---- Daily breakdown ----
    L.append("## Last 7 Days")
    L.append("")
    if daily and isinstance(daily, dict) and daily.get("data"):
        L.append("| Date | Trades | Profit | Profit Abs |")
        L.append("|---|---:|---:|---:|")
        for row in daily["data"]:
            d = row.get("date")
            tc = row.get("trade_count")
            pabs = row.get("abs_profit")
            prel = row.get("rel_profit")  # ratio
            L.append(f"| {d} | {tc if tc is not None else '—'} | {_pct(prel)} | {_f(pabs, 4)} |")
        L.append("")
    else:
        L.append("_No daily breakdown available._")
        L.append("")

    # ---- Fee reconciliation ----
    L.append("## Fee Reconciliation")
    L.append("")
    if fee_recon:
        st = fee_recon.get("status")
        loc = fee_recon["local"]
        L.append(f"- **Status**: `{st}`")
        L.append(f"- Lookback: {fee_recon['lookback_days']} days")
        L.append(
            f"- Local trades / fee: {loc['trades']} / ${_f(loc['fee_usd'], 4)} "
            f"(effective {_f(loc['effective_rate_pct'], 4)}%)"
        )
        if fee_recon.get("real"):
            real = fee_recon["real"]
            L.append(
                f"- Real trades / fee: {real['trades']} / ${_f(real['fee_usd'], 4)} "
                f"(effective {_f(real['effective_rate_pct'], 4)}%)"
            )
        diff = fee_recon.get("diff_pct")
        if diff is not None:
            L.append(f"- Diff: **{diff:+.2f}%** (tolerance ±{fee_recon['tolerance_pct']:.1f}%)")
        if st == "tolerance_exceeded":
            rec = fee_recon.get("recommended_fee")
            alerts.append(f"Fee diff exceeded ({diff:+.2f}%); recommended fee={rec:.6f}")
            L.append(f"- **Action**: update `config.json` fee to `{rec:.6f}`")
        L.append("")
    else:
        L.append("_Not computed._")
        L.append("")

    # ---- Claude cost ----
    L.append("## Claude API Cost (today)")
    L.append("")
    if cost_state:
        cost = cost_state.get("cost_usd", 0.0) or 0.0
        soft = float(os.getenv("CLAUDE_DAILY_COST_USD_MAX", "10.0"))
        hard = float(os.getenv("CLAUDE_DAILY_COST_HARD_STOP", "20.0"))
        calls = cost_state.get("calls", 0)
        inp = cost_state.get("input_tokens", 0)
        outp = cost_state.get("output_tokens", 0)
        L.append(f"- Cost: **${cost:.4f}** ({calls} calls, in={inp:,} / out={outp:,} tokens)")
        L.append(f"- Caps: soft=${soft:.2f} → fallback model, hard=${hard:.2f} → block")
        if cost >= hard:
            alerts.append(f"Claude HARD CAP reached: ${cost:.2f} >= ${hard:.2f}")
            L.append("- **Action**: investigate; raise cap or wait until UTC reset")
        elif cost >= soft:
            L.append("- _Soft cap active — running on fallback model._")
        by_model = cost_state.get("by_model", {})
        if by_model:
            L.append("")
            L.append("| Model | Calls | Cost |")
            L.append("|---|---:|---:|")
            for m, v in by_model.items():
                L.append(f"| {m} | {v.get('calls', 0)} | ${_f(v.get('cost_usd', 0), 4)} |")
        L.append("")
    else:
        L.append("_cost_tracker unavailable (running outside container?)._")
        L.append("")

    # ---- Alerts summary ----
    if alerts:
        L.insert(2, "")
        L.insert(2, "")
        L.insert(2, "\n".join(f"- {a}" for a in alerts))
        L.insert(2, "## ⚠ Alerts")

    return "\n".join(L), alerts


def _telegram_message(profit, alerts: list[str]) -> str:
    """Telegram 푸시용 짧은 요약 메시지."""
    lines: list[str] = []
    if alerts:
        lines.append("🚨 Freqtrade Alerts")
        for a in alerts:
            lines.append(f"- {a}")
    else:
        lines.append("✅ Freqtrade Daily - clean")

    if profit:
        ratio = profit.get("profit_today")
        absv = profit.get("profit_today_abs")
        try:
            today_pct = float(ratio) * 100
            lines.append("")
            lines.append(f"Today P/L: {today_pct:+.2f}% ({absv})")
        except (TypeError, ValueError):
            pass
        try:
            cum_pct = float(profit.get("profit_all", 0)) * 100
            lines.append(f"Cumulative: {cum_pct:+.2f}%")
        except (TypeError, ValueError):
            pass
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--days", type=int, default=7, help="fee reconciliation lookback days (default 7)"
    )
    p.add_argument(
        "--no-write", action="store_true", help="skip writing to user_data/daily_reports/"
    )
    p.add_argument(
        "--out-dir", default="/freqtrade/user_data/daily_reports", help="report output directory"
    )
    p.add_argument(
        "--telegram",
        action="store_true",
        help="push alerts to Telegram (TELEGRAM_TOKEN/CHAT_ID required)",
    )
    p.add_argument(
        "--telegram-always",
        action="store_true",
        help="push to Telegram even when no alerts (implies --telegram)",
    )
    args = p.parse_args()

    profit = _api_get("/profit")
    status = _api_get("/status")
    balance = _api_get("/balance")
    daily = _api_get("/daily?timescale=7")

    api_alive = profit is not None or status is not None
    fee_recon = compute_reconciliation(lookback_days=args.days)
    cost_state = cost_tracker.get_state() if cost_tracker else None

    md, alerts = _build_report(profit, status, balance, daily, fee_recon, cost_state)

    print(md)
    print("")
    if alerts:
        print(f"Alerts: {len(alerts)}", file=sys.stderr)
        for a in alerts:
            print(f"  - {a}", file=sys.stderr)

    if not args.no_write:
        try:
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            out_path = out_dir / f"{today}.md"
            out_path.write_text(md)
            print(f"\nReport written: {out_path}", file=sys.stderr)
        except Exception as e:
            print(f"[warn] write failed: {e}", file=sys.stderr)

    # Telegram push
    want_push = args.telegram or args.telegram_always
    if want_push:
        if telegram_notify is None:
            print("[warn] telegram_notify unavailable", file=sys.stderr)
        elif not telegram_notify.is_configured():
            print("[warn] TELEGRAM_TOKEN/CHAT_ID not set, skipping push", file=sys.stderr)
        elif alerts or args.telegram_always:
            tg_text = _telegram_message(profit, alerts)
            ok = telegram_notify.send(
                tg_text, parse_mode=None, disable_notification=not bool(alerts)
            )
            print(f"[telegram] sent: {ok}", file=sys.stderr)
        else:
            print(
                "[telegram] no alerts, skipping push (use --telegram-always to override)",
                file=sys.stderr,
            )

    if not api_alive:
        return 2
    if alerts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

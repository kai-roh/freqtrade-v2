"""daily_report: 마크다운 빌더 알림 분기, exit code."""

from __future__ import annotations

import importlib
import sys


def _load_dr(monkeypatch, cache_dir):
    monkeypatch.setenv("LLM_CACHE_BASE_DIR", str(cache_dir))
    if "daily_report" in sys.modules:
        del sys.modules["daily_report"]
    import daily_report

    importlib.reload(daily_report)
    return daily_report


def test_build_report_no_data_no_alerts(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    md, alerts = dr._build_report(None, None, None, None, None, None)
    assert "# Daily Report" in md
    assert "_API unreachable" in md
    assert alerts == []


def test_alert_on_daily_loss(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    profit = {
        "profit_today": -0.07,
        "profit_today_abs": -70.0,
        "profit_all": 0.0,
        "profit_all_abs": 0.0,
        "closed_trade_count": 5,
        "latest_trade_date": "2026-05-07",
    }
    md, alerts = dr._build_report(profit, [], None, None, None, None)
    assert any("Daily loss" in a for a in alerts)
    assert "## ⚠ Alerts" in md


def test_no_alert_when_loss_within_threshold(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    profit = {"profit_today": -0.02, "profit_today_abs": -20.0}
    _, alerts = dr._build_report(profit, [], None, None, None, None)
    assert alerts == []


def test_fee_recon_tolerance_exceeded_triggers_alert(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    fee = {
        "lookback_days": 7,
        "tolerance_pct": 5.0,
        "local": {"trades": 10, "fee_usd": 5.0, "effective_rate_pct": 0.06, "notional_usd": 8000.0},
        "real": {"trades": 12, "fee_usd": 8.0, "effective_rate_pct": 0.10},
        "diff_pct": 60.0,
        "recommended_fee": 0.0015,
        "status": "tolerance_exceeded",
    }
    md, alerts = dr._build_report(None, [], None, None, fee, None)
    assert any("Fee diff exceeded" in a for a in alerts)
    assert "0.001500" in md  # recommended fee


def test_claude_hard_cap_alert(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_DAILY_COST_HARD_STOP", "10.0")
    cost = {
        "cost_usd": 12.5,
        "calls": 50,
        "input_tokens": 100_000,
        "output_tokens": 5_000,
        "by_model": {},
    }
    _, alerts = dr._build_report(None, [], None, None, None, cost)
    assert any("HARD CAP" in a for a in alerts)


def test_claude_under_caps_no_alert(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    cost = {"cost_usd": 1.5, "calls": 5, "input_tokens": 1000, "output_tokens": 100, "by_model": {}}
    _, alerts = dr._build_report(None, [], None, None, None, cost)
    assert alerts == []


def test_open_trades_table_renders(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    status = [
        {
            "pair": "BTC/USDT:USDT",
            "is_short": False,
            "open_rate": 60000.0,
            "current_rate": 60500.0,
            "profit_ratio": 0.008,
            "stake_amount": 50.0,
        },
        {
            "pair": "ETH/USDT:USDT",
            "is_short": True,
            "open_rate": 3000.0,
            "current_rate": 2980.0,
            "profit_ratio": 0.007,
            "stake_amount": 50.0,
        },
    ]
    md, _ = dr._build_report(None, status, None, None, None, None)
    assert "BTC/USDT:USDT" in md and "ETH/USDT:USDT" in md
    assert "long" in md and "short" in md


def test_entry_gate_metrics_table_renders(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    metrics = [
        {
            "pair": "SOL/USDT:USDT",
            "gates": {
                "guard_base": {"pass": 120, "rate": 0.4167},
                "long_prediction_ok": {"pass": 30, "rate": 0.1042},
                "short_prediction_ok": {"pass": 45, "rate": 0.1562},
                "final_long": {"pass": 2, "rate": 0.0069},
                "final_short": {"pass": 3, "rate": 0.0104},
            },
        }
    ]
    md, alerts = dr._build_report(None, [], None, None, None, None, metrics)
    assert alerts == []
    assert "## Entry Gate Metrics" in md
    assert (
        "| Pair | PredUse | Pred1 | DI | Fund | Base | L Pred | S Pred | L Final | S Final |" in md
    )
    assert "| SOL | — | — | — | — | 41.7% | 10.4% | 15.6% | 2 | 3 |" in md


def test_pct_helper_ratio_vs_percent(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    assert dr._pct(0.05) == "+5.00%"
    assert dr._pct(5.0, scale="percent") == "+5.00%"
    assert dr._pct(None) == "—"
    assert dr._pct("not-a-number") == "—"


def test_telegram_message_has_alerts(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    profit = {"profit_today": -0.07, "profit_today_abs": -70.0, "profit_all": 0.05}
    msg = dr._telegram_message(profit, ["Daily loss exceeded"])
    assert "🚨" in msg
    assert "Daily loss exceeded" in msg
    assert "-7.00%" in msg
    assert "+5.00%" in msg


def test_telegram_message_clean_when_no_alerts(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    msg = dr._telegram_message({"profit_today": 0.01}, [])
    assert "✅" in msg
    assert "🚨" not in msg


def test_telegram_message_handles_missing_profit(monkeypatch, tmp_path):
    dr = _load_dr(monkeypatch, tmp_path)
    # API 미접속 — profit None
    msg = dr._telegram_message(None, ["alert"])
    assert "🚨" in msg
    assert "alert" in msg

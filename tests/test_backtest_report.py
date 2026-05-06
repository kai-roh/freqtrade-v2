"""backtest_report: 결과 JSON → 마크다운 + Live-Gate exit code."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "backtest_report.py"


def _run(result_path: Path, output: Path, strategy: str = "KaiBaseStrategy"):
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--result", str(result_path),
         "--output", str(output),
         "--strategy", strategy,
         "--timerange", "20260207-20260507"],
        capture_output=True, text=True,
    )


def _pass_payload():
    return {"strategy": {"KaiBaseStrategy": {
        "total_trades": 250,
        "profit_total": 0.183,
        "profit_total_abs": 183.0,
        "sharpe": 1.45, "sortino": 2.10,
        "max_drawdown_account": 0.082,
        "wins": 145, "losses": 95, "draws": 10,
        "profit_mean": 0.0042,
        "backtest_days": 90,
        "starting_balance": 1000.0, "final_balance": 1183.0,
        "results_per_pair": [
            {"key": "BTC/USDT:USDT", "trades": 80,
             "profit_total_pct": 5.2, "profit_total_abs": 52.0, "duration_avg": "0:45:00"},
        ],
        "enter_tag": [
            {"key": "freqai_long", "trades": 130, "profit_total_pct": 12.4},
        ],
        "exit_reason_summary": [
            {"key": "roi", "trades": 110, "profit_total_pct": 14.1},
        ],
    }}}


def test_pass_branch(tmp_path):
    rp = tmp_path / "result.json"
    op = tmp_path / "REPORT.md"
    rp.write_text(json.dumps(_pass_payload()))
    proc = _run(rp, op)
    assert proc.returncode == 0, proc.stderr
    md = op.read_text()
    assert "# Backtest Report" in md
    assert "PASS — eligible for next phase" in md
    assert "+18.30%" in md  # ratio → percent
    assert "Per-Pair Results" in md
    assert "Enter Tags" in md
    assert "Exit Reasons" in md


def test_fail_branch_low_sharpe(tmp_path):
    payload = _pass_payload()
    payload["strategy"]["KaiBaseStrategy"]["sharpe"] = 0.3
    rp = tmp_path / "result.json"; op = tmp_path / "REPORT.md"
    rp.write_text(json.dumps(payload))
    proc = _run(rp, op)
    assert proc.returncode == 1
    assert "FAIL — keep dry-run" in op.read_text()


def test_fail_branch_high_mdd(tmp_path):
    payload = _pass_payload()
    payload["strategy"]["KaiBaseStrategy"]["max_drawdown_account"] = 0.25
    rp = tmp_path / "result.json"; op = tmp_path / "REPORT.md"
    rp.write_text(json.dumps(payload))
    proc = _run(rp, op)
    assert proc.returncode == 1


def test_missing_result_file(tmp_path):
    proc = _run(tmp_path / "nope.json", tmp_path / "REPORT.md")
    assert proc.returncode == 2


def test_invalid_json(tmp_path):
    rp = tmp_path / "result.json"
    rp.write_text("{not valid")
    proc = _run(rp, tmp_path / "REPORT.md")
    assert proc.returncode == 2


def test_falls_back_to_first_strategy_when_named_not_found(tmp_path):
    payload = _pass_payload()
    # rename strategy key
    payload["strategy"]["OtherStrategy"] = payload["strategy"].pop("KaiBaseStrategy")
    rp = tmp_path / "result.json"; op = tmp_path / "REPORT.md"
    rp.write_text(json.dumps(payload))
    proc = _run(rp, op, strategy="KaiBaseStrategy")
    # falls back to OtherStrategy and still passes
    assert proc.returncode == 0
    assert "OtherStrategy" in op.read_text()


def test_per_pair_percent_scale_detection(tmp_path):
    """results_per_pair는 percent(0~100) 단위가 흔함 → 자동 감지."""
    payload = _pass_payload()
    rp = tmp_path / "result.json"; op = tmp_path / "REPORT.md"
    rp.write_text(json.dumps(payload))
    _run(rp, op)
    md = op.read_text()
    # 5.2 percent → +5.20% (ratio로 잘못 해석하면 +520.00%)
    assert "+5.20%" in md
    assert "+520.00%" not in md

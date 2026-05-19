"""fee_reconciliation: 합계 산출, tolerance 분기, 권장 fee 산출."""

from __future__ import annotations

import importlib
import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest


@pytest.fixture
def fr(tmp_path, monkeypatch):
    db = tmp_path / "trades.sqlite"
    monkeypatch.setenv("FT_DB_PATH", str(db))
    import fee_reconciliation as fr

    importlib.reload(fr)
    return fr, db


def _seed(db, rows):
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE trades (
        id INTEGER, pair TEXT, is_open INTEGER, open_date TEXT, close_date TEXT,
        open_rate REAL, close_rate REAL, amount REAL, fee_open REAL, fee_close REAL,
        stake_amount REAL, close_profit_abs REAL
    )""")
    for r in rows:
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r)
    con.commit()
    con.close()


def test_no_db_returns_no_local_trades(fr, monkeypatch, tmp_path):
    mod, _ = fr
    monkeypatch.setenv("FT_DB_PATH", str(tmp_path / "absent.sqlite"))
    importlib.reload(mod)
    out = mod.compute_reconciliation()
    assert out["status"] == "no_local_trades"
    assert out["local"]["trades"] == 0


def test_local_only_no_binance_keys(fr, monkeypatch):
    mod, db = fr
    yest = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed(
        db,
        [
            (1, "BTC/USDT:USDT", 0, yest, yest, 60000.0, 60500.0, 0.01, 0.001, 0.001, 600.0, 5.0),
            (2, "ETH/USDT:USDT", 0, yest, yest, 3000.0, 3050.0, 0.5, 0.001, 0.001, 1500.0, 25.0),
        ],
    )
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    out = mod.compute_reconciliation()
    assert out["status"] == "no_binance_data"
    assert out["local"]["trades"] == 2
    assert out["local"]["fee_usd"] > 0
    assert out["real"] is None


def test_open_trades_are_skipped(fr):
    mod, db = fr
    yest = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed(
        db,
        [
            (1, "BTC/USDT:USDT", 1, yest, yest, 60000.0, 60500.0, 0.01, 0.001, 0.001, 600.0, 0.0),
        ],
    )
    out = mod.compute_reconciliation()
    assert out["local"]["trades"] == 0


def test_tolerance_within_threshold(fr, monkeypatch):
    mod, db = fr
    yest = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed(
        db,
        [
            (1, "BTC/USDT:USDT", 0, yest, yest, 60000.0, 60500.0, 0.01, 0.001, 0.001, 600.0, 5.0),
        ],
    )
    # local fee: (open+close) * amount = (60000+60500)*0.01 = 1205
    #            *0.002 / 2 = 1.205
    # 실제값을 local과 거의 같게 → diff < 5%
    monkeypatch.setattr(
        mod, "fetch_binance_actual_fees", lambda *a, **k: [{"fee": {"cost": 1.20}, "cost": 1205.0}]
    )
    out = mod.compute_reconciliation()
    assert out["status"] == "ok"
    assert out["recommended_fee"] is None
    assert abs(out["diff_pct"]) < 5.0


def test_tolerance_exceeded_recommends_fee(fr, monkeypatch):
    mod, db = fr
    yest = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed(
        db,
        [
            (1, "BTC/USDT:USDT", 0, yest, yest, 60000.0, 60500.0, 0.01, 0.001, 0.001, 600.0, 5.0),
        ],
    )
    # local fee = 1.205. 실제는 2.0 → diff +66%
    monkeypatch.setattr(
        mod, "fetch_binance_actual_fees", lambda *a, **k: [{"fee": {"cost": 2.0}, "cost": 1205.0}]
    )
    out = mod.compute_reconciliation()
    assert out["status"] == "tolerance_exceeded"
    assert out["recommended_fee"] is not None
    # avg_real_rate = 2.0 / 1205 * 100 ≈ 0.166%
    # recommended = 0.00166 * 1.5 ≈ 0.00249
    assert 0.002 < out["recommended_fee"] < 0.003


def test_empty_binance_trade_list_does_not_alert(fr, monkeypatch):
    mod, db = fr
    yest = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _seed(
        db,
        [
            (1, "BTC/USDT:USDT", 0, yest, yest, 60000.0, 60500.0, 0.01, 0.001, 0.001, 600.0, 5.0),
        ],
    )
    monkeypatch.setattr(mod, "fetch_binance_actual_fees", lambda *a, **k: [])
    out = mod.compute_reconciliation()
    assert out["status"] == "no_real_trades"
    assert out["real"]["trades"] == 0
    assert out["diff_pct"] is None
    assert out["recommended_fee"] is None


def test_fetch_binance_no_keys_returns_none(fr, monkeypatch):
    mod, _ = fr
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    assert mod.fetch_binance_actual_fees() is None


def test_main_json_output(fr, capsys):
    mod, _ = fr
    import sys

    sys.argv = ["fee_reconciliation.py", "--json", "--days", "5"]
    rc = mod.main()
    out = capsys.readouterr().out
    assert '"status"' in out
    assert '"lookback_days": 5' in out
    assert rc == 0  # no_local_trades → 0

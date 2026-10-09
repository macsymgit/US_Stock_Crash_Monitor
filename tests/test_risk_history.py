from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys

import pandas as pd
import pytest

import risk_history as history
import risk_model


def record(path, ticker="VOO", score=60, day="2026-10-09", profile="default"):
    history.record_risk_score(ticker, score, observation_date=day, profile=profile, path=path)


def test_daily_update_does_not_overwrite_other_profiles_or_assets(tmp_path):
    path = tmp_path / "history.csv"
    record(path)
    record(path, score=70)
    record(path, ticker="LEGACY", score=80)
    record(path, profile="custom", score=90)
    assert len(pd.read_csv(path)) == 3
    assert history.load_risk_history("VOO", profile="default", path=path)["score"].tolist() == [70]
    assert history.load_risk_history("VOO", profile="custom", path=path)["score"].tolist() == [90]


def test_legacy_records_are_preserved_but_not_mixed_with_new_model(tmp_path):
    path = tmp_path / "history.csv"
    path.write_text("date,ticker,score\n2026-09-17,VOO,50\n", encoding="utf-8")
    record(path)
    assert len(history.load_risk_history("VOO", path=path)) == 2
    frame = history.load_risk_history("VOO", profile="default", path=path)
    assert frame["date"].dt.date.astype(str).tolist() == ["2026-10-09"]


def test_empty_and_invalid_rows_do_not_break_trend(tmp_path):
    path = tmp_path / "history.csv"
    path.touch()
    assert history.load_risk_history("VOO", path=path).empty
    path.write_text(
        "date,ticker,score\nbad,VOO,60\n2026-10-09,VOO,NaN\n"
        "2026-10-08,VOO,999\n2026-10-07,VOO,40\n",
        encoding="utf-8",
    )
    assert history.load_risk_history("VOO", path=path)["score"].tolist() == [40]


def test_write_failure_keeps_original_file(monkeypatch, tmp_path):
    path = tmp_path / "history.csv"
    record(path)
    original = path.read_bytes()

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(history.os, "replace", fail)
    with pytest.raises(OSError):
        record(path, score=80)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_malformed_header_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "history.csv"
    path.write_text("invalid,header\n1,2\n", encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        record(path)
    assert path.read_bytes() == original


def test_concurrent_writes_keep_all_assets(tmp_path):
    path = tmp_path / "history.csv"
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(record, path, ticker=f"ETF{i}") for i in range(24)]
        for future in futures:
            future.result()
    frame = pd.read_csv(path)
    assert len(frame) == 24
    assert frame["ticker"].nunique() == 24


def test_separate_processes_keep_each_others_records(tmp_path):
    path = tmp_path / "history.csv"
    code = """
import sys
from risk_history import record_risk_score
for i in range(3):
    record_risk_score(f'ETF{sys.argv[2]}_{i}', 60,
        observation_date='2026-10-09', profile='default', path=sys.argv[1])
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path), str(i)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(4)
    ]
    for process in processes:
        _, error = process.communicate(timeout=20)
        assert process.returncode == 0, error.decode(errors="replace")
    assert len(pd.read_csv(path)) == 12


def test_profile_uses_effective_weights_and_credit_method():
    normal = history.scoring_profile(risk_model.DEFAULT_WEIGHTS)
    scaled = history.scoring_profile({k: v * 10 for k, v in risk_model.DEFAULT_WEIGHTS.items()})
    all_zero = history.scoring_profile(dict.fromkeys(risk_model.FACTOR_META, 0))
    assert normal == scaled == all_zero
    assert normal != history.scoring_profile(risk_model.DEFAULT_WEIGHTS, "percentile")

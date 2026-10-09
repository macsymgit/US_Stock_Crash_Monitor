"""CSV历史持久化：按模型和权重隔离，锁定读写，原子替换并保留旧记录。"""

import csv
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from threading import RLock

import pandas as pd

import risk_model

HISTORY_FILE = Path(__file__).with_name("risk_history.csv")
_LOCK = RLock()
_COLUMNS = ["date", "ticker", "score", "model_version", "profile"]


def scoring_profile(weights, credit_method="absolute"):
    values = risk_model.normalize_weights(weights)
    if values["credit"] == 0:
        credit_method = "disabled"
    payload = {
        "weights": {k: round(v, 10) for k, v in values.items()},
        "credit_method": credit_method,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


@contextmanager
def _file_lock(path):
    """同一进程用RLock，不同进程用操作系统文件锁。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.with_suffix(path.suffix + ".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_rows(path):
    if not path.exists() or path.stat().st_size == 0:
        return [], list(_COLUMNS)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if not {"date", "ticker", "score"}.issubset(columns):
            raise ValueError("风险历史CSV缺少date/ticker/score列，原文件已保留")
        rows = list(reader)
        if any(None in row for row in rows):
            raise ValueError("风险历史CSV包含损坏行，原文件已保留")
    return rows, columns + [c for c in _COLUMNS if c not in columns]


def record_risk_score(ticker, score, *, observation_date, profile, path=None):
    """只接受明确的行情日期与评分配置。同日同配置覆盖，不混合不同模型。"""
    score = risk_model._finite(score, "风险评分", 0, 100)
    day = date.fromisoformat(str(observation_date)).isoformat()
    path = Path(path) if path is not None else HISTORY_FILE
    key = (day, ticker, risk_model.MODEL_VERSION, profile)
    with _file_lock(path):
        rows, columns = _read_rows(path)
        existing = [
            row
            for row in rows
            if tuple(row.get(c, "") for c in ("date", "ticker", "model_version", "profile")) == key
        ]
        if len(existing) == 1 and existing[0]["score"] == f"{score:.1f}":
            return
        rows = [
            row
            for row in rows
            if tuple(row.get(c, "") for c in ("date", "ticker", "model_version", "profile")) != key
        ]
        rows.append(dict(zip(_COLUMNS, (*key[:2], f"{score:.1f}", *key[2:]))))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8",
                dir=path.parent,
                prefix=path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def load_risk_history(ticker, *, profile=None, path=None):
    """profile指定时仅返回同模型同配置的记录；旧三列数据仍可读取。"""
    path = Path(path) if path is not None else HISTORY_FILE
    empty = pd.DataFrame(columns=["date", "score"])
    if not path.exists():
        return empty
    with _file_lock(path):
        rows, _ = _read_rows(path)
    if not rows:
        return empty
    frame = pd.DataFrame(rows)
    frame = frame.loc[frame["ticker"] == ticker].copy()
    if profile is not None:
        if "profile" not in frame or "model_version" not in frame:
            return empty
        frame = frame.loc[
            (frame["profile"] == profile) & (frame["model_version"] == risk_model.MODEL_VERSION)
        ]
    frame["date"] = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["date", "score"])
    frame = frame.loc[frame["score"].between(0, 100)]
    return frame[["date", "score"]].sort_values("date").reset_index(drop=True)

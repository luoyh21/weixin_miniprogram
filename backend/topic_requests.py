"""File-backed topic application records.

The topic pipeline is intentionally small and runs in the existing FastAPI
process, so request state must be written before background work starts.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

from .paths import DATA_DIR


CST = timezone(timedelta(hours=8))
REQUESTS_FILE = DATA_DIR / "topic_requests.json"
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def _load() -> list[dict]:
    if not REQUESTS_FILE.exists():
        return []
    try:
        data = json.loads(REQUESTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(rows: list[dict]) -> None:
    REQUESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REQUESTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, REQUESTS_FILE)


def create(*, applicant: str, title: str, intro: str, keywords: list[str],
           estimate: dict) -> dict:
    with _LOCK:
        rows = _load()
        normalized = title.strip().lower()
        for row in rows:
            if row.get("title", "").strip().lower() == normalized and row.get("status") not in (
                "rejected", "failed",
            ):
                raise ValueError("该专题已有申请或正在生成")
        status = "queued" if estimate.get("tier") == "low" else "pending"
        row = {
            "id": uuid.uuid4().hex[:12],
            "applicant": applicant,
            "title": title.strip(),
            "intro": intro.strip(),
            "keywords": keywords,
            "seed_urls": [],
            "status": status,
            "cost_tier": estimate.get("tier", "high"),
            "estimate": estimate,
            "topic_id": "",
            "created_at": _now(),
            "updated_at": _now(),
            "decided_by": "",
            "decision_note": "",
            "error": "",
        }
        rows.append(row)
        _save(rows)
        return dict(row)


def list_all() -> list[dict]:
    with _LOCK:
        rows = _load()
    rows.sort(key=lambda row: row.get("created_at", ""), reverse=True)
    return rows


def list_for(account: str) -> list[dict]:
    account = account.strip().lower()
    return [row for row in list_all() if row.get("applicant", "").lower() == account]


def get(request_id: str) -> dict | None:
    with _LOCK:
        return next((dict(row) for row in _load() if row.get("id") == request_id), None)


def update(request_id: str, **changes) -> dict:
    with _LOCK:
        rows = _load()
        for row in rows:
            if row.get("id") != request_id:
                continue
            row.update(changes)
            row["updated_at"] = _now()
            _save(rows)
            return dict(row)
    raise ValueError("专题申请不存在")


def recover_interrupted() -> int:
    """Mark work lost during a process restart as retryable."""
    with _LOCK:
        rows = _load()
        changed = 0
        for row in rows:
            if row.get("status") == "running":
                row.update(
                    status="failed",
                    error="服务重启导致任务中断，可由超级管理员重试",
                    updated_at=_now(),
                )
                changed += 1
        if changed:
            _save(rows)
        return changed

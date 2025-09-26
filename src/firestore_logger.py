# src/firestore_logger.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict

from google.cloud import firestore

_db = firestore.Client()

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def log_chat(q: str, r: str, meta: Dict[str, Any] | None = None) -> None:
    """
    Append one entry to sessions/YYYY-MM-DD.entries (array of {q,r,ts,meta}).
    Idempotent doc creation; uses ArrayUnion for append semantics.
    """
    meta = meta or {}
    ts = _now_utc_iso()
    doc_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc_ref = _db.collection("sessions").document(doc_id)

    # Ensure doc exists with entries array (merge keeps existing content)
    doc_ref.set({"entries": []}, merge=True)

    entry = {"q": q, "r": r, "ts": ts, "meta": meta}
    # Append using atomic ArrayUnion
    doc_ref.update({"entries": firestore.ArrayUnion([entry])})

    # Emit to stdout so it shows up in Cloud Run logs
    print(f"[FS] appended {doc_id}: {entry}")

# Back-compat alias (so code calling append_log(...) also works)
def append_log(q: str, r: str, meta: Dict[str, Any] | None = None) -> None:
    return log_chat(q, r, meta)
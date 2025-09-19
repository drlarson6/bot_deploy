# storage.py or inline near helpers
import os, json
from typing import Iterable, Dict, Any

# Backends:
# - GCS (set GCS_BUCKET env var)
# - Local /tmp fallback (for dev or if GCS temporarily unavailable)
GCS_BUCKET = os.environ.get("GCS_BUCKET")

from datetime import datetime, timezone

def now_iso():
    return datetime.now(timezone.utc).isoformat()

_gcs_client = _gcs_bucket = None
if GCS_BUCKET:
    try:
        from google.cloud import storage  # pip: google-cloud-storage
        _gcs_client = storage.Client()
        _gcs_bucket = _gcs_client.bucket(GCS_BUCKET)
    except Exception:
        _gcs_client = _gcs_bucket = None  # degrade gracefully

def _gcs_blob_path(sid: str) -> str:
    return f"sessions/{sid}.jsonl"

def append_log(sid: str, event_type: str, data: Any) -> None:
    """Append one JSONL record for this sid. Prefers GCS; /tmp fallback."""
    rec = {"t": now_iso(), "type": event_type, "data": data}
    line = json.dumps(rec, ensure_ascii=False) + "\n"

    # Try GCS
    if _gcs_bucket:
        try:
            blob = _gcs_bucket.blob(_gcs_blob_path(sid))
            try:
                existing = blob.download_as_text(encoding="utf-8")
            except Exception:
                existing = ""
            blob.upload_from_string(existing + line, content_type="text/plain; charset=utf-8")
            return
        except Exception:
            pass  # fall through to local

    # Local fallback (/tmp)
    pdir = "/tmp/sessions"
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, f"{sid}.jsonl"), "a", encoding="utf-8") as f:
        f.write(line)

def iter_session_events(sid: str) -> Iterable[Dict[str, Any]]:
    """Yield JSON dict events for this sid (Q/A/etc.). Prefers GCS."""
    # GCS first
    if _gcs_bucket:
        try:
            blob = _gcs_bucket.blob(_gcs_blob_path(sid))
            if blob.exists():
                text = blob.download_as_text(encoding="utf-8")
                for ln in text.splitlines():
                    if not ln.strip():
                        continue
                    try:
                        yield json.loads(ln)
                    except Exception:
                        continue
                return
        except Exception:
            pass  # fall through

    # Local fallback
    p = f"/tmp/sessions/{sid}.jsonl"
    if not os.path.exists(p):
        return
    with open(p, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except Exception:
                continue
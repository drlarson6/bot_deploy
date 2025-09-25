# src/gcs_logger.py
import os, json, datetime, uuid
from google.cloud import storage

BUCKET_NAME = os.getenv("SESSIONS_BUCKET", "zelora-prod-sessions")
SESSIONS_PREFIX = os.getenv("SESSIONS_PREFIX", "sessions")

_client = None
def _client_once():
    global _client
    if _client is None:
        _client = storage.Client()
    return _client

def append_jsonl(user_input: str, reply: str, meta: dict | None = None) -> None:
    """
    Append one JSON line to gs://<bucket>/<prefix>/daily-YYYY-MM-DD.jsonl (UTC date).
    Uses a safe 'compose' append so we don't download the whole file.
    """
    try:
        client = _client_once()
        bucket = client.bucket(BUCKET_NAME)

        # Daily filename in UTC
        day = datetime.datetime.utcnow().date().isoformat()
        key = f"{SESSIONS_PREFIX}/daily-{day}.jsonl"
        dest = bucket.blob(key)

        # Prepare a one-line payload object
        line = json.dumps({
            "ts": datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "input": user_input,
            "reply": reply,
            **(meta or {}),
        }) + "\n"

        # Write a small temp object, then compose into the daily file (server-side append)
        tmp_name = f"{SESSIONS_PREFIX}/.tmp-{uuid.uuid4().hex}.jsonl"
        tmp = bucket.blob(tmp_name)
        tmp.upload_from_string(line, content_type="application/json")

        if dest.exists():   # compose: [existing, tmp] -> dest
            dest.compose([dest, tmp])
        else:
            # First line of the day; just copy tmp to dest
            dest.rewrite(tmp)

        # best-effort cleanup (ignore errors)
        try:
            tmp.delete()
        except Exception:
            pass
    except Exception as e:
        # Don't raise — logging must never break the request path
        # If you have 'app' logger, you can import it and warn instead.
        print(f"[gcs_logger] append_jsonl failed: {e}")
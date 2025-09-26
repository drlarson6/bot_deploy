# src/search_and_email.py
"""
Safe Gmail/Calendar helpers for both local dev and Cloud Run.

- Local dev: set env LOCAL_DEV_GMAIL=1 and place a user OAuth token at ./token.json
- Cloud Run: no token.json; functions become no-ops that return safe fallbacks.
  (Prevents crashes when these features aren't configured.)
"""

from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone

# Scopes used when LOCAL_DEV_GMAIL=1 and token.json exists
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
CAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Lazy imports only when needed (to avoid heavy deps / crashes)
def _try_import_google():
    from google.oauth2.credentials import Credentials  # type: ignore
    from googleapiclient.discovery import build  # type: ignore
    return Credentials, build


def _have_local_token() -> bool:
    return os.getenv("LOCAL_DEV_GMAIL", "0") == "1" and os.path.exists("token.json")


def _load_local_user_creds(scopes: List[str]):
    """
    Local-only: load OAuth user credentials from token.json.
    Never called automatically in Cloud Run.
    """
    Credentials, build = _try_import_google()
    creds = Credentials.from_authorized_user_file("token.json", scopes)
    return creds


# ---------- Public: service builders (lazy & safe) ----------

def get_gmail_service():
    """
    Local dev: returns a Gmail service if token.json exists and LOCAL_DEV_GMAIL=1.
    Cloud Run / no token: returns None.
    """
    if not _have_local_token():
        return None
    Credentials, build = _try_import_google()
    creds = _load_local_user_creds(GMAIL_SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_calendar_service():
    """
    Local dev: returns a Calendar service if token.json exists and LOCAL_DEV_GMAIL=1.
    Cloud Run / no token: returns None.
    """
    if not _have_local_token():
        return None
    Credentials, build = _try_import_google()
    creds = _load_local_user_creds(CAL_SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


# ---------- Public: high-level helpers (safe fallbacks) ----------

def send_email(to: str, subject: str, body: str) -> str:
    """
    Local dev: sends via Gmail API.
    Cloud Run: returns a message indicating email is disabled.
    """
    svc = get_gmail_service()
    if svc is None:
        return "(email disabled in this environment)"

    from base64 import urlsafe_b64encode
    from email.mime.text import MIMEText

    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    encoded = urlsafe_b64encode(msg.as_bytes()).decode()

    svc.users().messages().send(userId="me", body={"raw": encoded}).execute()  # type: ignore
    return "sent"


def search_gmail(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """
    Local dev: searches Gmail and returns a list of message metadata.
    Cloud Run: returns [].
    """
    svc = get_gmail_service()
    if svc is None:
        return []

    res = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()  # type: ignore
    ids = [m["id"] for m in res.get("messages", [])]

    out: List[Dict[str, Any]] = []
    for mid in ids:
        m = svc.users().messages().get(userId="me", id=mid, format="metadata").execute()  # type: ignore
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        out.append({
            "id": mid,
            "snippet": m.get("snippet", ""),
            "from": headers.get("from"),
            "subject": headers.get("subject"),
            "date": headers.get("date"),
        })
    return out


def list_calendar_events(
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    Local dev: lists upcoming events.
    Cloud Run: returns [].
    """
    svc = get_calendar_service()
    if svc is None:
        return []

    if time_min is None:
        time_min = datetime.now(timezone.utc).isoformat()

    res = svc.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()  # type: ignore

    return res.get("items", [])


# ---------- Compatibility aliases (cover common import names) ----------
# If your app imports these specific names, they will resolve.
search_emails = search_gmail
list_events = list_calendar_events

__all__ = [
    "get_gmail_service",
    "get_calendar_service",
    "send_email",
    "search_gmail",
    "search_emails",
    "list_calendar_events",
    "list_events",
]

import json, os
from datetime import datetime, timezone

class SessionStore:
    def __init__(self, session):
        self.session = session

    def path(self):
        return os.path.join("sessions", f"{self.session.id}.json") if self.session.id else None

    def append_log(self, q, r):
        p = self.path()
        if not p: return
        try:
            with open(p, "r+") as f:
                data = json.load(f)
                data.setdefault("questions", []).append(q)
                data.setdefault("responses", []).append(r)
                f.seek(0); json.dump(data, f, indent=2); f.truncate()
        except Exception as e:
            print(f"⚠ Could not log Q/A: {e}")

    def end_file(self):
        p = self.path()
        if not p: return
        try:
            with open(p, "r+") as f:
                data = json.load(f)
                data["status"] = "ended"
                data["ended_at"] = datetime.now(timezone.utc).isoformat()
                f.seek(0); json.dump(data, f, indent=2); f.truncate()
        except Exception as e:
            print(f"⚠ Could not update session file: {e}")

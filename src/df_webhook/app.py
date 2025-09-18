import os, json
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify

# Config
CONFIDENCE_THRESHOLD = float(os.getenv("DF_CONF_THRESHOLD", "0.85"))
CONFIRM_CONTEXT = "awaiting_start_confirmation"
SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", "/tmp/sessions"))  # Cloud Run/local: /tmp is writable
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

def cx(session: str, name: str) -> str:
    return f"{session}/contexts/{name}"

@app.post("/dialogflow-webhook")
def dialogflow_webhook():
    req = request.get_json(force=True) or {}
    qr = req.get("queryResult") or {}
    intent = (qr.get("intent") or {}).get("displayName", "")
    conf = float(qr.get("intentDetectionConfidence") or 0.0)
    session = req.get("session", "")
    contexts = qr.get("outputContexts") or []
    utter = qr.get("queryText") or ""

    # startDeviationSession — silent on low confidence, otherwise ask yes/no and set context
    if intent == "startDeviationSession":
        if conf < CONFIDENCE_THRESHOLD:
            return jsonify({}), 200  # stay silent
        return jsonify({
            "fulfillmentText": "I think you want to start a deviation session. Should I begin now? (yes/no)",
            "outputContexts": [{
                "name": cx(session, CONFIRM_CONTEXT),
                "lifespanCount": 2,
                "parameters": {"first_utterance": utter, "confidence": conf}
            }]
        }), 200

    # confirm.start.yes — verify context, then write a session file
    if intent == "confirm.start.yes":
        has_ctx = any((c.get("name") or "").endswith(f"/contexts/{CONFIRM_CONTEXT}") for c in contexts)
        if not has_ctx:
            return jsonify({"fulfillmentText": "I’m not currently starting a session. Say 'start deviation' first."}), 200

        dev_session_id = uuid4().hex

        from app import current_session_id
        current_session_id = dev_session_id

        data = {
            "dev_session_id": dev_session_id,
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "started",
            "df_session": session,
            "why": next((c.get("parameters", {}).get("first_utterance", "") for c in contexts
                         if (c.get("name") or "").endswith(f"/contexts/{CONFIRM_CONTEXT}")), ""),
            "questions": [],
            "responses": []
        }
        (SESSIONS_DIR / f"{dev_session_id}.json").write_text(json.dumps(data, indent=2))
        app.logger.info(f"WROTE: {(SESSIONS_DIR / f'{dev_session_id}.json')}")
        print(f"WROTE: {(SESSIONS_DIR / f'{dev_session_id}.json')}", flush=True)

        return jsonify({
            "fulfillmentText": "Starting a deviation session now. I’ll guide you through the interview.",
            "outputContexts": []
        }), 200

    # confirm.start.no — just acknowledge
    if intent == "confirm.start.no":
        return jsonify({
            "fulfillmentText": "Okay, not starting a session. If you need one later, just say “start deviation.”",
            "outputContexts": []
        }), 200

    # Default: no handler
    return jsonify({}), 200

@app.get("/")
def health():
    return "ok", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

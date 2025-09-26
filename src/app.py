import os, json, traceback
from uuid import uuid4
from datetime import datetime, timezone
import logging
import traceback
from .session_state import session, SessionType
from flask import current_app, send_from_directory

from .tts_bytes import synthesize_to_bytes
from .router import handle_session_text_router, Ctx
USE_NEW_ROUTER = True  # toggle the new router on/off for testing

from .session_state import session, SessionType
# import or reference your app/flask instance, ask_gpt, and call_sheets_action from wherever they actually live

from dotenv import load_dotenv
load_dotenv()
import requests
import dataclasses
from http import client
import logging
from wsgiref import headers
from flask import Flask, request, jsonify, render_template, send_file, Response

from .search_and_email import (
    read_sheet_and_send_email, spreadsheet_id, range_name,
    create_google_sheet_min,   # ← add this
    append_rows,
    creds
)
#from google.oauth2 import service_account
from google.auth import default
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import webbrowser
from google.cloud import speech_v1p1beta1 as speech
# import numpy as np
# Global variable to store the latest transcript
latest_transcript = None
from flask_socketio import SocketIO, emit
from google.cloud import dialogflow_v2 as dialogflow
#from pydub import AudioSegment
import io
import logging
from threading import Timer
import threading

import re
from google.cloud import translate
from .chat_gpt4o import ask_gpt, chat_with_gpt, handle_control_signal
from google.cloud import texttospeech, storage
from firestore_logger import append_log

USE_GCS_LOG = os.getenv("USE_GCS_LOG", "0") == "1"
SESSIONS_BUCKET = os.getenv("SESSIONS_BUCKET", "zelora-prod-sessions")

with open(os.path.join("specs", "registry.json")) as f:
    REGISTRY = json.load(f)

# Load sheet actions at startup
with open(os.path.join(os.path.dirname(__file__), "sheet_actions.json")) as f:
    SHEET_ACTIONS = {a["name"]: a for a in json.load(f)}

#client = texttospeech.TextToSpeechClient()
from .tts_google import synthesize_to_file
logging.basicConfig(level=logging.INFO)

# --- minimal GCS JSONL logger ---
import json, datetime
from google.cloud import storage

from gcs_logger import append_jsonl


BUCKET_NAME = "zelora-prod-sessions"

def log_to_gcs(user_input: str, reply: str):
    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        # file per day
        fname = f"sessions/daily-{datetime.date.today().isoformat()}.jsonl"
        blob = bucket.blob(fname)

        line = json.dumps({
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
            "input": user_input,
            "reply": reply,
        }) + "\n"

        # append (compose object if already exists)
        if blob.exists():
            old = blob.download_as_text()
            blob.upload_from_string(old + line, content_type="application/json")
        else:
            blob.upload_from_string(line, content_type="application/json")
    except Exception as e:
        app.logger.warning(f"GCS log failed: {e}")

_storage_client = storage.Client()
_sessions_bucket = _storage_client.bucket("zelora-prod-sessions")

def _append_log_entry(entry: dict):
    """
    Append a JSON line to a daily JSONL file in GCS.
    Simple & safe: download current content (if any), append one line, upload.
    """
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    line = json.dumps({"t": ts, **entry}) + "\n"
    blob_name = f"sessions/daily-{datetime.date.today().isoformat()}.jsonl"
    blob = _sessions_bucket.blob(blob_name)

    try:
        existing = blob.download_as_text()
    except Exception:
        existing = ""
    blob.upload_from_string(existing + line, content_type="application/jsonl")
# --- end minimal GCS JSONL logger ---

CONFIDENCE_THRESHOLD = float(os.getenv("DF_CONF_THRESHOLD", "0.85"))
CONFIRM_CONTEXT = "awaiting_deviation_confirmation"
CONTROL_HOOK_URL = os.getenv("CONTROL_HOOK_URL", "http://127.0.0.1:5055/control-hook")

# CONTROL_HOOK_URL = "http://127.0.0.1:5000/control-hook"  # adjust if needed
# Track if we are inside a deviation session
#session.mode = False

PORT = 5055

#session.mode = False
#session.id = None

USE_NEW_ROUTER = True  # toggle the new router on/off for testing

# --- Feature toggles ---
USE_DIALOGFLOW = False  # start in GPT-only mode

last_prompt = None
last_reply  = None

# Initialize the Google Cloud Translation client
translate_client = translate.TranslationServiceClient()

app = Flask(__name__)

@app.after_request
def add_revision_header(resp):
    resp.headers["X-Served-By"] = os.environ.get("K_REVISION", "unknown")
    return resp

app.logger.info(f"Loaded router from: {handle_session_text_router.__module__}")


@app.route("/robots.txt")
def robots_txt():
    return send_from_directory("static", "robots.txt", mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap_xml():
    return send_from_directory("static", "sitemap.xml", mimetype="application/xml")

@app.route("/favicon.ico")
def favicon_ico():
    return send_from_directory("static", "favicon.ico", mimetype="image/x-icon")

@app.route('/health')
def health():
    return "ok", 200

socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# Path to your service account key file
#SERVICE_ACCOUNT_FILE = '/Users/douglaslarson/bot_project/intent-agent-9xeu-948dd173e887.json'

# Define the scopes
SCOPES = ['https://www.googleapis.com/auth/drive','https://www.googleapis.com/auth/dialogflow', 'https://www.googleapis.com/auth/spreadsheets']

# Set the environment variable to the path of the JSON file
#os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = SERVICE_ACCOUNT_FILE



#credentials = None  # App Engine will automatically provide credentials
credentials, project = default(scopes=SCOPES)
sheet_service = build('sheets', 'v4', credentials=credentials)

#sheet_service = build('sheets', 'v4', credentials=credentials)

#client = gspread.authorize(credentials)
#print(client)

drive_service = build('drive', 'v3', credentials=credentials)

# Your Google Cloud project ID
project_id = 'intent-agent-9xeu'
parent = f"projects/{project_id}"


# Generate a random session ID
session_id = str(uuid4())

# Construct the Dialogflow endpoint
dialogflow_endpoint = f'https://dialogflow.googleapis.com/v2/projects/{project_id}/agent/sessions/{session_id}:detectIntent'

DIALOGFLOW_ENDPOINT = dialogflow_endpoint
DIALOGFLOW_API_KEY = os.getenv('DIALOGFLOW_API_KEY')

#synthesize_to_file("Whatever you want the bot to say.")

#session.id = None  # track current active deviation session

VOICE_SESSIONS = {}  # session_id -> {"spreadsheet_id","sheet_url","tab","log_path"}

def _voice_session_file(session_id):
    return os.path.join("sessions", f"{session_id}.jsonl")

os.makedirs("sessions", exist_ok=True)

def _voice_log_turn(session_id, text, plan, result):
    entry = {
        "t": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "plan": plan,
        "result": result
    }
    with open(_voice_session_file(session_id), "a") as f:
        f.write(json.dumps(entry) + "\n")

@app.route("/voice-turn", methods=["POST"])
def voice_turn():
    """
    Body:
    {
      "session_id": "optional",
      "intent": "CREATE_SHEET | BOLD_HEADER",
      "params": {...},
      "tab": "Sheet1",
      "text": "raw user utterance"
    }
    """
    data = request.get_json(force=True, silent=True) or {}
    intent = (data.get("intent") or "").upper()
    params = data.get("params") or {}
    tab = data.get("tab") or "Sheet1"
    text = data.get("text") or ""

    # ensure/assign session
    session_id = data.get("session_id") or f"vsess_{uuid4().hex[:8]}"
    sess = VOICE_SESSIONS.get(session_id)

    # 1) create sheet
    if intent == "CREATE_SHEET":
        title = params.get("title_hint") or "Untitled Sheet"
        spreadsheet_id, sheet_url = create_google_sheet_min(title)
        VOICE_SESSIONS[session_id] = {
            "spreadsheet_id": spreadsheet_id,
            "sheet_url": sheet_url,
            "tab": tab,
            "log_path": _voice_session_file(session_id)
        }
        _voice_log_turn(session_id, text, {"kind": "create", "title_hint": title},
                        {"sheet_url": sheet_url})
        return jsonify({
            "status": "ok",
            "session_id": session_id,
            "spreadsheet_id": spreadsheet_id,
            "sheet_url": sheet_url,
            "say": f"Created sheet {title}"
        })

    # 2) modify (requires existing session)
    if not sess or not sess.get("spreadsheet_id"):
        return jsonify({
            "status": "needs_create",
            "message": "no sheet yet for this session",
            "session_id": session_id
        }), 200

    spreadsheet_id = sess["spreadsheet_id"]
    tab = data.get("tab") or sess.get("tab") or "Sheet1"

    if intent == "BOLD_HEADER":
        sheet_id = get_sheet_id(spreadsheet_id, tab)
        req = ACTION_BUILDERS["BoldHeader"](sheet_id, {
            "row_number": int(params.get("row_number", 1)),
            **({"alignment": params["alignment"]} if "alignment" in params else {})
        })
        svc = _sheets_service()
        result = svc.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [req]}
        ).execute()
        _voice_log_turn(session_id, text,
                        {"kind": "action", "action": "BoldHeader", "args": params},
                        result)
        return jsonify({
            "status": "ok",
            "session_id": session_id,
            "spreadsheet_id": spreadsheet_id,
            "action": "BoldHeader",
            "say": f"The header row is now bold and {params.get('alignment','centered').lower()}"
        })

    return jsonify({
        "status": "unknown_intent",
        "intent": intent,
        "session_id": session_id
    }), 200
# --- end baby-step voice glue ---

@app.route("/df-webhook", methods=["POST"])
def df_webhook():
    body = request.get_json(silent=True, force=True) or {}

    query       = body.get("queryResult") or {}
    intent      = ((query.get("intent") or {}).get("displayName") or "").strip()
    parameters  = query.get("parameters") or {}

    # --- Session ID normalization ---
    sid_full  = body.get("session") or "sessions/local"
    # Extract the trailing token after '/sessions/' — DF uses full path, router often uses the short token
    sid_short = sid_full.rsplit("/sessions/", 1)[-1] if "/sessions/" in sid_full else sid_full

    app.logger.info(f"[DF] intent={intent} sid_full={sid_full} sid_short={sid_short}")

    # Is this the confirmation that should hand off to GPT mode?
    intent_l = intent.lower()
    is_confirm_yes = (
        "confirm" in intent_l and "yes" in intent_l
        and any(w in intent_l for w in ("sheet", "spreadsheet", "create"))
    ) or intent_l in {
        "createspreadsheet.confirm.yes",
        "startspreadsheetsession.confirm.yes",
    }

    if is_confirm_yes:
        switched = False

        # Try flipping your existing in-process flag for BOTH ids
        try:
            from session_store import set_session_flag, is_session_flag_on
            for key in ("sheet_session_active", "gpt_sheet"):  # use whichever you already use
                set_session_flag(sid_full,  key, True)
                set_session_flag(sid_short, key, True)
            app.logger.info(
                f"[DF] switch ON via session_store for sid_full={sid_full} sid_short={sid_short} "
                f"now_full={is_session_flag_on(sid_full,'sheet_session_active') or is_session_flag_on(sid_full,'gpt_sheet')} "
                f"now_short={is_session_flag_on(sid_short,'sheet_session_active') or is_session_flag_on(sid_short,'gpt_sheet')}"
            )
            switched = True
        except Exception as e:
            app.logger.warning(f"[DF] session_store setter not available ({e}); will try /control-hook")

        # Fallback to your control-hook (flip for BOTH ids) if that's your original path
        if not switched:
            try:
                import requests
                for sid in (sid_full, sid_short):
                    requests.post(
                        "http://127.0.0.1:5055/control-hook",
                        json={"action": "start_sheet_session", "session_id": sid},
                        timeout=2,
                    )
                app.logger.info(f"[DF] switch ON via /control-hook for sid_full={sid_full} and sid_short={sid_short}")
                switched = True
            except Exception as e2:
                app.logger.error(f"[DF] control-hook failed: {e2}")

        say = "Starting your spreadsheet session."
    else:
        say = "Okay."

    # Keep your current payload/context shape. We’ll replace placeholders later.
    spid = parameters.get("spreadsheet_id") or "spreadsheet-abc"
    url  = parameters.get("sheet_url") or "https://docs.google.com/spreadsheets/d/spreadsheet-abc"

    ctx_name = f"{sid_full}/contexts/spreadsheet_session"

    return jsonify({
        "fulfillmentText": say,
        "payload": {
            "handoff": "gpt",
            "session_id": sid_short,       # send short back; many clients expect this
            "spreadsheet_id": spid,
            "sheet_url": url
        },
        "outputContexts": [{
            "name": ctx_name,
            "lifespanCount": 50,
            "parameters": {
                "session_id": sid_short,
                "spreadsheet_id": spid,
                "sheet_url": url
            }
        }]
    })

@app.route("/debug-state", methods=["GET"])
def debug_state():
    return jsonify({
        "session": {
            "mode": bool(session.mode),
            "type": getattr(session.type, "name", str(session.type)),
            "id": session.id,
            "spreadsheet_id": getattr(session, "spreadsheet_id", None),
            "sheet_title": getattr(session, "sheet_title", None),
        }
    }), 200

def handle_session_text(user_text: str):
    if USE_NEW_ROUTER:
        app.logger.info(f"🧪 Ctx fields at runtime: {list(Ctx.__dataclass_fields__.keys())}")
        # src/app.py  (inside handle_session_text)
        ctx = Ctx(
            session=session,
            SessionType=SessionType,
            app=app,                     # ← REQUIRED
            chat_with_gpt=chat_with_gpt,
            call_sheets_action=call_sheets_action,
        )
        app.logger.info("🧭 using NEW router w/ app in Ctx")
        return handle_session_text_router(user_text, ctx)
    return handle_session_text_legacy(user_text)


def handle_session_text_legacy(user_text):
    """
    Route live-session text by session.type.
    - DEVIATION: your existing Q/A + GPT fallback (with file logging).
    - SPREADSHEET: intercept Bold/Unbold header voice commands, else GPT fallback.
    Returns (handled: bool, reply: str|None)
    """
    low = (user_text or "").strip().lower()

    if not session.mode:
        return False, None

    # ----- small file helpers -----
    def _session_path():
        return os.path.join("sessions", f"{session.id}.json") if session.id else None

    def _append_log(q, r):
        path = _session_path()
        if not path:
            return
        try:
            with open(path, "r+") as f:
                data = json.load(f)
                data.setdefault("questions", []).append(q)
                data.setdefault("responses", []).append(r)
                f.seek(0); json.dump(data, f, indent=2); f.truncate()
        except Exception as e:
            print(f"⚠ Could not log Q/A: {e}")

    def _end_session_file():
        path = _session_path()
        if not path:
            return
        try:
            with open(path, "r+") as f:
                data = json.load(f)
                data["status"] = "ended"
                data["ended_at"] = datetime.now(timezone.utc).isoformat()
                f.seek(0); json.dump(data, f, indent=2); f.truncate()
        except Exception as e:
            print(f"⚠ Could not update session file: {e}")

    # ----- end/close commands per-session -----
    if session.type == SessionType.DEVIATION and low == "close deviation":
        _end_session_file()
        session.mode = False
        session.type = SessionType.NONE
        session.id = None
        return True, "Deviation session ended."

    if session.type == SessionType.SPREADSHEET and low in ("close spreadsheet", "end spreadsheet", "stop spreadsheet"):
        _end_session_file()
        session.mode = False
        session.type = SessionType.NONE
        session.id = None
        return True, "Spreadsheet session ended."

    # ----- route by type -----
    if session.type == SessionType.SPREADSHEET:
        # Voice → Sheets intercepts (BEFORE GPT fallback)
        import re

        def _extract_row(text: str) -> int:
            m = re.search(r"\brow\s+(\d+)\b", text, re.I)
            if m:
                try:
                    return max(1, int(m.group(1)))
                except Exception:
                    pass
            if re.search(r"\b(first|1st|top)\s+row\b", text, re.I):
                return 1
            return 1  # default

        # Bold header
        if (("bold" in low) and ("header" in low or "headers" in low)) or re.search(r"\b(first|1st|top)\s+row\b.*\bbold\b", low, re.I):
            row = _extract_row(user_text)
            ok, msg = call_sheets_action("BoldHeader", {"row_number": row})
            app.logger.info(f"🎯 Voice→Sheets: BoldHeader row={row} ok={ok} msg={msg}")
            reply = "Okay—done." if ok else f"Hmm—{msg}"
            _append_log(user_text, f"BoldHeader(row={row}): {msg}")
            return True, reply

        # Unbold header
        if ("unbold" in low and ("header" in low or "headers" in low)) or re.search(r"\bremove\b.*\bbold\b.*\bheader", low, re.I):
            row = _extract_row(user_text)
            ok, msg = call_sheets_action("UnboldHeader", {"row_number": row})
            app.logger.info(f"🎯 Voice→Sheets: UnboldHeader row={row} ok={ok} msg={msg}")
            reply = "Okay—done." if ok else f"Hmm—{msg}"
            _append_log(user_text, f"UnboldHeader(row={row}): {msg}")
            return True, reply

        # Spreadsheet GPT fallback
        reply, _ = ask_gpt(user_text)
        _append_log(user_text, reply)
        return True, reply

    if session.type == SessionType.DEVIATION:
        # Deviation GPT fallback (as you already had)
        reply, _ = ask_gpt(user_text)
        _append_log(user_text, reply)
        return True, reply

    # Unknown type → let DF handle
    return False, None


def extract_sop_link(previous_record):
    sop_reference = None
    # Check if the word "SOP" exists in the description
    if 'SOP' in previous_record['Description/Instruction']:
        # Use regex to extract SOP number (e.g., SOP-002)
        match = re.search(r'SOP-\d+', previous_record['Description/Instruction'])
        if match:
            sop_reference = match.group(0)  # This will give you "SOP-002"
    
    return sop_reference

import subprocess

def transcribe_audio(audio_data, client, config):
    audio = speech.RecognitionAudio(content=audio_data)
    response = client.recognize(config=config, audio=audio)
    transcript = response.results[0].alternatives[0].transcript if response.results else ''
    return transcript

def process_transcript(transcript, session_client, session_path):
    text_input = dialogflow.TextInput(text=transcript, language_code='en-US')
    query_input = dialogflow.QueryInput(text=text_input)
    response = session_client.detect_intent(session=session_path, query_input=query_input)
    return response.query_result.fulfillment_text

@app.route('/speak', methods=['POST'])
def speak():
    try:
        app.logger.info('[/speak] start')
        data = request.get_json(force=True) or {}
        text = (data.get('text') or '').strip()
        app.logger.info(f'[/speak] text_len={len(text)}')
        if not text:
            return jsonify({'error': 'No text provided'}), 400

        audio = synthesize_to_bytes(text)   # returns MP3 bytes; no filesystem
        app.logger.info(f'[/speak] bytes={len(audio)}')
        r = Response(audio, mimetype='audio/mpeg')
        r.headers['Access-Control-Allow-Origin'] = '*'
        return r

    except Exception:
        app.logger.exception('[/speak] ERROR')
        return jsonify({'error': 'TTS failed', 'detail': traceback.format_exc()}), 500

def record_and_transcribe():
    # Step 1: Configure the Speech-to-Text client and request
    speech_client = speech.SpeechClient()
    speech_config = speech.RecognitionConfig(
        # If the browser sends webm/opus (most do), you can switch to:
        # encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
        # sample_rate_hertz=48000,
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        language_code='en-US',
        # enable_automatic_punctuation=True,  # optional
    )

    # Check if the 'audio' file is in the request
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    # Get the uploaded audio file from the request
    audio_file = request.files['audio']
    audio_data = audio_file.read()
    if not audio_data:
        return jsonify({'error': 'Audio data is empty'}), 400

    try:
        # Step 2: Transcribe
        transcript = transcribe_audio(audio_data, speech_client, speech_config)
        print("R&T → session.mode:", session.mode, "| transcript:", repr((transcript or "")[:80]))

        # Intercept end-command / GPT-only handling first
        handled, reply = handle_session_text(transcript)
        if handled:
            payload = [{
                'transcript': transcript,
                'response_text': reply,
                'follow_up_needed': False
            }]
            socketio.emit('stt_result', {'message': payload})
            return jsonify({"message": payload}), 200

    except Exception as e:
        print(f"Error during transcription: {e}")
        return jsonify({"error": "Transcription failed"}), 500

    # HARD BLOCK: if deviation session is active, skip Dialogflow entirely
    if session.mode:
        response_text, _ = ask_gpt(transcript or "(no speech)")
        #if (transcript or "").strip():
        #    log_chat_to_history(transcript, response_text)
        payload = [{
            'transcript': transcript,
            'response_text': response_text,
            'follow_up_needed': False
        }]
        socketio.emit('stt_result', {'message': payload})
        return jsonify({"message": payload}), 200

    # Step 3–4: Normal mode → GPT or DF depending on toggle
    print("Input:", transcript)
    response_text = ""
    intent = ""

    try:
        if USE_DIALOGFLOW:
            df = send_to_dialogflow(transcript)
            qr = (df or {}).get("queryResult", {}) or {}
            intent = ((qr.get("intent") or {}).get("displayName") or "").strip()
            response_text = (qr.get("fulfillmentText") or "").strip()
        else:
            response_text, _ = ask_gpt(transcript or "(no speech)")
        # Drop huge audio field if present
        if isinstance(df, dict) and 'outputAudio' in df:
            df = {k: v for k, v in df.items() if k != 'outputAudio'}

        print("DF picked:", {"intent": intent, "text": response_text})

        # --- Deviation intents for the VOICE path (flip the switch here) ---
        if intent == 'startDeviationSession':
            payload = [{'transcript': transcript, 'response_text': response_text, 'follow_up_needed': False}]
            socketio.emit('stt_result', {'message': payload})
            return jsonify({"message": payload}), 200

        if intent == 'confirm.start.no':
            payload = [{'transcript': transcript, 'response_text': response_text or "Okay, not starting a session.", 'follow_up_needed': False}]
            socketio.emit('stt_result', {'message': payload})
            return jsonify({"message": payload}), 200

        if intent == 'confirm.start.yes':
            # Flip the switch via control hook (single source of truth)
            try:
                requests.post(CONTROL_HOOK_URL, json={"action": "start_session"}, timeout=2.5)
                print("🎛️  control-hook called from STT path")
            except Exception as e:
                # Failsafe so you aren’t stuck in DF
                print(f"⚠️ control-hook failed from STT path: {e} — enabling session locally.")
                session.mode = True
                session.type = SessionType.DEVIATION
                session.id = uuid4().hex
                os.makedirs("sessions", exist_ok=True)
                with open(os.path.join("sessions", f"{session.id}.json"), "w") as f:
                    json.dump({
                        "dev_session_id": session.id,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "status": "started",
                        "questions": [],
                        "responses": []
                    }, f, indent=2)

            payload = [{'transcript': transcript,
                        'response_text': "Starting a deviation session now. I’ll guide you through the interview.",
                        'follow_up_needed': False}]
            socketio.emit('stt_result', {'message': payload})
            return jsonify({"message": payload}), 200
        # --- end deviation-intents handling ---

    except Exception as e:
        print(f"Dialogflow error: {e}")

    if not response_text:
        # Fallback to GPT
        response_text, _ = ask_gpt(transcript or "(no speech)")

    # ✅ Log the turn
    #if (transcript or "").strip():
    #    log_chat_to_history(transcript, response_text)
    else:
        print("⚠️ Skipping log: transcript is empty")

    # Step 5: Language check
    language_code = 'ru' if 'по ППИ' in response_text else 'en'

    # Step 6: Translate if needed
    if response_text and language_code != 'en':
        try:
            resp = translate_client.translate_text(
                parent=parent,
                contents=[response_text],
                mime_type="text/plain",
                target_language_code=language_code
            )
            response_text = resp.translations[0].translated_text
        except Exception as e:
            print(f"Error during translation: {e}")
    else:
        print("No response text to translate or language is English.")

    # Step 7: Build payload
    payload = [{
        'transcript': transcript,
        'response_text': response_text,
        'follow_up_needed': False
    }]

    # Step 8: Emit + return
    socketio.emit('stt_result', {'message': payload})
    return jsonify({"message": payload}), 200

def process_dialogflow_response(transcript):
    print('Made it to process_dialogflow_response')
    
    dialogflow_response = send_to_dialogflow(transcript)
    
    #print(json.dumps(dialogflow_response, indent=2))  # Print the full response for debugging

    fulfillment_text = get_nested(dialogflow_response, 'queryResult', 'fulfillmentText')
    intent_name = get_nested(dialogflow_response, 'queryResult', 'intent', 'displayName')
    response_message = decision_tree(intent_name, dialogflow_response)

    stepNumber = get_nested(dialogflow_response, 'queryResult', 'outputContexts', 0, 'parameters', 'stepNumber')
    print(f"Extracted Step Number from Context: {stepNumber}")  # Debug print

    return jsonify({'response': fulfillment_text, 'message': response_message})

def find_step_info(step_number):
    try:
        # Set the correct range in the spreadsheet to look for the data
        spreadsheet_id = '1Qydl1HEcv7L5L9QqnZ8dpzAe-4T8kaeHUcZKtVdwMAI'
        range_name = 'Cake!A6:C30'  # Range where your steps, SOP, and description are located

        # Get the data from the spreadsheet
        result = sheet_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
        values = result.get('values', [])

        # Loop through the rows to find the step number
        for row in values:
            if row and row[0] == str(step_number):
                # Extract SOP from column B and description from column C
                sop_link = row[1] if len(row) > 1 else None
                description = row[2] if len(row) > 2 else None
                #print(f"Found! Description: {description}, SOP Link: {sop_link}")
                return description, sop_link
        # If step is not found, return None
        print(f"Step {step_number} not found.")
        return None, None

    except Exception as e:
        print(f"Error during step info retrieval: {e}")
        return None, None

def get_step_info(step_number):
    # Open the Google Sheet
    sheet = client.open_by_key('1Qydl1HEcv7L5L9QqnZ8dpzAe-4T8kaeHUcZKtVdwMAI').sheet1
    
    # Define the headers manually for expected structure
    expected_headers = ['Step', 'SOP', 'Description/Instruction', 'Performer', 'Verifier']
    
    # Use the defined headers while getting the records
    records = sheet.get_all_records(expected_headers=expected_headers, head=6)
    
    # Print the records to make sure they are being fetched correctly
    for i, record in enumerate(records):
        print(f"Record {i}: {record}")  # Print each record
    
    # Proceed to check if the step matches
    for i, record in enumerate(records):
        if 'Step' in record:
            # Print each step as it's being compared for debugging
            print(f"Checking Step from record: '{record['Step']}' (Type: {type(record['Step'])}) against step_number: '{step_number}' (Type: {type(step_number)})")
            
            # Ensure both values are strings and strip any surrounding spaces
            record_step = str(record['Step']).strip()
            input_step = str(step_number).strip()
            
            print(f"Processed Step: '{record_step}' vs Input Step: '{input_step}'")
            
            if record_step == input_step:
                sop_link = None
                print(f"Match found for step number: {step_number}")
                
                # Check if there's a previous record for the SOP link
                if i > 0:  # Ensure there's a row above to check
                    previous_record = records[i - 1]
                    if 'SOP' in previous_record['Description/Instruction']:
                        sop_link = previous_record['Description/Instruction']
                
                # Return the description and SOP link (if found)
                return record['Description/Instruction'], sop_link
    
    # If no match is found, return None
    return None, None

# Dictionary to simulate session storage
session_storage = {}

def decision_tree(intent_name, dialogflow_response):
    print('Made it to decision_tree')
    print(f"Intent name: {intent_name}")  # Debug print
    sop_topic = get_nested(dialogflow_response, 'queryResult', 'parameters', 'sopTopic')
    userID = get_nested(dialogflow_response, 'queryResult', 'parameters', 'userid')
    stepNumber = get_nested(dialogflow_response, 'queryResult', 'parameters', 'stepNumber')
    print(f"Extracted Step Number: {stepNumber}")  # Debug print to check if the step number is being retrieved

    session_id = dialogflow_response.get('session', 'default_session_id')
    
    if userID is not None:
        userID = userID.lower()

    if intent_name == 'findSOP' and sop_topic:
        
        sop_link, error_message = find_sop_pdf(sop_topic)  # Expects two values from the function

        if sop_link:
            response_message = f"Here is the SOP for {sop_topic}: {sop_link}"
        else:
            response_message = error_message
    elif intent_name == 'NewPasswordIntent - askUserID' and userID in ('larson', 'smith'): 
        read_sheet_and_send_email(spreadsheet_id, range_name, userID, 'drlarson6@gmail.com')
        response_message = 'Action taken based on Dialogflow response'
    
    # New workaround, 03SEP24
    elif intent_name == 'helpWithStep':
        stepNumber = get_nested(dialogflow_response, 'queryResult', 'parameters', 'stepNumber')
        print(f"Extracted Step Number in 'helpWithStep': {stepNumber}")

        # Store stepNumber in session storage
        if stepNumber:
            session_storage[session_id] = stepNumber
        print(f"Step number stored in session: {session_storage.get(session_id)}")

    elif intent_name == 'helpWithStep - yes':
        # Retrieve stepNumber from session storage
        stepNumber = session_storage.get(session_id, None)  # Use default None if session_id is not found
        print(f"Step number retrieved from session: {stepNumber}")

        if stepNumber:
             # Extract the value from the list if it's wrapped in one
            if isinstance(stepNumber, list):
                stepNumber = stepNumber[0]
                
            # Convert stepNumber to a string for comparison
            stepNumber = str(stepNumber)  # Ensure stepNumber is a string
            print("Checking if stepNumber is truthy...")
            print(stepNumber)
            
            description, sop_link = find_step_info(stepNumber)
            
            print(f"Received description: {description}, SOP link: {sop_link}")
            
            if description:
                response_message = f"The step number is: {stepNumber}. Here is the description: {description}."
                if sop_link:
                    response_message += f" The associated SOP is: {sop_link}."
            else:
                response_message = "I couldn't retrieve the description for this step number. Could you please try again?"
            
        else:
            print("Inside else block")
            response_message = "I couldn't retrieve the step number. Could you please repeat it?"
        
        # This will correctly return the response in JSON format back to Dialogflow
        return jsonify({
            "fulfillmentText": response_message
        })
@app.route('/ask-gpt', methods=['POST'])
def ask_gpt():
    global last_prompt, last_reply

    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or request.form.get("text") or request.values.get("text")
    if not text:
        return jsonify({"error": "missing text"}), 400


    data = request.get_json()
    prompt = (data.get('prompt') or data.get('text') or '').strip()

    handled, reply = handle_session_text(prompt)
    if handled:
        return jsonify({'reply': reply}), 200

    if not prompt:
        return jsonify({'reply': '(No input received)'}), 200

    try:
        if session.mode:
            # Deviation session → GPT-4o only
            if prompt == last_prompt:
                return jsonify({'reply': last_reply})

            reply, _ = ask_gpt(prompt)
            last_prompt = prompt
            last_reply = reply
            #log_chat_to_history(prompt, reply)
            return jsonify({'reply': reply}), 200

        else:
            # Pre-session → Dialogflow
            #df_reply = send_to_dialogflow_text(prompt)  # wrapper returns plain string
            return jsonify({'reply': df_reply}), 200

    except Exception as e:
        print("⚠️ Error in ask_gpt():", e)
        return jsonify({'reply': '(An error occurred.)'}), 500

@app.route('/')
def home():
    return render_template('index.html')  # 'index.html' file should be in your 'templates' folder.

@app.route('/log-microphone-state', methods=['POST'])
def log_microphone_state():
    data = request.get_json()  # Get the JSON data sent from the client
    if data and 'microphoneState' in data:
        print(f"Microphone is {data['microphoneState']}")  # Log the microphone state

    return jsonify(success=True)  # Respond with success


@app.route('/handle-form', methods=['POST'])
def handle_form():
    user_input = request.form.get('user_input', '').strip()

    # 0) Intercept if we’re in GPT-only session (or ending it)
    handled, reply = handle_session_text(user_input)
    if handled:
        return jsonify({'response': reply}), 200

    if not user_input:
        return jsonify({'response': '(No input)'}), 200

    # 1) Dialogflow (optional)
    try:
        df = None  # ensure defined even if you skip DF
        # df = send_to_dialogflow(user_input)  # uncomment when ready
        qr = (df or {}).get('queryResult', {}) or {}
        intent = (qr.get('intent') or {}).get('displayName', '') or ''
        text = qr.get('fulfillmentText') or ''

        if intent == 'startDeviationSession':
            return jsonify({'response': text}), 200

        if intent == 'confirm.start.no':
            return jsonify({'response': text or "Okay, not starting a session."}), 200

        if intent == 'confirm.start.yes':
            try:
                requests.post(CONTROL_HOOK_URL, json={"action": "start_session"}, timeout=2.5)
            except Exception as e:
                app.logger.warning(f'Control hook failed: {e}; enabling session locally.')
                session.mode = True
            return jsonify({'response': "Starting a deviation session now. I’ll guide you through the interview."}), 200

        if text:
            return jsonify({'response': text}), 200

    except Exception as e:
        app.logger.warning(f'DF error: {e} (falling back to GPT)')

    # 2) Fallback: GPT

    # 3) Firestore log (best-effort) 

    from firestore_logger import log_chat   # or `append_log` if that’s the version you kept
    reply, _ = ask_gpt(user_input)

    try:
        app.logger.info("📝 Firestore logging: start")
        log_chat(user_input, reply, meta={"src": "handle-form"})
        app.logger.info("✅ Firestore logging: appended")

    except Exception as e:
        app.logger.warning(f"Firestore logging skipped: {e}")

    # 4) GCS logging (optional best-effort)
    try:
        from gcs_logger import append_jsonl
        append_jsonl(user_input, reply, meta={"src": "handle-form"})
    except Exception as e:
        app.logger.warning(f"GCS logging skipped: {e}")

    return jsonify({'response': reply}), 200

@app.route('/control-hook', methods=['POST'])
def control_hook():
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action")
    sess_type = (data.get("type") or "deviation").lower()

    if action == "start_session":
        if session.mode:
            # Preserve legacy shape; also include current state for debugging
            return jsonify({
                "status": "already_active",
                "mode": "gpt",
                "session": {
                    "mode": bool(session.mode),
                    "type": getattr(session.type, "name", str(session.type)),
                    "id": session.id
                }
            }), 409

        # --- set session type exactly as requested ---
        if sess_type == "spreadsheet":
            session.type = SessionType.SPREADSHEET
        else:
            session.type = SessionType.DEVIATION

        session.mode = True
        dev_session_id = uuid4().hex
        session.id = dev_session_id

        # Optional spreadsheet context (store once at start)
        if session.type == SessionType.SPREADSHEET:
            sid = data.get("spreadsheet_id")
            stitle = data.get("sheet_title")
            if sid:
                setattr(session, "spreadsheet_id", sid)
            if stitle:
                setattr(session, "sheet_title", stitle)

        session_data = {
            "dev_session_id": dev_session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "started",
            "type": session.type.name,   # add type to file for clarity
            "questions": [],
            "responses": []
        }

        try:
            os.makedirs("sessions", exist_ok=True)
            with open(os.path.join("sessions", f"{dev_session_id}.json"), "w") as f:
                json.dump(session_data, f, indent=2)
        except Exception:
            session.mode = False
            session.type = SessionType.NONE
            session.id = None
            logging.exception("Failed to create session file")
            return jsonify({"status": "error", "error": "file_write_failed"}), 500

        logging.info(f"{session.type.name} session started — GPT-4o mode")

        # Preserve legacy keys and ALSO include normalized 'session' block
        return jsonify({
            "status": "ok",
            "dev_session_id": dev_session_id,
            "mode": "gpt",
            "session": {
                "mode": True,
                "type": session.type.name,
                "id": dev_session_id
            }
        }), 200

    elif action == "set_sheet_context":
        # Update spreadsheet target without restarting the session
        if not session.mode or session.type != SessionType.SPREADSHEET:
            return jsonify({"status": "error", "error": "no_active_spreadsheet_session"}), 409

        sid = data.get("spreadsheet_id")
        stitle = data.get("sheet_title")
        missing = []
        if not sid:
            missing.append("spreadsheet_id")
        if not stitle:
            missing.append("sheet_title")
        if missing:
            return jsonify({"status": "needs_input", "missing": missing}), 200

        setattr(session, "spreadsheet_id", sid)
        setattr(session, "sheet_title", stitle)
        logging.info(f"Spreadsheet context updated: id={sid} title={stitle}")

        return jsonify({
            "status": "ok",
            "updated": ["spreadsheet_id", "sheet_title"],
            "session": {
                "mode": True,
                "type": session.type.name,
                "id": session.id
            }
        }), 200

    elif action == "end_session":
        if not session.mode:
            return jsonify({"status": "not_active", "mode": "dialogflow"}), 200

        # Try to mark the session file as ended (best-effort)
        try:
            path = os.path.join("sessions", f"{session.id}.json") if session.id else None
            if path and os.path.exists(path):
                with open(path, "r+") as f:
                    data_json = json.load(f)
                    data_json["status"] = "ended"
                    data_json["ended_at"] = datetime.now(timezone.utc).isoformat()
                    f.seek(0); json.dump(data_json, f, indent=2); f.truncate()
        except Exception:
            logging.exception("Failed to update session file to ended")

        session.mode = False
        session.type = SessionType.NONE
        session.id = None
        logging.info("Session ended — Dialogflow mode")
        return jsonify({"status": "ok", "mode": "dialogflow"}), 200

    return jsonify({"status": "ignored", "mode": "gpt" if session.mode else "dialogflow"}), 200

@app.route('/sheet-create', methods=['POST'])
def sheet_create():
    data = request.get_json(force=True, silent=True) or {}
    spec_id = data.get("spec_id")

    # no JSON at all
    if not data:
        return jsonify({"status": "error", "reason": "no_json"}), 400

    # missing required field
    if not spec_id:
        return jsonify({"status": "needs_input", "missing": ["spec_id"]}), 200

    # look up the spec in registry
    spec = REGISTRY.get(spec_id)
    if not spec:
        return jsonify({"status": "error", "reason": "unknown_spec", "spec_id": spec_id}), 400

    # check for required inputs from spec
    required = [r["name"] for r in spec.get("data_requirements", []) if r.get("required")]
    provided = set(data.keys())  # top-level keys only for now
    missing = [name for name in required if name not in provided]
    if missing:
        return jsonify({"status": "needs_input", "spec_id": spec_id, "missing": missing}), 200

    # log the request
    request_id = uuid4().hex
    entry = {
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": data,
    }
    try:
        os.makedirs("sheet_logs", exist_ok=True)
        log_path = os.path.join("sheet_logs", "sheet_requests.jsonl")
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logging.exception("Failed to write sheet request")
        return jsonify({"status": "error", "error": "file_write_failed"}), 500

    logging.info(f"Sheet create request logged — {request_id}")

    # ---- Create + (optional) seed, with JSON error on failure ----
    try:
        title = data.get("title_hint") or f"Sheet {request_id}"
        spreadsheet_id, sheet_url = create_google_sheet_min(title)

        if spec_id == "sheets.curated_2d.v1":
            append_rows(spreadsheet_id, [
                ["Item", "Qty", "Cost"],
                ["Widget", 2, 12.5],
                ["Gizmo", 1, 8.0],
                ["Whatsit", 3, 3.0],
                ["Totals:", "=SUM(B2:B4)", "=SUM(C2:C4)"],
            ])

        # (optional) persist a mapping for later lookups by request_id
        session_rec = {
            "request_id": request_id,
            "spec_id": spec_id,
            "spreadsheet_id": spreadsheet_id,
            "sheet_url": sheet_url,
            "title": title,
        }
        try:
            session_log = os.path.join("sheet_logs", "sheet_sessions.jsonl")
            with open(session_log, "a") as f:
                f.write(json.dumps(session_rec) + "\n")
        except Exception:
            logging.exception("Failed to write session mapping (non-fatal)")

        return jsonify({
            "status": "ok",
            "request_id": request_id,
            "spec_id": spec_id,
            "spreadsheet_id": spreadsheet_id,
            "sheet_url": sheet_url,
            "message": "logged",
        }), 200

    except Exception as e:
        logging.exception("sheet-create failed")
        return jsonify({"status": "error", "error": str(e)}), 500



def _ctx(name, lifespan=2, params=None):
    """Helper to build a Dialogflow ES context object."""
    return {"name": name, "lifespanCount": lifespan, "parameters": params or {}}

def handle_deviation_intents(req):
    qr = req.get("queryResult") or {}
    intent = (qr.get("intent") or {}).get("displayName", "")
    if intent not in {"startDeviationSession","confirm.start.yes","confirm.start.no"}:
        return None  # not handled here

    conf = float(qr.get("intentDetectionConfidence", 0.0))
    session = req.get("session","")
    contexts = qr.get("outputContexts") or []
    utter = qr.get("queryText","")
    cx = lambda name: f"{session}/contexts/{name}"

    if intent == "startDeviationSession":
        if conf < CONFIDENCE_THRESHOLD:
            return jsonify({}), 200  # stay silent
        return jsonify({
            "fulfillmentText": "I think you want to start a deviation session. Should I begin now? (yes/no)",
            "outputContexts": [{
                "name": cx(CONFIRM_CONTEXT),
                "lifespanCount": 2,
                "parameters": {"first_utterance": utter, "confidence": conf}
            }]
        }), 200

    if intent == "confirm.start.yes":
        has_ctx = any((c.get("name") or "").endswith(f"/contexts/{CONFIRM_CONTEXT}") for c in contexts)
        if not has_ctx:
            return jsonify({"fulfillmentText": "I’m not currently starting a session. Say 'start deviation' first."}), 200

        # Start the deviation “writing” here (no external hook needed)
        dev_session_id = uuid4().hex
        os.makedirs("sessions", exist_ok=True)
        data = {
            "dev_session_id": dev_session_id,
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "status": "started",
            "why": next((c.get("parameters",{}).get("first_utterance","") for c in contexts
                         if (c.get("name") or "").endswith(f"/contexts/{CONFIRM_CONTEXT}")), "")
        }
        with open(os.path.join("sessions", f"{dev_session_id}.json"), "w") as f:
            json.dump(data, f, indent=2)

        return jsonify({
            "fulfillmentText": "Starting a deviation session now. I’ll guide you through the interview.",
            "outputContexts": []
        }), 200

    if intent == "confirm.start.no":
        return jsonify({
            "fulfillmentText": "Okay, not starting a session. If you need one later, just say “start deviation.”",
            "outputContexts": []
        }), 200


@app.route("/dialogflow-webhook", methods=["POST"])
def dialogflow_webhook():
    
    req = request.get_json(force=True)
    qr = req.get("queryResult", {})
    intent = qr.get("intent", {}).get("displayName", "")
    conf = float(qr.get("intentDetectionConfidence", 0.0))
    session_path = req.get("session", "")
    contexts = qr.get("outputContexts", []) or []
    user_utterance = qr.get("queryText", "")

    # Build fully-qualified context name
    def cx(name):
        return f"{session_path}/contexts/{name}"

    if intent == "startDeviationSession":
        if conf < CONFIDENCE_THRESHOLD:
            app.logger.info("Low confidence %.3f < %.3f - ignoring", conf, CONFIDENCE_THRESHOLD)
            return jsonify({})  # silent

        # High confidence — ask for human confirmation & set context
        return jsonify({
            "fulfillmentText": "I think you want to start a deviation session. Should I begin now? (yes/no)",
            "outputContexts": [
                _ctx(cx("awaiting_start_confirmation"), lifespan=2, params={
                    "first_utterance": user_utterance,
                    "confidence": conf
                })
            ]
    })

    elif intent == "confirm.start.yes":
        # Verify confirmation context is active
        ctx_ok = any(c.get("name", "").endswith("/contexts/awaiting_start_confirmation") for c in contexts)
        if not ctx_ok:
            return jsonify({"fulfillmentText": "I’m not currently starting a session. Say 'start deviation' first."})

        # Fire control hook
        payload = {
            "action": "start_session",
            "source": "dialogflow",
            "session_meta": {
                "df_session": session_path,
                "why": next((c.get("parameters", {}).get("first_utterance") for c in contexts
                             if c.get("name","").endswith("/contexts/awaiting_start_confirmation")), ""),
            },
            # ✅ Fixed timestamp line
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        }
        try:
            requests.post(CONTROL_HOOK_URL, json=payload, timeout=2.5)
        except Exception as e:
            return jsonify({"fulfillmentText": f"Attempted to start, but the control hook failed: {e}"})

        return jsonify({
            "fulfillmentText": "Starting a deviation session now. I’ll guide you through the interview.",
            "outputContexts": []
        })

    elif intent == "confirm.start.no":
        return jsonify({
            "fulfillmentText": "Okay, not starting a session. If you need one later, just say “start deviation.”",
            "outputContexts": []
        })

    return jsonify({
        "fulfillmentText": f"No matching handler (intent={intent}, conf={conf})"
    })



def index():
    return render_template('index.html')
    
def find_sop_pdf(sop_topic):
    """Finds the SOP PDF in the Google Drive within a specific folder."""

    # Define your folder ID here where SOPs are stored
    folder_id = '1stIfA9V9rDjFLoBVRQ6lMJEwusM8Kg3M'

    # Construct the query to search for the file within the specified folder
    print(f"sop_topic: {sop_topic}")
    query = f"(name contains '{sop_topic}' and (mimeType='application/pdf' or mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document' or mimeType='application/vnd.google-apps.spreadsheet')) and '{folder_id}' in parents"


    try:
        # Execute the search query
        print(f"Query: {query}")

        search_result = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = search_result.get('files', [])
        print(f"Files found: {files}")

        if not files:
            print(f"No files found with the topic '{sop_topic}'.")
            return None, "SOP PDF not found."

        # Assuming the first file found is the one we want
        else:
            sop_file = files[0]
            sop_file_id = sop_file.get('id')
            print(sop_file_id)
            # Construct a link to the file for viewing
            sop_link = f"https://drive.google.com/file/d/{sop_file_id}/view"
            # This will open the SOP link in the user's default browser
            #webbrowser.open_new_tab(sop_link)

            print(f"SOP link: {sop_link}")
            return sop_link, None

    except Exception as e:
        # Log the exception and return an error message
        print(f"An error occurred: {e}")
        return None, "An error occurred while searching for the SOP PDF."


def get_nested(data, *keys):
    current_level = data
    for key in keys:
        if isinstance(current_level, dict):
            current_level = current_level.get(key)
        elif isinstance(current_level, list) and isinstance(key, int):
            if 0 <= key < len(current_level):
                current_level = current_level[key]
            else:
                return None
        else:
            return None
    return current_level


def send_to_dialogflow(user_input):
    # Hard block DF while a deviation session is active
    if session.mode:
        print("🛑 DF BLOCKED (session.mode=True)")
        return {"queryResult": {}}

    print("➡️ DF CALLED")
    # Refresh credentials to obtain a fresh access token
    credentials.refresh(Request())
    headers = {
        'Authorization': f'Bearer {credentials.token}',
        'Content-Type': 'application/json'
    }
    data = {
        'queryInput': {
            'text': {
                'text': user_input,
                'languageCode': 'en'
            }
        },
        'sessionId': session_id   # this is your DF HTTP session id from the top of the file
    }
    response = requests.post(DIALOGFLOW_ENDPOINT, headers=headers, json=data)
    return response.json()

def send_to_dialogflow_text(user_input):
    # Hard block DF while a deviation session is active
    if session.mode:
        print("🛑 DF_TEXT BLOCKED (session.mode=True)")
        return "(DF disabled during deviation session)"
    print("➡️ DF_TEXT CALLED")
    df = send_to_dialogflow(user_input)
    return df.get("queryResult", {}).get("fulfillmentText", "(No reply)")



# --- the ONLY opener we keep ---
def open_safari_when_ready():
    import time, subprocess, requests
    url = f'http://127.0.0.1:{PORT}/'
    health = f'http://127.0.0.1:{PORT}/health'
    for _ in range(60):  # wait up to ~30s
        try:
            r = requests.get(health, timeout=0.5)
            if r.status_code == 200:
                print(f"[opener] opening Safari → {url}")
                subprocess.run(["open", "-a", "Safari", url])
                return
        except Exception:
            pass
        time.sleep(0.5)
    print("[opener] timed out; not opening browser")

# ---- Helpers ----

#SHEETS_ACTION_URL = "http://127.0.0.1:5055/sheet-action"  # <-- point to your existing boldHeader endpoint if different

SHEET_ACTION_ENDPOINT = "http://127.0.0.1:5055/sheet-action"  # adjust if needed

def call_sheets_action(action: str, args: dict) -> tuple[bool, str]:
    """
    Posts to /sheet-action with both 'params' and 'args' so either schema works.
    Returns (ok, message).
    """
    try:
        # Enrich args with session context (spreadsheet_id / sheet_title)
        enriched = dict(args or {})
        sid = getattr(session, "spreadsheet_id", None)
        stitle = getattr(session, "sheet_title", None)
        if sid is not None:
            enriched.setdefault("spreadsheet_id", sid)
        if stitle is not None:
            enriched.setdefault("sheet_title", stitle)

        # Some handlers expect 'header_row' instead of 'row_number'
        if "row_number" in enriched and "header_row" not in enriched:
            enriched["header_row"] = enriched["row_number"]

        # Log what we’re sending (helps if something still complains)
        try:
            current_app.logger.info(f"[POST] /sheet-action action={action} payload={enriched}")
        except Exception:
            pass

        # Send BOTH keys to be maximally compatible
        payload = {
            "action": action,
            "params": enriched,           # server variant A
            "args": enriched,             # server variant B
            "session_id": session.id,
        }

        r = requests.post(SHEET_ACTION_ENDPOINT, json=payload, timeout=5)

        try:
            current_app.logger.info(f"[RESP] status={r.status_code} body={r.text}")
        except Exception:
            pass

        content_type = r.headers.get("content-type", "")
        data = r.json() if content_type.startswith("application/json") else {}
        if r.ok and data.get("status") in {"ok", "logged", "done", "applied"}:
            return True, data.get("message") or f"{action} applied."
        body = data or r.text
        return False, f"{action} failed with {r.status_code}: {body}"
    except Exception as e:
        return False, f"{action} error: {e}"

def run_bold_header(spreadsheet_id: str, tab: str, row_number: int, alignment: str | None = None):
    """
    Wraps existing sender logic: resolve sheetId, build request via ACTION_BUILDERS["BoldHeader"],
    and call spreadsheets().batchUpdate(...). Returns (ok, message).
    """
    try:
        sheet_id = get_sheet_id(spreadsheet_id, tab)
        params = {"row_number": int(row_number or 1)}
        if alignment:
            params["alignment"] = alignment  # e.g., "CENTER", "LEFT", "RIGHT"

        req = ACTION_BUILDERS["BoldHeader"](sheet_id, params)  # your builder returns ONE request dict
        svc = _sheets_service()
        result = svc.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [req]}
        ).execute()
        return True, f"Bolded header row {params['row_number']} on {tab}"
    except Exception as e:
        app.logger.exception("BoldHeader failed")
        return False, f"bold_failed: {e}"

def run_unbold_header(spreadsheet_id: str, tab: str, row_number: int):
    try:
        sheet_id = get_sheet_id(spreadsheet_id, tab)
        req = ACTION_BUILDERS["UnboldHeader"](sheet_id, {"row_number": int(row_number or 1)})
        svc = _sheets_service()
        svc.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [req]}
        ).execute()
        return True, f"Unbolded header row {row_number} on {tab}"
    except Exception as e:
        app.logger.exception("UnboldHeader failed")
        return False, f"unbold_failed: {e}"


def build_BoldHeader(sheet_id: int, args: dict):
    row_number = int(args.get("row_number", 1))  # catalog expects row_number
    start = max(row_number - 1, 0)               # API uses 0-indexed rows
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": start, "endRowIndex": start + 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "horizontalAlignment": "CENTER"
            }},
            "fields": "userEnteredFormat(textFormat.bold,horizontalAlignment)"
        }
    }

ACTION_BUILDERS = {
    "BoldHeader": build_BoldHeader,
    # add others later, e.g. "FormatAsCurrency": build_FormatAsCurrency,
}

def build_UnboldHeader(sheet_id: int, args: dict):
    row_number = int(args.get("row_number", 1))
    start = max(row_number - 1, 0)
    alignment = (args.get("alignment") or "LEFT").upper()  # or default to previous value
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": start, "endRowIndex": start + 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": False},
                "horizontalAlignment": alignment
            }},
            "fields": "userEnteredFormat(textFormat.bold,horizontalAlignment)"
        }
    }

ACTION_BUILDERS["UnboldHeader"] = build_UnboldHeader


def _sheets_service():
    # reuse the creds from your module
    from search_and_email import creds
    return build("sheets", "v4", credentials=creds)

def get_sheet_id(spreadsheet_id: str, tab_title: str) -> int:
    svc = _sheets_service()
    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab_title:
            return s["properties"]["sheetId"]
    raise ValueError(f"Tab not found: {tab_title}")

# ---- action builders ----
def build_req_bold_header(sheet_id: int, row_number: int):
    # Sheets API uses 0-based indexes, end is exclusive
    start = max(row_number - 1, 0)
    end = start + 1
    return [{
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start,
                "endRowIndex": end
            },
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "horizontalAlignment": "CENTER"
                }
            },
            "fields": "userEnteredFormat(textFormat.bold,horizontalAlignment)"
        }
    }]

def col_letter_to_index(letter: str) -> int:
    # "A" -> 0, "B" -> 1, ... "AA" -> 26
    letter = letter.strip().upper()
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - ord('A') + 1)
    return n - 1

# FormatAsCurrency: inputs ["tab","column"]
def build_FormatAsCurrency(sheet_id: int, args: dict):
    column = args.get("column", "C")
    col = col_letter_to_index(column)
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY"}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    }

# FreezeTopRow: inputs ["tab","rows_to_freeze"]
def build_FreezeTopRow(sheet_id: int, args: dict):
    rows_to_freeze = int(args.get("rows_to_freeze", 1))
    return {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": rows_to_freeze}},
            "fields": "gridProperties.frozenRowCount"
        }
    }

# HighlightNegatives: inputs ["tab","column"]
def build_HighlightNegatives(sheet_id: int, args: dict):
    column = args.get("column", "C")
    col = col_letter_to_index(column)
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startColumnIndex": col,
                    "endColumnIndex": col + 1
                }],
                "booleanRule": {
                    "condition": {"type": "NUMBER_LESS", "values": [{"userEnteredValue": "0"}]},
                    "format": {"textFormat": {"foregroundColorStyle": {"rgbColor": {"red": 1.0}}}}
                }
            },
            "index": 0
        }
    }

# InsertColumns: inputs ["tab","after_column","count"]
def build_InsertColumns(sheet_id: int, args: dict):
    after_column = args.get("after_column", "A")
    count = int(args.get("count", 1))
    start = col_letter_to_index(after_column) + 1
    return {
        "insertDimension": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": start,
                "endIndex": start + count
            },
            "inheritFromBefore": True
        }
    }

# AddTotalRow: inputs ["tab","column","operation"]
# We'll append a new row with a label + a formula over the used range of the column
def build_AddTotalRow(sheet_id: int, args: dict):
    # This builder just returns a "note" for batchUpdate so we can run a separate values.append below.
    # We'll execute values.append in the route (since batchUpdate can't append formulas easily without knowing row).
    col_letter = args.get("column", "C")
    op = (args.get("operation", "SUM") or "SUM").upper()
    if op not in ("SUM", "AVERAGE", "MIN", "MAX", "COUNT"):
        op = "SUM"
    return {"_totals_helper": {"column": col_letter, "operation": op}}

# SortByColumn: inputs ["tab","column","order"]
def build_SortByColumn(sheet_id: int, args: dict):
    column = args.get("column", "A")
    order = (args.get("order", "ASC") or "ASC").upper()
    sort_order = "ASCENDING" if order.startswith("ASC") else "DESCENDING"
    col = col_letter_to_index(column)
    return {
        "sortRange": {
            "range": {"sheetId": sheet_id},  # whole sheet
            "sortSpecs": [{"dimensionIndex": col, "sortOrder": sort_order}]
        }
    }

# Registry: maps your catalog names to the builders above
ACTION_BUILDERS = {
    "BoldHeader": build_BoldHeader,
    "UnboldHeader": build_UnboldHeader,
    "FormatAsCurrency": build_FormatAsCurrency,
    "FreezeTopRow": build_FreezeTopRow,
    "HighlightNegatives": build_HighlightNegatives,
    "InsertColumns": build_InsertColumns,
    "AddTotalRow": build_AddTotalRow,
    "SortByColumn": build_SortByColumn,
    # "CreatePivot": ... (later)
    # "CreateBarChart": ... (later)
    # "AddTrendline": ... (later)
    # "ProtectHeaders": ... (later)
    # "UndoLastChange": ... (special; later)
}

@app.route("/sheet-action", methods=["POST"])
def sheet_action():
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")

    # 🔧 NORMALIZE: accept either "params" or "args" or top-level fields
    params = payload.get("params") or payload.get("args") or {}
    if not params and any(k in payload for k in ("spreadsheet_id", "sheet_title", "row_number", "header_row")):
        # support legacy clients that sent fields at top-level
        params = {
            k: payload.get(k)
            for k in ("spreadsheet_id", "sheet_title", "row_number", "header_row")
            if k in payload
        }

    # 🔁 Alias: row_number → header_row
    if "row_number" in params and "header_row" not in params:
        params["header_row"] = params["row_number"]

    spreadsheet_id = params.get("spreadsheet_id")
    sheet_title = params.get("sheet_title") or "Sheet1"
    header_row = params.get("header_row")

    if not spreadsheet_id:
        return jsonify({"status": "needs_input", "missing": ["spreadsheet_id"]}), 200

    # 🛠️ Handle known actions
    if action == "BoldHeader":
        ok, msg = run_bold_header(spreadsheet_id, sheet_title, header_row)  # alignment optional
        return (jsonify({"status": "ok", "message": msg}), 200) if ok else (jsonify({"status": "error", "error": msg}), 200)

    elif action == "UnboldHeader":
        ok, msg = run_unbold_header(spreadsheet_id, sheet_title, header_row)
        return (jsonify({"status": "ok", "message": msg}), 200) if ok else (jsonify({"status": "error", "error": msg}), 200)

    else:
        return jsonify({"status": "error", "error": f"Unknown action {action}"}), 400


# === Socket.IO handlers (added by patch) ===
@socketio.on("connect")
def _sio_connect():
    app.logger.info("✅ Socket.IO client connected")
    emit("server_hello", {"ok": True})

@socketio.on("user_transcript")
def _sio_user_transcript(data):
    """
    Client emits:  socket.emit("user_transcript", { text: "..." })
    Server emits:  "bot_message" with the reply text.
    """
    try:
        text = (data or {}).get("text", "").strip()
        if not text:
            emit("bot_message", {"text": "(no input)"})
            return

        # 1) Give your session interceptor first shot (routes to GPT if session is active)
        handled, reply = handle_session_text(text)
        if handled:
            emit("bot_message", {"text": reply})
            return

        # 2) Otherwise go through Dialogflow (your wrapper returns plain string)
        df_reply = send_to_dialogflow_text(text)
        emit("bot_message", {"text": df_reply})

    except Exception:
        app.logger.exception("Socket handler error")
        emit("bot_message", {"text": "(error processing message)"})
# === end Socket.IO handlers ===

_storage_client = None
def _gcs():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client

def _append_log(q, r, session_id=None):
    if not USE_GCS_LOG:
        return
    try:
        session_id = session_id or str(uuid.uuid4())
        obj = {
            "t": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "type": "q", "data": q
        }
        obj2 = {
            "t": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "type": "a", "data": r
        }
        bucket = _gcs().bucket(SESSIONS_BUCKET)
        blob = bucket.blob(f"sessions/{session_id}.jsonl")
        # append by read+write (cheap & simple for low volume)
        prev = b""
        if blob.exists():
            prev = blob.download_as_bytes()
        new = (prev + (json.dumps(obj)+"\n"+json.dumps(obj2)+"\n").encode("utf-8"))
        blob.upload_from_string(new, content_type="application/jsonl")
    except Exception as e:
        app.logger.exception(f"GCS append_log failed: {e}")
    
# Detect Cloud Run environment
IS_CLOUDRUN = bool(os.getenv("K_SERVICE"))
PORT = int(os.getenv("PORT", 8080))
HOST = "0.0.0.0" if IS_CLOUDRUN else "127.0.0.1"

if __name__ == "__main__":
    if not IS_CLOUDRUN:  # only open Safari locally
        threading.Thread(target=open_safari_when_ready, daemon=True).start()

    socketio.run(
        app,
        host=HOST,
        port=PORT,
        debug=not IS_CLOUDRUN,
        use_reloader=not IS_CLOUDRUN,
    )

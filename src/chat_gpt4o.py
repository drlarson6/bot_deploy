import os
import json
from openai import OpenAI
from datetime import datetime
from tts_google import synthesize_to_file

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

#HISTORY_FILE = os.path.join(os.getcwd(), "chat_history.json")
#chat_history = []


#last_bot_reply = ""

#HISTORY_PATH = "chat_history.json"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

chat_history = []

#if os.path.exists(HISTORY_FILE):
#    with open(HISTORY_FILE, "r") as f:
#        chat_history = json.load(f)


#@app.route('/ask-gpt', methods=['POST'])
def ask_gpt():
    data = request.get_json()
    prompt = data.get('prompt', '').strip()

    if not prompt:
        return jsonify({'reply': '(No input received)'}), 200

    global last_prompt, last_reply
    try:
        if prompt == last_prompt:
            # Duplicate: speak again, but don't log again
            return jsonify({'reply': last_reply})

        # ✅ Get GPT reply
        reply, control_json = chat_with_gpt(prompt)

        
        # ✅ Update last seen
        last_prompt = prompt
        last_reply = reply

        if control_json:
            handle_control_signal(control_json)


    except Exception as e:
        print("⚠️ Error in ask_gpt():", e)
        return jsonify({'reply': '(An error occurred.)'}), 500

    # ✅ Always log AFTER GPT reply is retrieved
    #log_chat_to_history(prompt, reply_text)

    return jsonify({'reply': reply})



def chat_with_gpt(prompt: str):
    global chat_history
    print("📩 chat_with_gpt() received prompt:", repr(prompt))
    try:
        # build messages (safe if no history)
        message_history = [
            {"role": "system", "content": "You are a concise assistant. Avoid unnecessary pleasantries."}
        ]
        for entry in (chat_history or []):  # tolerate empty/missing history
            message_history.append({"role": "user", "content": entry["prompt"]})
            message_history.append({"role": "assistant", "content": entry["reply"]})
        message_history.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=message_history,
        )
        reply = response.choices[0].message.content

        # record this turn in memory only (no disk)
        chat_history.append({"prompt": prompt, "reply": reply})

        # your control_json logic unchanged…
        control_json = None
        if "deviation" in prompt.lower():
            control_json = {"action": "start_session", "confidence": 0.9}
        return reply, control_json

    except Exception as e:
        print("Error in GPT chat:", e)
        return "Sorry, there was an error talking to GPT.", None
    
import datetime

chat_history = []

print("🟢 Preparing to enter log_chat_to_history()")

def log_chat_to_history(user_text, bot_reply):
    print("🟢 ENTERED log_chat_to_history()")
    global chat_history
    chat_history.append({
        "prompt": user_text,
        "reply": bot_reply,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    })    

def log_chat_to_history(user_text, bot_reply):
    print(f"🟢 FORCED LOG: user_text='{user_text}', bot_reply='{bot_reply}'")

    entry = {
        "timestamp": datetime.now().isoformat(),
        "prompt": user_text,
        "reply": bot_reply
    }

    try:
        #history = []
        #if os.path.exists(HISTORY_FILE):
        #    with open(HISTORY_FILE, "r") as f:
        #        try:
        #            history = json.load(f)
        #        except json.JSONDecodeError:
        #            print("⚠️ Corrupted JSON detected. Starting fresh.")

        #history.append(entry)

        #with open(HISTORY_FILE, "w") as f:
        #    json.dump(history, f, indent=2)
        #    f.flush()
        #    os.fsync(f.fileno())

        print("✅ LOGGING SUCCESSFUL")

    except Exception as e:
        print("❌ Logging failed:", e)

def handle_control_signal(control_json):
    try:
        print("🚀 Forwarding control JSON:", control_json)
        response = requests.post("http://localhost:5000/control-hook", json=control_json)
        print("✅ Control hook response:", response.status_code, response.text)
    except Exception as e:
        print("❌ Failed to send control JSON:", e)


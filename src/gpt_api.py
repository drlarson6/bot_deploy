from openai import OpenAI
import json
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def chat_with_gpt(prompt: str) -> str:
    ...


HISTORY_FILE = "chat_history.json"
chat_history = []

#if os.path.exists(HISTORY_FILE):
#    with open(HISTORY_FILE, "r") as f:
#        chat_history = json.load(f)

print("Chat with GPT-4o. Type 'exit' to quit.\n")

while True:
    user_input = input("You: ")
    if user_input.lower() == "exit":
        break

    chat_history.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=chat_history
    )

    reply = response.choices[0].message.content
    print("GPT-4o:", reply)

    chat_history.append({"role": "assistant", "content": reply})

    #with open(HISTORY_FILE, "w") as f:
    #    json.dump(chat_history, f, indent=2)

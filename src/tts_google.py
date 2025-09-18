# tts_google.py

import os
import logging
from google.cloud import texttospeech

# Configure logging once (at module load time)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize TTS client once
client = texttospeech.TextToSpeechClient()

def synthesize_to_file(text, filename="output.mp3", speaking_rate=1.1, pitch=1.3, voice_name="en-US-Wavenet-D"):
    if not text.strip():
        logging.warning("No text provided to synthesize.")
        return

    input_text = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code="en-US",
        name="en-US-Studio-O"  # Professional, clear female voice
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speaking_rate,
        pitch=pitch,
    )

    try:
        logging.info(f"🔊 Synthesizing speech for: {text[:60]}...")
        response = client.synthesize_speech(
            input=input_text, voice=voice, audio_config=audio_config
        )

        with open(filename, "wb") as out:
            out.write(response.audio_content)
            logging.info(f"✅ Audio written to {os.path.abspath(filename)}")

    except Exception as e:
        logging.error(f"❌ TTS synthesis failed: {e}")

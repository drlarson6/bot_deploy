# tts_bytes.py
from google.cloud import texttospeech

def synthesize_to_bytes(text: str) -> bytes:
    client = texttospeech.TextToSpeechClient()
    resp = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code="en-US", name="en-US-Standard-B"),
        audio_config=texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3),
    )
    return resp.audio_content
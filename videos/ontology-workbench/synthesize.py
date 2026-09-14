"""Render the public Korean script with Polly and retain real speech marks.

Run manually with AWS credentials: python synthesize.py.
Credentials and model responses are never logged or written to source.
"""
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
import json
import subprocess

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent


def render(scene):
    client = boto3.client("polly", region_name="ap-northeast-2",
                         config=Config(connect_timeout=10, read_timeout=90,
                                       retries={"mode": "standard", "max_attempts": 3}))
    output = ROOT / "assets" / "voice"
    output.mkdir(parents=True, exist_ok=True)
    audio, marks = output / (scene["id"] + ".mp3"), output / (scene["id"] + ".json")
    request = dict(Engine="neural", VoiceId="Seoyeon", LanguageCode="ko-KR",
                   TextType="ssml", Text="<speak><prosody rate=\"100%\">" + escape(scene["text"]) + "</prosody></speak>")
    if not audio.exists():
        response = client.synthesize_speech(**request, OutputFormat="mp3", SampleRate="24000")
        with response["AudioStream"] as stream:
            audio.write_bytes(stream.read())
    if not marks.exists():
        response = client.synthesize_speech(**request, OutputFormat="json",
                                           SpeechMarkTypes=["sentence", "word"])
        with response["AudioStream"] as stream:
            marks.write_bytes(stream.read())
    duration = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "default=nw=1:nk=1", str(audio)], text=True).strip())
    return {"id": scene["id"], "path": str(audio.relative_to(ROOT)),
            "duration": duration, "marks": str(marks.relative_to(ROOT)),
            "voice": "Seoyeon", "engine": "neural", "provider": "Amazon Polly"}


if __name__ == "__main__":
    script = json.loads((ROOT / "narration.json").read_text())
    with ThreadPoolExecutor(max_workers=3) as pool:
        voices = list(pool.map(render, script))
    (ROOT / "audio_meta.json").write_text(json.dumps(voices, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"segments": len(voices), "voiceSeconds": round(sum(v["duration"] for v in voices), 3)}))

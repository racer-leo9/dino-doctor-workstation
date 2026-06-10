# -*- coding: utf-8 -*-
"""Extract audio and transcribe to Chinese text using FunASR/Paraformer-zh, with LLM correction."""
import subprocess, sys, json, os, requests
from pathlib import Path

_FFMPEG_DIR = r"D:\JianyingPro\10.6.0.14057"
_FUNASR_MODEL = None


def _get_ffmpeg():
    ffmpeg = str(Path(_FFMPEG_DIR) / "ffmpeg.exe")
    if Path(ffmpeg).exists():
        return ffmpeg
    return "ffmpeg"


def extract_audio(video_path, output_dir=None):
    """Extract audio from video as 16kHz mono WAV using ffmpeg."""
    video_path = Path(video_path)
    if output_dir is None:
        output_dir = video_path.parent
    output_dir = Path(output_dir)
    video_id = video_path.stem.replace("video_", "")
    audio_path = output_dir / f"audio_{video_id}.wav"

    ffmpeg = _get_ffmpeg()
    probe_cmd = [ffmpeg, "-i", str(video_path)]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
    if "Audio:" not in probe_result.stderr:
        print("[TRANSCRIBE] No audio stream, skipping", file=sys.stderr)
        return None

    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"[TRANSCRIBE] Audio extraction failed: {result.stderr[:300]}", file=sys.stderr)
        return None
    return str(audio_path)


def _load_funasr_model():
    """Load FunASR Paraformer-zh model (cached globally)."""
    global _FUNASR_MODEL
    if _FUNASR_MODEL is not None:
        return _FUNASR_MODEL

    print("[TRANSCRIBE] Loading FunASR Paraformer-zh model...", file=sys.stderr)
    from funasr import AutoModel
    _FUNASR_MODEL = AutoModel(
        model="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        device="cpu",
        disable_update=True,
    )
    print("[TRANSCRIBE] FunASR model loaded", file=sys.stderr)
    return _FUNASR_MODEL


def transcribe_funasr(audio_path):
    """Transcribe WAV file using FunASR Paraformer-zh."""
    model = _load_funasr_model()

    print("[TRANSCRIBE] Transcribing with FunASR...", file=sys.stderr)
    result = model.generate(
        input=audio_path,
        batch_size_s=300,
    )

    if not result or len(result) == 0:
        return None

    text = result[0].get("text", "")
    print(f"[TRANSCRIBE] FunASR done: {len(text)} chars", file=sys.stderr)

    return {
        "text": text,
        "segments": [],
        "language": "zh",
    }


def llm_correct_transcript(raw_text, config=None):
    """Use LLM to correct ASR transcription errors."""
    if not raw_text or len(raw_text) < 5:
        return raw_text

    if config is None:
        try:
            import yaml
            config_path = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
        except:
            config = {}

    llm_cfg = config.get("llm", {})
    if not llm_cfg.get("api_key"):
        return raw_text

    api_key = llm_cfg["api_key"]
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    model = llm_cfg.get("model", "gpt-4o-mini")

    try:
        print("[TRANSCRIBE] LLM correcting...", file=sys.stderr)
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a Chinese text correction expert. Fix all typos and ASR errors in the following Chinese transcript. Output only the corrected text, no explanations."},
                    {"role": "user", "content": raw_text}
                ],
                "temperature": 0.1
            }, timeout=120
        )
        resp.raise_for_status()
        corrected = resp.json()["choices"][0]["message"]["content"].strip()
        if corrected:
            print(f"[TRANSCRIBE] LLM correction done: {len(raw_text)} -> {len(corrected)} chars", file=sys.stderr)
            return corrected
    except Exception as e:
        print(f"[TRANSCRIBE] LLM correction failed: {e}", file=sys.stderr)

    return raw_text


def transcribe_video(video_path, output_dir=None):
    """Full pipeline: extract audio -> FunASR transcribe -> LLM correct -> save."""
    video_path = Path(video_path)
    if output_dir is None:
        output_dir = video_path.parent
    output_dir = Path(output_dir)
    video_id = video_path.stem.replace("video_", "")

    # Extract audio
    print("[TRANSCRIBE] Extracting audio...", file=sys.stderr)
    audio_path = extract_audio(video_path, output_dir)
    if not audio_path:
        print("[TRANSCRIBE] No audio stream, returning empty", file=sys.stderr)
        return {
            "text": "",
            "raw_text": "",
            "segments": [],
            "language": "zh",
            "video_id": video_id,
            "no_audio": True,
        }

    # Transcribe with FunASR
    result = transcribe_funasr(audio_path)
    if not result:
        print("[TRANSCRIBE] FunASR transcription failed", file=sys.stderr)
        return {
            "text": "",
            "raw_text": "",
            "segments": [],
            "language": "zh",
            "video_id": video_id,
            "transcribe_failed": True,
        }

    # LLM correction
    raw_text = result["text"]
    corrected_text = llm_correct_transcript(raw_text, config=None)
    result["raw_text"] = raw_text
    result["text"] = corrected_text
    result["audio_path"] = audio_path
    result["video_id"] = video_id

    # Save corrected text
    transcript_path = output_dir / f"transcript_{video_id}.txt"
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(corrected_text)

    # Save full JSON
    json_path = output_dir / f"transcript_{video_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <video_path>", file=sys.stderr)
        sys.exit(1)
    video_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    result = transcribe_video(video_path, output_dir)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.exit(1)
# -*- coding: utf-8 -*-
"""Seedance video generation module via relay API."""
import sys, json, time, requests
from pathlib import Path


def generate_video(prompt, config, duration=5):
    """
    Generate a video using Seedance model.

    Args:
        prompt: Text description of the video to generate
        config: App config dict with LLM settings
        duration: Not directly supported by API, but included for future use

    Returns:
        dict with video_url, task_id, status, or None on failure
    """
    llm_cfg = config.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    model = "doubao-seedance-2-0-fast-260128"

    if not api_key:
        print("[SEEDANCE] No API key", file=sys.stderr)
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Build video generation prompt from script
    video_prompt = _build_prompt(prompt)

    # Submit generation task
    url = f"{base_url.rstrip('/')}/video/generations"
    data = {
        "model": model,
        "prompt": video_prompt,
    }

    try:
        print(f"[SEEDANCE] Submitting: {video_prompt[:80]}...", file=sys.stderr)
        resp = requests.post(url, headers=headers, json=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        task_id = result.get("id", "")
        status = result.get("status", "")
        print(f"[SEEDANCE] Task created: {task_id} ({status})", file=sys.stderr)

        if not task_id:
            print("[SEEDANCE] No task ID returned", file=sys.stderr)
            return None

        # Poll for completion
        poll_url = f"{url}/{task_id}"
        max_polls = 20  # Max 5 minutes (20 * 15s)
        for i in range(max_polls):
            time.sleep(15)
            try:
                r = requests.get(poll_url, headers=headers, timeout=15)
                r.raise_for_status()
                data = r.json()
                status = data.get("status", "")

                if status in ("completed", "succeeded", "done"):
                    video_data = data.get("data", [])
                    if video_data:
                        video_url = video_data[0].get("url", "")
                        print(f"[SEEDANCE] Video ready: {video_url[:80]}...", file=sys.stderr)
                        return {
                            "video_url": video_url,
                            "task_id": task_id,
                            "status": "completed",
                            "prompt": video_prompt,
                        }
                    print("[SEEDANCE] Completed but no video URL", file=sys.stderr)
                    return None

                if status in ("failed", "error"):
                    err = data.get("error", {})
                    print(f"[SEEDANCE] Failed: {err}", file=sys.stderr)
                    return None

                print(f"[SEEDANCE] Poll {i+1}/{max_polls}: {status}", file=sys.stderr)

            except Exception as e:
                print(f"[SEEDANCE] Poll error: {e}", file=sys.stderr)

        print("[SEEDANCE] Timeout waiting for video", file=sys.stderr)
        return None

    except Exception as e:
        print(f"[SEEDANCE] Error: {e}", file=sys.stderr)
        return None


def _build_prompt(script_text):
    """Build a video generation prompt from the optimized script."""
    # Truncate and clean the script for the prompt
    text = script_text[:300].strip()
    # Add style guidance
    prompt = f"短视频画面：{text}。风格：高清、明亮、产品特写、口腔护理类种草视频风格。"
    return prompt


if __name__ == "__main__":
    import yaml
    config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    prompt = sys.argv[1] if len(sys.argv) > 1 else "一个女孩在洗手台前用漱口水漱口，画面干净明亮"
    result = generate_video(prompt, config)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.exit(1)

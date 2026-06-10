# -*- coding: utf-8 -*-
"""
Smart key frame extraction v3: Auto-select 9 frames.
4 Hook + 4 Content + 4 CTA frames, scored by multimodal AI.
Blur detection via Laplacian variance. No interactive selection.
"""
import subprocess, sys, json, os, re, base64, pathlib, shutil
from pathlib import Path


def _find_ffmpeg():
    ff = shutil.which("ffmpeg")
    if ff:
        return str(pathlib.Path(ff).parent)
    for p in [r"D:\JianyingPro\10.6.0.14057", r"C:\ffmpeg\bin"]:
        if pathlib.Path(p, "ffmpeg.exe").exists():
            return p
    return ""


def get_video_duration(video_path):
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            cap.release()
            if fps > 0:
                return frame_count / fps
    except:
        pass
    ffmpeg_dir = _find_ffmpeg()
    ffprobe = str(pathlib.Path(ffmpeg_dir) / "ffprobe.exe") if ffmpeg_dir else "ffprobe"
    cmd = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return float(json.loads(result.stdout).get("format", {}).get("duration", 0))
    except:
        pass
    return 0


def extract_frame_at(video_path, timestamp, output_path):
    ffmpeg_dir = _find_ffmpeg()
    ffmpeg = str(pathlib.Path(ffmpeg_dir) / "ffmpeg.exe") if ffmpeg_dir else "ffmpeg"
    cmd = [
        ffmpeg, "-y", "-ss", f"{timestamp:.2f}",
        "-i", str(video_path),
        "-vframes", "1", "-q:v", "2",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.returncode == 0 and Path(output_path).exists()
    except:
        return False


def _laplacian_variance(image_path):
    """Compute Laplacian variance - higher = sharper."""
    try:
        import cv2
        img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return cv2.Laplacian(img, cv2.CV_64F).var()
    except:
        return 0.0


def _is_blurry(image_path, threshold=80.0):
    """Check if image is blurry (Laplacian variance below threshold)."""
    return _laplacian_variance(image_path) < threshold


def _image_to_base64(image_path):
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except:
        return None


def _assign_section(ts, duration):
    """Assign frame to hook / content / cta based on timestamp."""
    hook_end = duration * 0.20
    cta_start = duration * 0.80
    if ts < hook_end:
        return "hook"
    elif ts >= cta_start:
        return "cta"
    return "content"


def _score_dimension_label(dim):
    labels = {
        "hook":    "钩子冲击力 — 画面是否在前1.5秒内能抓住注意力、引发好奇或情绪共鸣",
        "content": "内容信息密度 — 画面是否承载了核心卖点、使用场景、对比效果等关键信息",
        "cta":     "CTR引导力 — 画面是否能刺激用户点击、购买、评论等行动",
    }
    return labels.get(dim, dim)


def score_frames_with_vision(candidates, transcript_text, config):
    """
    Score each candidate frame with multimodal AI across 3 dimensions:
    hook_score, content_score, cta_score (1-10 each).
    Also returns a blur flag.
    """
    if not config or not config.get("llm", {}).get("api_key"):
        print("[FRAMES] No LLM config, skipping vision scoring", file=sys.stderr)
        return candidates

    llm_cfg = config.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    model = llm_cfg.get("model", "gpt-4o-mini")

    import requests as _req

    # Score in batches of 6 to avoid payload too large
    batch_size = 6
    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start:batch_start + batch_size]

        content = []
        content.append({
            "type": "text",
            "text": (
                "你是短视频关键帧分析专家。以下是同一个短视频在不同时间点截取的画面帧。\n\n"
                f"【视频文案】{transcript_text[:500]}\n\n"
                "请对每帧从以下三个维度打分（1-10分）：\n"
                "1. hook_score（钩子冲击力）：画面是否能瞬间抓住注意力、引发好奇或情绪共鸣\n"
                "2. content_score（内容信息密度）：画面是否承载了核心卖点、使用场景、对比效果等关键信息\n"
                "3. cta_score（CTR引导力）：画面是否能刺激用户点击、购买等行动\n\n"
                "返回 JSON 数组，每项: {\"index\": 帧编号, \"hook_score\": N, \"content_score\": N, \"cta_score\": N, \"description\": \"画面简述\"}\n"
                "只返回 JSON，不要解释。"
            )
        })

        for c in batch:
            b64 = _image_to_base64(c["path"])
            if b64:
                content.append({"type": "text", "text": f"--- 帧 {c['index']} (时间: {c['timestamp']}s) ---"})
                content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

        try:
            resp = _req.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Return only valid JSON arrays."},
                        {"role": "user", "content": content}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2000,
                },
                timeout=120,
            )
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            # Extract JSON from possible markdown fences
            m = re.search(r'\[.*\]', raw, re.DOTALL)
            if m:
                scores = json.loads(m.group())
            else:
                scores = json.loads(raw)

            # Map scores back to candidates
            score_map = {s["index"]: s for s in scores}
            for c in batch:
                s = score_map.get(c["index"], {})
                c["hook_score"] = s.get("hook_score", 5)
                c["content_score"] = s.get("content_score", 5)
                c["cta_score"] = s.get("cta_score", 5)
                c["ai_description"] = s.get("description", "")

            print(f"[FRAMES] Vision scored batch {batch_start}-{batch_start+len(batch)}", file=sys.stderr)
        except Exception as e:
            print(f"[FRAMES] Vision scoring batch failed: {e}", file=sys.stderr)
            for c in batch:
                c.setdefault("hook_score", 5)
                c.setdefault("content_score", 5)
                c.setdefault("cta_score", 5)
                c.setdefault("ai_description", "")

    return candidates


def auto_select_9_frames(candidates, duration, blur_threshold=80.0):
    """
    Auto-select 9 frames: 3 hook + 4 content + 2 CTA.
    - Filter out blurry frames.
    - Rank by the dimension-specific score within each section.
    - If not enough frames in a section, borrow from adjacent.
    """
    # Step 1: Filter out blurry frames
    sharp = []
    blurry_count = 0
    for c in candidates:
        lv = _laplacian_variance(c["path"])
        c["laplacian_var"] = round(lv, 1)
        if lv >= blur_threshold:
            sharp.append(c)
        else:
            blurry_count += 1
    print(f"[FRAMES] {blurry_count}/{len(candidates)} frames filtered as blurry (threshold={blur_threshold})", file=sys.stderr)

    if not sharp:
        print("[FRAMES] All frames blurry, using all candidates", file=sys.stderr)
        sharp = candidates

    # Step 2: Assign sections
    for c in sharp:
        c["section"] = _assign_section(c["timestamp"], duration)

    # Step 3: Group by section
    groups = {"hook": [], "content": [], "cta": []}
    for c in sharp:
        groups[c["section"]].append(c)

    # Step 4: Score-field mapping per section
    score_key = {"hook": "hook_score", "content": "content_score", "cta": "cta_score"}

    # Step 5: Select top 3 per section
    selected = []
    for section in ["hook", "content", "cta"]:
        key = score_key[section]
        selected_ids = {c["path"] for c in selected}
        pool = [c for c in groups[section] if c["path"] not in selected_ids]
        # Sort by the dimension score descending, then by laplacian_var descending
        pool.sort(key=lambda x: (x.get(key, 0), x.get("laplacian_var", 0)), reverse=True)
        section_limit = {"hook": 3, "content": 4, "cta": 2}.get(section, 4)
        picked = pool[:section_limit]

        # If not enough, borrow from other sections
        if len(picked) < section_limit:
            picked_ids = {c["path"] for c in picked}
            remaining = [c for c in sharp if c["path"] not in picked_ids and c["path"] not in selected_ids]
            remaining.sort(key=lambda x: (x.get(key, 0), x.get("laplacian_var", 0)), reverse=True)
            for c in remaining:
                if len(picked) >= section_limit:
                    break
                picked.append(c)

        selected.extend(picked)

    # Step 6: Sort final selection by timestamp for timeline order
    selected.sort(key=lambda x: x["timestamp"])

    # Step 7: Assign display info
    for i, c in enumerate(selected):
        c["final_index"] = i + 1
        dim_key = score_key[c["section"]]
        c["primary_score"] = c.get(dim_key, 0)
        c["role"] = {
            "hook": "钩子画面",
            "content": "内容画面",
            "cta": "转化画面",
        }.get(c["section"], "内容画面")

    return selected


def extract_frames(video_path, count=9, output_dir=None, config=None, transcript_text=""):
    """
    Main entry: Extract 9 key frames automatically.
    3 hook + 4 content + 2 CTA, AI-scored, blur-filtered.
    No interactive selection needed.
    """
    video_path = Path(video_path)
    video_id = video_path.stem.replace("video_", "")
    if output_dir is None:
        output_dir = video_path.parent / f"frames_{video_id}"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    duration = get_video_duration(video_path)
    if duration <= 0:
        return {"frames": [], "stages": [], "candidate_groups": []}

    # Adaptive sampling interval
    interval = 2.0
    if duration < 15:
        interval = 1.0
    elif duration > 60:
        interval = 3.0

    print(f"[FRAMES] Duration: {duration:.1f}s, sampling every {interval}s", file=sys.stderr)

    # Step 1: Extract candidate frames at regular intervals
    candidates = []
    t = 0.5
    while t < duration - 0.5:
        ts = round(t, 2)
        idx = len(candidates) + 1
        output_path = output_dir / f"candidate_{idx:02d}.png"
        success = extract_frame_at(video_path, ts, output_path)
        if success:
            candidates.append({
                "index": idx,
                "path": str(output_path),
                "timestamp": ts,
            })
        t += interval

    # For very short videos, use smaller interval
    if len(candidates) < 6:
        interval = max(0.5, duration / 8)
        candidates = []
        t = 0.5
        while t < duration - 0.5:
            ts = round(t, 2)
            idx = len(candidates) + 1
            output_path = output_dir / f"candidate_{idx:02d}.png"
            success = extract_frame_at(video_path, ts, output_path)
            if success:
                candidates.append({
                    "index": idx,
                    "path": str(output_path),
                    "timestamp": ts,
                })
            t += interval

    if not candidates:
        print("[FRAMES] No candidates extracted", file=sys.stderr)
        return {"frames": [], "stages": [], "candidate_groups": []}

    print(f"[FRAMES] Extracted {len(candidates)} candidate frames", file=sys.stderr)

    # Step 2: AI vision scoring
    if config and transcript_text:
        candidates = score_frames_with_vision(candidates, transcript_text, config)
    else:
        print("[FRAMES] No config/transcript, using position-based scoring", file=sys.stderr)
        for c in candidates:
            pos_ratio = c["timestamp"] / duration if duration > 0 else 0.5
            c["hook_score"] = round(5.0 + 3.0 * (1 - pos_ratio), 1)
            c["content_score"] = round(5.0 + 2.0 * (1 - abs(pos_ratio - 0.5) * 2), 1)
            c["cta_score"] = round(5.0 + 3.0 * pos_ratio, 1)

    # Step 3: Auto-select 9 frames
    selected = auto_select_9_frames(candidates, duration)

    # Step 4: Copy selected frames to final output with section-based naming
    section_cn = {"hook": "钩子", "content": "内容", "cta": "转化"}
    section_counters = {"hook": 0, "content": 0, "cta": 0}
    final_frames = []
    for i, c in enumerate(selected):
        sec = c.get("section", "content")
        section_counters[sec] = section_counters.get(sec, 0) + 1
        sec_label = section_cn.get(sec, sec)
        file_name = f"{sec_label}-{section_counters[sec]}.png"
        dst = output_dir / file_name
        src = Path(c["path"])
        if src.exists():
            shutil.copy2(str(src), str(dst))
        frame_info = {
            "index": i + 1,
            "path": str(dst),
            "file_name": file_name,
            "timestamp": c["timestamp"],
            "section": sec,
            "section_cn": sec_label,
            "role": c.get("role", ""),
            "score": c.get("primary_score", 0),
            "hook_score": c.get("hook_score", 0),
            "content_score": c.get("content_score", 0),
            "cta_score": c.get("cta_score", 0),
            "laplacian_var": c.get("laplacian_var", 0),
            "ai_description": c.get("ai_description", ""),
        }
        final_frames.append(frame_info)

    frame_urls = [
        f"/data/{os.path.relpath(str(output_dir / f['file_name']), str(output_dir.parent)).replace(chr(92), '/')}"
        for f in final_frames
    ]

    result = {
        "video_id": video_id,
        "video_path": str(video_path),
        "frame_count": len(final_frames),
        "duration": duration,
        "method": "auto_select_9_ai_scored",
        "stages": [f["section"] for f in final_frames],
        "frames": final_frames,
        "frame_urls": frame_urls,
        "candidate_groups": [],  # Empty - no interactive selection
        "output_dir": str(output_dir),
    }

    with open(output_dir / "frames_meta.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[FRAMES] Auto-selected {len(final_frames)} frames: "
          f"{sum(1 for f in final_frames if f['section']=='hook')} hook + "
          f"{sum(1 for f in final_frames if f['section']=='content')} content + "
          f"{sum(1 for f in final_frames if f['section']=='cta')} cta",
          file=sys.stderr)
    return result


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 2:
        print("Usage: python extract_frames.py <video_path>", file=_sys.stderr)
        _sys.exit(1)
    video_path = _sys.argv[1]
    result = extract_frames(video_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))

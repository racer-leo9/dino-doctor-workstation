# -*- coding: utf-8 -*-
"""
Monitor Feishu Bitable for new video links and auto-analyze.
Polls the table, finds records where 视频链接 is set but 拆解状态 != 已完成,
runs the LLM-first analysis pipeline, and updates the existing record.
"""
import sys, os, time, json, traceback
from pathlib import Path
from datetime import datetime

scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

import yaml
import requests
from download_video import download_video
from transcribe import transcribe_video
from extract_frames import extract_frames
from feishu_upload import FeishuHelper

# Reuse analysis functions from analyze_and_push (LLM-first + rule fallback)
from num_helper import safe_int
from analyze_and_push import (
    analyze_structure, analyze_key_frames,
    extract_viral_elements, generate_optimization,
    generate_optimized_script
)

CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"
POLL_INTERVAL = 10  # seconds


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_pending_records(helper):
    """Find records with 视频链接 set but 拆解状态 != 已完成."""
    url = f"{helper.BASE}/bitable/v1/apps/{helper.app_token}/tables/{helper.table_id}/records"
    params = {"page_size": 100}
    resp = requests.get(url, headers=helper._headers(), params=params)
    d = resp.json()
    if d.get("code") != 0:
        print(f"  [ERROR] Failed to list records: {d}", file=sys.stderr)
        return []

    pending = []
    for item in d.get("data", {}).get("items", []):
        fields = item.get("fields", {})
        record_id = item.get("record_id", "")
        video_link = fields.get("视频链接")
        status = fields.get("拆解状态")

        if not video_link:
            continue
        if status == "已完成" or status == "处理中":
            continue

        if isinstance(video_link, dict):
            video_link = video_link.get("link", video_link.get("text", ""))

        if video_link:
            pending.append({
                "record_id": record_id,
                "video_url": str(video_link).strip(),
                "fields": fields,
            })

    return pending


def analyze_and_update(record_info, helper, config):
    """Run analysis pipeline and update the existing record."""
    record_id = record_info["record_id"]
    url = record_info["video_url"]
    data_dir = Path(__file__).parent.parent.parent / "data" / record_id
    data_dir.mkdir(parents=True, exist_ok=True)

    llm_enabled = config.get("llm", {}).get("enabled") and config["llm"].get("api_key")
    print(f"\n{'='*60}")
    print(f"  Analyzing: {url}")
    print(f"  Record ID: {record_id}")
    print(f"  Engine: {'LLM + rule fallback' if llm_enabled else 'Rule only'}")
    print(f"{'='*60}")

    # Mark as 处理中
    helper.update_record(record_id, {"拆解状态": "处理中"})

    try:
        # Step 1: Download
        print("\n  [1/6] Downloading video...")
        meta = download_video(url, str(data_dir))
        if not meta or not meta.get("video_path"):
            raise RuntimeError("Download failed")

        video_path = meta["video_path"]
        video_id = meta.get("video_id", "unknown")
        print(f"    -> {video_path} ({meta.get('duration', 0)}s)")

        # Step 2: Transcribe
        print("\n  [2/6] Transcribing audio...")
        transcript = transcribe_video(video_path, str(data_dir))
        transcript_text = transcript["text"] if transcript else ""
        print(f"    -> {len(transcript_text)} chars")

        # Step 3: Extract frames
        print("\n  [3/6] Extracting key frames...")
        frame_result = extract_frames(video_path, count=6, output_dir=str(data_dir / "frames"))
        frame_paths = [f["path"] for f in frame_result.get("frames", [])]
        print(f"    -> {len(frame_paths)} frames")

        # Step 4: Analyze (LLM-first)
        print("\n  [4/6] Analyzing content...")
        title = meta.get("title", "Unknown")
        structure = analyze_structure(transcript_text, title, config)
        frame_analysis = analyze_key_frames(transcript_text, title, frame_paths, config)
        viral_elements = extract_viral_elements(meta, transcript_text, structure, config)
        optimization = generate_optimization(title, structure, viral_elements, config)
        optimized_script = generate_optimized_script(title, transcript_text, structure, optimization, config)

        structure_text = (
            f"=== {structure.get('part1_label', 'Part 1')} ===\n"
            f"{structure.get('part1_text', '')}\n"
            f"[分析] {structure.get('part1_analysis', '')}\n\n"
            f"=== {structure.get('part2_label', 'Part 2')} ===\n"
            f"{structure.get('part2_text', '')}\n"
            f"[分析] {structure.get('part2_analysis', '')}\n\n"
            f"=== {structure.get('part3_label', 'Part 3')} ===\n"
            f"{structure.get('part3_text', '')}\n"
            f"[分析] {structure.get('part3_analysis', '')}"
        )

        frames_text = "\n".join(
            f"[帧{f.get('frame', i+1)}] {f.get('role', '')} ({f.get('stage', '')})"
            for i, f in enumerate(frame_analysis)
        )

        # Step 5: Upload files
        print("\n  [5/6] Uploading files to Feishu...")
        fields_update = {
            "视频主题": title,
            "视频平台": meta.get("platform", "抖音"),
            "视频时长_秒": meta.get("duration", 0),
            "原文案": transcript_text,
            "文案结构分析": structure_text,
            "核心画面": frames_text,
            "爆款元素": viral_elements if isinstance(viral_elements, list) else [viral_elements],
            "优化方向": "\n".join(f"{i+1}. {s}" for i, s in enumerate(optimization)),
            "优化后文案": optimized_script,
            "拆解状态": "已完成",
            "拆解时间": int(datetime.now().timestamp() * 1000),
            # LLM 增强字段
            "钩子类型": structure.get("hook_type", ""),
            "钩子评分": safe_int(structure.get("hook_score", 0)),
            "情绪曲线": structure.get("emotion_arc", ""),
            "主导情绪": structure.get("dominant_emotion", ""),
            "情绪强度": safe_int(structure.get("emotion_intensity", 0)),
            "标题公式": structure.get("title_formula", ""),
            "CTA类型": structure.get("cta_type", ""),
            "内容评分": safe_int(structure.get("content_score", 0)),
        }

        # Upload video
        if video_path:
            vt = helper.upload_file(video_path)
            if vt:
                fields_update["原视频文件"] = [{"file_token": vt}]

        # Upload frames
        if frame_paths:
            tokens = []
            for fp in frame_paths:
                t = helper.upload_image(fp)
                if t:
                    tokens.append({"file_token": t})
            if tokens:
                fields_update["关键帧图片"] = tokens

        # Step 6: Update record
        print("\n  [6/6] Updating Feishu record...")
        ok = helper.update_record(record_id, fields_update)

        if ok:
            print(f"\n  [OK] Analysis complete! Record {record_id} updated.")
        else:
            print(f"\n  [FAIL] Failed to update record {record_id}.")

        # Save local result
        result = {
            "record_id": record_id,
            "video_url": url,
            "title": title,
            "duration": meta.get("duration", 0),
            "transcript_length": len(transcript_text),
            "viral_elements": viral_elements,
            "engine": "llm" if llm_enabled else "rule",
            "success": ok,
            "timestamp": datetime.now().isoformat(),
        }
        with open(data_dir / "result.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        return ok

    except Exception as e:
        print(f"\n  [FAIL] Error: {e}", file=sys.stderr)
        traceback.print_exc()
        helper.update_record(record_id, {"拆解状态": "失败"})
        return False


def main():
    config = load_config()
    llm_enabled = config.get("llm", {}).get("enabled") and config["llm"].get("api_key")

    print("=" * 60)
    print("  Feishu Video Monitor - Auto Analysis")
    print(f"  Engine: {'LLM + rule fallback' if llm_enabled else 'Rule only'}")
    print(f"  Polling every {POLL_INTERVAL}s")
    print("=" * 60)

    helper = FeishuHelper(
        config["feishu"]["app_id"],
        config["feishu"]["app_secret"],
        config["feishu"]["bitable"]["app_token"],
        config["feishu"]["bitable"]["table_id"],
    )

    processed = set()

    while True:
        try:
            pending = get_pending_records(helper)
            new_records = [r for r in pending if r["record_id"] not in processed]

            if new_records:
                print(f"\n  Found {len(new_records)} new record(s) to analyze.")
                for record in new_records:
                    success = analyze_and_update(record, helper, config)
                    if success:
                        processed.add(record["record_id"])
            else:
                print(".", end="", flush=True)

        except KeyboardInterrupt:
            print("\n\n  Monitor stopped.")
            break
        except Exception as e:
            print(f"\n  [ERROR] {e}", file=sys.stderr)
            traceback.print_exc()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()




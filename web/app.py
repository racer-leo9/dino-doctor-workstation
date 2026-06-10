# -*- coding: utf-8 -*-
"""
短视频拆解 Web 应用
Flask 后端 + SSE 实时进度 + 静态文件服务
"""
import os
import sys, os, json, time, threading, uuid, re
_ffmpeg_dir = r"D:\JianyingPro\10.6.0.14057"; os.environ["PATH"] = _ffmpeg_dir + ";" + os.environ.get("PATH", "") if os.path.isdir(_ffmpeg_dir) else None
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime

scripts_dir = Path(__file__).parent.parent / "skills" / "video-analyzer" / "scripts"
sys.path.insert(0, str(scripts_dir))

from flask import Flask, render_template, request, jsonify, Response, send_from_directory, send_file
from flask_compress import Compress
import yaml

import requests
from download_video import download_video
from transcribe import transcribe_video
from extract_frames import extract_frames
from feishu_upload import FeishuHelper
from seedance_gen import generate_video
from image_gen import generate_reference_image
import llm_analyzer
from analyze_and_push import analyze_key_frames
from scrape_douyin import scrape_douyin_data
from search_douyin import search_douyin_videos
from num_helper import safe_int

DATA_DIR = Path(__file__).parent.parent / "data"

# === Report Library ===
REPORTS_FILE = DATA_DIR / "reports.json"

def save_image_locally(image_data):
    """Save a generated image (URL or base64) to local data directory. Returns local URL path."""
    import base64
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    filename = f"ai_img_{ts}.png"
    filepath = DATA_DIR / filename
    
    if image_data.startswith("data:image"):
        # Base64 data URI
        header, b64 = image_data.split(",", 1)
        img_bytes = base64.b64decode(b64)
        with open(filepath, "wb") as f:
            f.write(img_bytes)
    elif image_data.startswith("http"):
        # Remote URL - download
        try:
            resp = requests.get(image_data, timeout=60)
            if resp.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(resp.content)
            else:
                return image_data  # fallback to remote URL
        except Exception:
            return image_data
    else:
        return image_data
    
    return f"/data/{filename}"

def save_video_locally(video_url):
    """Download a generated video to local data directory. Returns local URL path."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    filename = f"seedance_{ts}.mp4"
    filepath = DATA_DIR / filename
    
    if video_url.startswith("http"):
        try:
            resp = requests.get(video_url, timeout=120, stream=True)
            if resp.status_code == 200:
                with open(filepath, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                return f"/data/{filename}"
            else:
                return video_url
        except Exception:
            return video_url
    return video_url

def load_reports():
    if REPORTS_FILE.exists():
        try:
            with open(REPORTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []

def save_report(report):
    reports = load_reports()
    reports.insert(0, report)
    REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    return report

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
Compress(app)
@app.after_request
def add_cache_headers(response):
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    path = request.path
    if path.startswith('/data/') and ('.png' in path or '.jpg' in path or '.mp4' in path or '.webp' in path):
        response.headers['Cache-Control'] = 'public, max-age=86400'
    elif path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

# Configure logging
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(str(Path(__file__).parent / "app.log"), encoding="utf-8"),
    ]
)
app.logger.setLevel(logging.INFO)

def load_config():
    config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# === AI Generation Config ===
_cfg = load_config()
_llm = _cfg.get("llm", {})
AI_BASE_URL = _llm.get("base_url", "https://api.openai.com/v1")
AI_API_KEY = _llm.get("api_key", "")
VIDEO_MODEL = "doubao-seedance-2-0-fast-260128"
IMAGE_MODEL = "gpt-image-2-pro"
AI_VIDEO_TASKS = {}

tasks = {}
tasks_lock = threading.RLock()
TASK_TTL_SECONDS = 6 * 60 * 60
analysis_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis")

@app.route("/data/<path:filepath>")
def serve_data(filepath):
    """提供 data 目录下的静态文件，支持 Range 请求（视频播放必须）"""
    import mimetypes
    full_path = DATA_DIR / filepath
    if not full_path.exists():
        return "Not Found", 404
    file_size = full_path.stat().st_size
    content_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
    range_header = request.headers.get("Range")
    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1
            def generate():
                with open(full_path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(8192, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            resp = Response(generate(), status=206, content_type=content_type)
            resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            resp.headers["Content-Length"] = str(length)
            resp.headers["Accept-Ranges"] = "bytes"
            return resp
    def generate_full():
        with open(full_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk
    resp = Response(generate_full(), content_type=content_type)
    resp.headers["Content-Length"] = str(file_size)
    resp.headers["Accept-Ranges"] = "bytes"
    return resp

def run_analysis(task_id, url, selected_steps=None):
    task = tasks.get(task_id)
    if not task:
        app.logger.warning('Task %s not found', task_id)
        return
    config = load_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ALL_STEPS = ["download","scrape","transcribe","frames","structure","competitor","viral","optimize"]
    if not selected_steps:
        selected_steps = ALL_STEPS
    def should_run(step):
        return step in selected_steps

    def update_progress(step, status, detail="", extra=None):
        item = {"step": step, "status": status, "detail": detail, "time": time.strftime("%H:%M:%S")}
        if extra:
            item.update(extra)
        task["progress"].append(item)
        task["current_step"] = step

    try:
        # Step 1: 下载
        meta = None
        if not should_run("download"):
            update_progress("download", "skipped", "已跳过")
        else:
            update_progress("download", "running", "正在下载视频...")
            meta = download_video(url, str(DATA_DIR))
        if should_run("download") and (not meta or not meta.get("video_path")):
            update_progress("download", "failed", "下载失败")
            task["status"] = "failed"
            return
        if not meta:
            meta = {}

        video_path = meta["video_path"]
        video_id = meta["video_id"]
        video_rel = os.path.relpath(video_path, str(DATA_DIR)).replace("\\", "/")
        filesize_kb = meta.get("filesize", 0) // 1024
        update_progress("download", "done", f"下载完成 ({filesize_kb}KB)", {"video_url": f"/data/{video_rel}"})

        # Step 1.5: 抓取抹音数据 (OpenCLI)
        scraped = {}
        if not should_run("scrape"):
            update_progress("scrape", "skipped", "已跳过")
        else:
            update_progress("scrape", "running", "正在抓取视频数据...")
            scraped = scrape_douyin_data(url, video_id)
        if scraped.get("scrape_ok"):
            detail_parts = []
            if scraped.get("likes"): detail_parts.append(f"点赞{scraped['likes']}")
            if scraped.get("comments"): detail_parts.append(f"评论{scraped['comments']}")
            if scraped.get("favorites"): detail_parts.append(f"收藏{scraped['favorites']}")
            update_progress("scrape", "done", " ✔ ".join(detail_parts) if detail_parts else "数据抓取完成", {
                "scraped_data": scraped
            })
        else:
            update_progress("scrape", "done", "数据抓取跳过", {"scraped_data": scraped})

        # Step 2: 转录 + LLM纠错
        if not should_run("transcribe"):
            update_progress("transcribe", "skipped", "已跳过")
            transcript = None
        else:
            update_progress("transcribe", "running", "正在转录音频...")
            transcript = transcribe_video(video_path, str(DATA_DIR))
        transcript_text = transcript["text"] if transcript else ""
        raw_text = transcript.get("raw_text", transcript_text) if transcript else ""
        update_progress("transcribe", "done", f"转录完成 ({len(transcript_text)}字)", {
            "raw_text": raw_text, "corrected_text": transcript_text
        })

        # Step 3: 均匀抽帧 + 多模态模型智能打分选帧（交互式选择）
        if not should_run("frames"):
            update_progress("frames", "skipped", "已跳过")
            frame_paths = []
            frame_urls = []
            frame_stages = []
        else:
            update_progress("frames", "running", "正在抽取候选帧 + AI打分...")
            frame_result = extract_frames(
                video_path, count=9,
                output_dir=str(DATA_DIR / f"frames_{video_id}"),
                config=config, transcript_text=transcript_text
            )
            frame_paths = [f["path"] for f in frame_result.get("frames", [])]
            frame_urls = frame_result.get("frame_urls", [])
            if not frame_urls:
                frame_urls = ["/data/{}".format(os.path.relpath(p, str(DATA_DIR)).replace(chr(92), "/")) for p in frame_paths]
            frame_stages = [f.get("section", "") for f in frame_result.get("frames", [])]
            frame_scores = [f.get("score", 0) for f in frame_result.get("frames", [])]
            hook_n = sum(1 for f in frame_result.get("frames", []) if f.get("section") == "hook")
            content_n = sum(1 for f in frame_result.get("frames", []) if f.get("section") == "content")
            cta_n = sum(1 for f in frame_result.get("frames", []) if f.get("section") == "cta")
            update_progress("frames", "done", f"AI自动选取 {len(frame_paths)} 帧: {hook_n}钩子 + {content_n}内容 + {cta_n}转化", {
                "frame_urls": frame_urls, "frame_stages": frame_stages, "frame_scores": frame_scores
            })
        # Step 4: 文案结构拆解
        title = meta.get("title", "Unknown")

        if not should_run("structure"):
            update_progress("structure", "skipped", "已跳过")
            structure = {}
        else:
            update_progress("structure", "running", "正在分析文案结构...")
            from analyze_and_push import analyze_structure, extract_viral_elements, generate_optimization, generate_optimized_script
            structure = analyze_structure(transcript_text, title, config)

        # Generate product title from analysis
        # Step 4.5: 竞品差异分析
        if not should_run("competitor"):
            update_progress("competitor", "skipped", "已跳过")
            competitor = {}
        else:
            update_progress("competitor", "running", "正在分析竞品差异...")
        competitor = llm_analyzer.llm_competitor_analysis(title, transcript_text, structure, config)
        if competitor:
            update_progress("competitor", "done", "竞品分析完成", {"competitor": competitor})
        else:
            update_progress("competitor", "done", "竞品分析跳过", {"competitor": {}})

        update_progress("structure", "done", f"钩子类型: {structure.get('hook_type', '未知')}", {
            "hook_type": structure.get("hook_type", ""),
            "hook_score": safe_int(structure.get("hook_score", 0)),
            "emotion_arc": structure.get("emotion_arc", ""),
            "dominant_emotion": structure.get("dominant_emotion", ""),
            "emotion_intensity": safe_int(structure.get("emotion_intensity", 0)),
            "cta_type": structure.get("cta_type", ""),
            "content_score": safe_int(structure.get("content_score", 0)),
            "title_formula": structure.get("title_formula", ""), "hook_detail": structure.get("hook_detail", ""), "cta_detail": structure.get("cta_detail", ""), "rhetoric": structure.get("rhetoric", []), "psychology_triggers": structure.get("psychology_triggers", []), "target_audience": structure.get("target_audience", ""), "pain_points": structure.get("pain_points", []), "selling_points": structure.get("selling_points", []),
            "content_rhythm": structure.get("content_rhythm", ""),
            "trust_building": structure.get("trust_building", ""),
            "platform_adaptation": structure.get("platform_adaptation", ""),
            "part1_text": structure.get("part1_text", ""),
            "part1_analysis": structure.get("part1_analysis", ""),
            "part2_text": structure.get("part2_text", ""),
            "part2_analysis": structure.get("part2_analysis", ""),
            "part3_text": structure.get("part3_text", ""),
            "part3_analysis": structure.get("part3_analysis", ""),
        })

        # 关键帧多模态分析已跳过（节省时间）
        frame_analysis = []

        # Step 6: 爆款元素
        update_progress("viral", "running", "正在提取爆款元素...")
        viral_elements = extract_viral_elements(meta, transcript_text, structure, config)
        # 生成标题（产品名 + 核心爆款元素）
        generated_title = llm_analyzer.llm_generate_title(transcript_text, structure, config, viral_elements)
        if generated_title:
            task["title"] = generated_title
            meta["title"] = generated_title
            update_progress("viral", "done", f"识别了 {len(viral_elements)} 个元素 | {generated_title}", {"viral_elements": viral_elements})
        else:
            update_progress("viral", "done", f"识别了 {len(viral_elements)} 个元素", {"viral_elements": viral_elements})

        # Step 7: 优化建议
        update_progress("optimize", "running", "正在生成优化建议...")
        optimization = generate_optimization(title, structure, viral_elements, config)
        update_progress("optimize", "done", "优化建议生成完成", {"optimization": optimization})

        # 汇总结果
        task["result"] = {
            "title": meta.get("title", title) or title, "platform": meta.get("platform", "unknown"),
            "duration": meta.get("duration", 0), "url": url,
            "video_url": f"/data/{video_rel}", "frame_urls": frame_urls,
            "hook_detail": structure.get("hook_detail", ""), "cta_detail": structure.get("cta_detail", ""),
            "rhetoric": structure.get("rhetoric", []), "psychology_triggers": structure.get("psychology_triggers", []),
            "target_audience": structure.get("target_audience", ""), "pain_points": structure.get("pain_points", []),
            "selling_points": structure.get("selling_points", []),
        }

        optimized_script = ''  # 重写步骤已移除
        # Auto-save report to library
        try:
            report_title = generated_title or title
            # Extract top viral element names
            viral_names = []
            if isinstance(viral_elements, list):
                for v in viral_elements[:3]:
                    if isinstance(v, dict):
                        viral_names.append(v.get("element", v.get("name", "")))
                    elif isinstance(v, str):
                        viral_names.append(v)
            viral_tag = " ".join(viral_names[:2]) if viral_names else ""
            report_name = f"{report_title} | {viral_tag}" if viral_tag else report_title

            report_data = {
                "id": task_id,
                "name": report_name,
                "product_name": report_title,
                "viral_tag": viral_tag,
                "title": title,
                "url": url,
                "platform": meta.get("platform", "unknown"),
                "duration": meta.get("duration", 0),
                "video_url": f"/data/{video_rel}",
                "frame_urls": frame_urls,
                "transcript": transcript_text[:2000],
                "hook_type": structure.get("hook_type", ""),
                "hook_score": safe_int(structure.get("hook_score", 0)),
                "hook_detail": structure.get("hook_detail", ""),
                "cta_type": structure.get("cta_type", ""),
                "cta_detail": structure.get("cta_detail", ""),
                "content_score": safe_int(structure.get("content_score", 0)),
                "emotion_arc": structure.get("emotion_arc", ""),
                "dominant_emotion": structure.get("dominant_emotion", ""),
                "emotion_intensity": safe_int(structure.get("emotion_intensity", 0)),
                "title_formula": structure.get("title_formula", ""),
                "rhetoric": structure.get("rhetoric", []),
                "psychology_triggers": structure.get("psychology_triggers", []),
                "target_audience": structure.get("target_audience", ""),
                "pain_points": structure.get("pain_points", []),
                "selling_points": structure.get("selling_points", []),
                "content_rhythm": structure.get("content_rhythm", ""),
                "trust_building": structure.get("trust_building", ""),
                "platform_adaptation": structure.get("platform_adaptation", ""),
                "viral_elements": viral_elements if isinstance(viral_elements, list) else [],
                "optimization": optimization if isinstance(optimization, list) else [],
                "optimized_script": optimized_script or "",
                "frame_analysis": frame_analysis if isinstance(frame_analysis, list) else [],
                "competitor": competitor if isinstance(competitor, dict) else {},
                "structure": {
                    "part1_text": structure.get("part1_text", ""),
                    "part1_analysis": structure.get("part1_analysis", ""),
                    "part2_text": structure.get("part2_text", ""),
                    "part2_analysis": structure.get("part2_analysis", ""),
                    "part3_text": structure.get("part3_text", ""),
                    "part3_analysis": structure.get("part3_analysis", ""),
                },
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_report(report_data)
        except Exception as e:
            print(f"Failed to save report: {e}", file=sys.stderr)

        task["status"] = "completed"

    except Exception as e:
        update_progress(task.get("current_step", "unknown"), "failed", str(e))
        task["status"] = "failed"

@app.route("/api/generate-video-prompt", methods=["POST"])
def api_generate_video_prompt():
    """Generate hook scene video prompt based on an analysis report."""
    import requests as http_requests
    data = request.get_json() or {}
    report_id = data.get("report_id", "").strip()
    if not report_id:
        return jsonify({"error": "请选择拆解报告"}), 400

    reports = load_reports()
    report = next((r for r in reports if str(r.get("id")) == report_id), None)
    if not report:
        return jsonify({"error": "报告不存在"}), 404

    product = report.get("product_name", report.get("title", "未知"))
    hook_type = data.get("hook_type", "").strip() or report.get("hook_type", "")
    hook_detail = report.get("hook_detail", "")
    structure = report.get("structure", {})
    part1_text = structure.get("part1_text", "") if isinstance(structure, dict) else ""
    part1_analysis = structure.get("part1_analysis", "") if isinstance(structure, dict) else ""
    target_audience = report.get("target_audience", "")
    pain_points = report.get("pain_points", [])
    pain_str = "、".join(pain_points[:3]) if isinstance(pain_points, list) else ""
    transcript = report.get("transcript", "")[:200]

    config = load_config()
    llm_cfg = config.get("llm", {})
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "gpt-5.5")

    system_msg = """你是顶级短视频钩子专家，专攻Seedance AI视频生成提示词。

你的任务：根据对标视频的钩子策略，生成一个3-5秒的开头钩子画面提示词。

钩子策略类型：
- 痛点：直接点出用户高频痛苦，引发共鸣（例："牙套磨嘴怎么办"）
- 悬念：制造认知缺口，让用户想找答案（例："99%的人不知道这个方法"）
- 直接：开门见山展示产品效果（例："看，涂上这个立刻不磨了"）
- 问题：用疑问句引发好奇（例："为什么你的正畸蜡总是掉？"）
- 数字：用具体数据制造冲击（例："3秒止痛，24小时不掉"）

提示词要求：
- 按时间轴逐秒拆解（0-1s / 1-2s / ...）
- 核心原则：每秒只发生一个动作，但这个动作要用丰富的细节来描述
- 每秒描述必须包含以下全部要素：
  * 【一个动作】只描述一个核心动作（如：右手拿起蜡盒），不要叠加多个动作
  * 【人物表情】微表情细节（眉心、嘴角、眼球方向、嘴唇状态）
  * 【产品交互】如果有产品，描述产品外观细节和接触方式
  * 【光影】光源方向、色温、阴影形状
  * 【镜头】优先固定机位（如"85mm固定机位"），非必要不运镜，只在情绪转折点用一次缓慢推近
  * 【情绪】这一秒的情绪状态
- 示例（正确）：
  "0-1s：女生眉头紧锁，眉心向内收拢，嘴唇微微颤动，眼球向右下方缓缓移动看向嘴角被磨的位置，右手食指指腹轻轻按在左侧嘴角向外拉开，露出银色金属托槽末端；晨光从右侧窗户以45度角照入，脸颊侧面形成柔和的明暗分界线，85mm固定机位半身中景，情绪：隐忍的疼痛"
- 示例（错误）：
  "0-1s：皱眉看牙套，浴室环境"（太简陋，缺少细节）
  "0-1s：皱眉同时右手拿蜡盒然后左手拉嘴角"（多个动作塞进1秒）
- 3秒视频节奏：0-1s痛点感受 / 1-2s问题加剧 / 2-3s情绪转折
- 5秒视频节奏：0-1s痛点感受 / 1-2s问题加剧 / 2-3s产品出场 / 3-4s使用产品 / 4-5s效果展示
- 风格：竖屏9:16，4K超高清，真实自然光感
- 重要：画面中不要出现任何字幕、文字、水印

如果用户提供了参考人物图片，在提示词中写"人物外貌完全参照参考人物图片"
如果用户提供了参考产品图片，在提示词中写"产品外观完全参照参考产品图片"

直接输出提示词文本，不要输出JSON，不要输出其他解释。"""

    # Build user message
    user_need = data.get("user_need", "").strip()
    user_msg = f"""对标视频信息：
- 产品：{product}
- 目标人群：{target_audience}
- 钩子类型：{hook_type}
- 钩子策略：{hook_detail}
- 痛点：{pain_str}
- 原视频钩子文案：{part1_text}
- 钩子分析：{part1_analysis}
- 原视频开头：{transcript}

请根据以上信息，生成一个钩子视频提示词，时长{data.get("duration", 5)}秒。{("钩子类型要求：" + hook_type) if hook_type else "根据报告分析自动选择最合适的钩子类型。"}

{("用户额外需求：" + user_need) if user_need else ""}"""

    # Inject reference descriptions
    ref_person_desc = data.get("ref_person_desc", "").strip()
    ref_product_desc = data.get("ref_product_desc", "").strip()
    ref_person_url = data.get("ref_person_url", "").strip()
    ref_product_url = data.get("ref_product_url", "").strip()
    if ref_person_url:
        user_msg += f"\n\n参考人物描述：{ref_person_desc or '见参考图片'}"
    if ref_product_url:
        user_msg += f"\n\n参考产品描述：{ref_product_desc or '见参考图片'}"

    try:
        resp = http_requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ], "max_tokens": 1500, "temperature": 0.7},
            timeout=60
        )
        if resp.status_code >= 400:
            return jsonify({"error": f"LLM error: {resp.status_code}"}), 500
        result = resp.json()
        prompt = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not prompt:
            print(f"LLM returned empty. Response: {result}")
            return jsonify({"error": "LLM returned empty"}), 500
        return jsonify({"success": True, "prompts": [{"scene": "钩子", "prompt": prompt.strip()}], "product": product})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ai-images")
def api_ai_images():
    """List all AI-generated images from data directory."""
    images = []
    data_dir = DATA_DIR
    if data_dir.exists():
        for f in sorted(data_dir.glob("ai_*.png"), reverse=True):
            images.append(f"/data/{f.name}")
        for f in sorted(data_dir.glob("ai_*.jpg"), reverse=True):
            images.append(f"/data/{f.name}")
        # Also check generated subfolder
        gen_dir = data_dir / "generated"
        if gen_dir.exists():
            for f in sorted(gen_dir.glob("*.png"), reverse=True):
                images.append(f"/data/generated/{f.name}")
    return jsonify({"images": images[:50]})

@app.route("/api/reports")
def api_list_reports():
    reports = load_reports()
    return jsonify(reports)

@app.route("/api/reports/<report_id>")
def api_get_report(report_id):
    reports = load_reports()
    for r in reports:
        if r.get("id") == report_id:
            return jsonify(r)
    return jsonify({"error": "not found"}), 404

@app.route("/api/reports/<report_id>", methods=["DELETE"])
def api_delete_report(report_id):
    reports = load_reports()
    reports = [r for r in reports if r.get("id") != report_id]
    REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json()
    url = data.get("url", "").strip()
    steps = data.get("steps", [])
    if not url:
        return jsonify({"error": "请输入视频链接"}), 400
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "id": task_id, "url": url, "status": "running",
        "progress": [], "result": None, "current_step": "",
        "created_at": time.strftime("%H:%M:%S"),
    }
    tasks[task_id]["selected_steps"] = steps
    analysis_executor.submit(run_analysis, task_id, url, steps)
    return jsonify({"task_id": task_id})

@app.route("/api/status/<task_id>")
def api_status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task)

@app.route("/api/stream/<task_id>")
def api_stream(task_id):
    def generate():
        last_len = 0
        while True:
            task = tasks.get(task_id)
            if not task:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                break
            progress = task["progress"]
            if len(progress) > last_len:
                for item in progress[last_len:]:
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                last_len = len(progress)
            if task["status"] in ("completed", "failed"):
                yield f"data: {json.dumps({'status': task['status'], 'result': task.get('result')}, ensure_ascii=False)}\n\n"
                break
            time.sleep(0.5)
    return Response(generate(), mimetype="text/event-stream")

@app.route("/api/refine-prompt", methods=["POST"])
def api_refine_prompt():
    """Refine a scene video prompt based on user instructions."""
    import requests as http_requests
    data = request.get_json(force=True) or {}
    current_prompt = data.get("prompt", "").strip()
    instruction = data.get("instruction", "").strip()
    scene = data.get("scene", "")
    
    if not current_prompt:
        return jsonify({"error": "No prompt to refine"}), 400
    if not instruction:
        return jsonify({"error": "No modification instruction"}), 400
    
    config = load_config()
    llm_cfg = config.get("llm", {})
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "gpt-4o-mini")
    
    system_msg = """你是Seedance视频提示词优化专家。用户会给你一段视频生成提示词和修改要求，请根据要求修改提示词。

规则：
1. 保持提示词的整体结构和格式不变
2. 只修改用户要求改动的部分
3. 保持逐秒描述的精确性
4. 输出修改后的完整提示词，不要加任何解释"""
    
    user_msg = "当前提示词（" + scene + "场景）：\n" + current_prompt + "\n\n修改要求：" + instruction
    try:
        resp = http_requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ], "max_tokens": 2000, "temperature": 0.7},
            timeout=60
        )
        if resp.status_code >= 400:
            return jsonify({"error": f"LLM error: {resp.status_code}"}), 500
        result = resp.json()
        refined = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not refined:
            return jsonify({"error": "LLM returned empty"}), 500
        return jsonify({"success": True, "prompt": refined.strip()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/refine", methods=["POST"])
def api_refine():
    """根据用户优化建议重新调整文案"""
    data = request.get_json()
    current_script = data.get("current_script", "")
    user_feedback = data.get("feedback", "").strip()
    title = data.get("title", "")
    transcript = data.get("transcript", "")

    if not user_feedback:
        return jsonify({"error": "请输入优化建议"}), 400

    config = load_config()
    llm_cfg = config.get("llm", {})
    if not llm_cfg.get("enabled") or not llm_cfg.get("api_key"):
        return jsonify({"error": "LLM 未配置"}), 500

    try:
        resp = __import__("requests").post(
            f"{llm_cfg['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {llm_cfg['api_key']}", "Content-Type": "application/json"},
            json={
                "model": llm_cfg.get("model", "mimo-v2.5"),
                "messages": [
                    {"role": "system", "content": "你是短视频文案优化专家。用户会给出一段文案和修改建议，请根据建议重新优化文案。只输出优化后的完整文案，不要加解释。保持简体中文。"},
                    {"role": "user", "content": f"【视频标题】{title}\n\n【原文案】{transcript[:500]}\n\n【当前优化文案】\n{current_script}\n\n【修改建议】\n{user_feedback}"}
                ],
                "temperature": 0.7
            }, timeout=120
        )
        resp.raise_for_status()
        refined = resp.json()["choices"][0]["message"]["content"].strip()
        return jsonify({"refined_script": refined})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/generate-copy", methods=["POST"])
def api_generate_copy():
    """文案生成接口 - 调用GPT生成短视频文案"""
    import requests as http_requests
    data = request.get_json() or {}
    product = data.get("product", "").strip()
    link = data.get("link", "").strip()
    copy_type = data.get("type", "full")
    ref_report_id = data.get("ref_report_id", "").strip()
    extra = data.get("extra", "").strip()

    if not product and not ref_report_id:
        return jsonify({"error": "请输入产品名称或选择参考报告"}), 400

    # Load reference report if provided
    ref_context = ""
    if ref_report_id:
        reports = load_reports()
        ref = next((r for r in reports if str(r.get("id")) == ref_report_id), None)
        if ref:
            ref_context = f"""
参考拆解报告数据：
- 标题：{ref.get('title', '')}
- 钩子类型：{ref.get('hook_type', '')}
- 钩子详情：{ref.get('hook_detail', '')}
- 爆款元素：{', '.join(ref.get('viral_elements', [])[:3]) if isinstance(ref.get('viral_elements'), list) else ''}
- 转录文案：{(ref.get('transcript', '') or '')[:500]}
- 优化建议：{str(ref.get('optimization', ''))[:300]}
"""
            if not product:
                product = ref.get("product_name", ref.get("title", "未知产品"))

    type_prompts = {
        "full": "请为该产品写一条完整的短视频带货文案（口播稿），包含：1）前3秒强力钩子开头 2）痛点场景 3）产品解决方案 4）卖点罗列 5）信任背书 6）转化结尾CTA。文案口语化、有节奏感、适合15-60秒短视频。",
        "hook": "请为该产品写5个不同的短视频钩子开头（前3秒），要求：每个钩子用不同的策略（恐惧、好奇、反常识、利益点、场景代入），口语化、有冲击力，让人停下来观看。",
        "title": "请为该产品生成10个爆款短视频标题，要求：使用数字、疑问、对比、悬念等技巧，适合抖音/小红书平台，每个标题控制在20字以内。",
        "cta": "请为该产品写5个不同的短视频转化结尾（CTA），要求：引导下单、评论、收藏，口语化、有紧迫感，配合短视频节奏。",
        "rewrite": "请基于以上拆解报告数据，改写优化文案。保留原有的爆款策略框架，但在表达上更加精炼、口语化、有节奏感。优化钩子吸引力、痛点描述、卖点表达和CTA转化力。"
    }

    prompt = type_prompts.get(copy_type, type_prompts["full"])

    system_msg = "你是一位顶级短视频文案策划师，精通抖音、快手、小红书平台的爆款文案创作。你的文案风格口语化、有节奏感、善于用钩子抓住注意力、用痛点激发共鸣、用卖点促成转化。请直接输出文案内容，不要输出多余的解释说明。"

    user_msg = f"产品：{product}"
    if link:
        user_msg += f"\n产品链接：{link}"
    if ref_context:
        user_msg += f"\n\n{ref_context}"
    if extra:
        user_msg += f"\n\n补充要求：{extra}"
    user_msg += f"\n\n{prompt}"

    config = load_config()
    llm_cfg = config.get("llm", {})
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "gpt-4o-mini")

    try:
        resp = http_requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg}
                ],
                "max_tokens": 5000,
                "temperature": 0.8
            },
            timeout=120
        )
        if resp.status_code >= 400:
            return jsonify({"error": f"LLM请求失败: {resp.status_code}", "detail": resp.text[:200]}), 500

        result = resp.json()
        text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not text:
            return jsonify({"error": "LLM返回为空", "raw": result}), 500

        return jsonify({"text": text, "product": product, "type": copy_type})

    except Exception as e:
        return jsonify({"error": f"文案生成异常: {str(e)}"}), 500

@app.route("/api/tasks")
def api_tasks():
    """返回所有任务列表（内存中的 + 飞书已完成的）"""
    task_list = []
    # 内存中的任务
    for tid, t in sorted(tasks.items(), key=lambda x: x[1].get("created_at", ""), reverse=True):
        task_list.append({
            "id": tid, "source": "local",
            "url": t["url"], "status": t["status"],
            "title": (t.get("result") or {}).get("title", ""),
            "created_at": t.get("created_at", ""),
            "content_score": (t.get("result") or {}).get("content_score", 0) if t.get("result") else 0,
        })
    # 飞书记录已暂时关闭
    # try:
    #     config = load_config()
    #     ...
    task_list.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify(task_list)

@app.route("/api/feishu/<record_id>")
def api_feishu_detail(record_id):
    """从飞书读取单条记录详情"""
    try:
        config = load_config()
        resp = __import__("requests").post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": config["feishu"]["app_id"], "app_secret": config["feishu"]["app_secret"]}, timeout=5)
        token = resp.json().get("tenant_access_token")
        at = config["feishu"]["bitable"]["app_token"]
        tid = config["feishu"]["bitable"]["table_id"]
        r = __import__("requests").get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{at}/tables/{tid}/records/{record_id}",
            headers={"Authorization": f"Bearer {token}"}, timeout=10)
        f = r.json().get("data", {}).get("record", {}).get("fields", {})
        return jsonify({
            "id": record_id, "source": "feishu", "status": "completed",
            "title": str(f.get("\u89c6\u9891\u4e3b\u9898", "")),
            "url": str(f.get("\u89c6\u9891\u94fe\u63a5", "")),
            "platform": str(f.get("\u89c6\u9891\u5e73\u53f0", "")),
            "content_score": f.get("\u5185\u5bb9\u8bc4\u5206", 0),
            "hook_type": str(f.get("\u94a9\u5b50\u7c7b\u578b", "")),
            "hook_score": f.get("\u94a9\u5b50\u8bc4\u5206", 0),
            "dominant_emotion": str(f.get("\u4e3b\u5bfc\u60c5\u7eea", "")),
            "emotion_intensity": f.get("\u60c5\u7eea\u5f3a\u5ea6", 0),
            "cta_type": str(f.get("CTA\u7c7b\u578b", "")),
            "emotion_arc": str(f.get("\u60c5\u7eea\u66f2\u7ebf", "")),
            "transcript": str(f.get("\u539f\u6587\u6848", "")),
            "structure": str(f.get("\u6587\u6848\u7ed3\u6784\u5206\u6790", "")),
            "viral_elements": f.get("\u7206\u6b3e\u5143\u7d20", []),
            "optimization": str(f.get("\u4f18\u5316\u65b9\u5411", "")),
            "optimized_script": str(f.get("\u4f18\u5316\u540e\u6587\u6848", "")),
            "progress": [
                {"step": "transcribe", "status": "done", "detail": "", "corrected_text": str(f.get("\u539f\u6587\u6848", "")), "raw_text": ""},
                {"step": "structure", "status": "done", "detail": "",
                    "hook_type": str(f.get("\u94a9\u5b50\u7c7b\u578b", "")),
                    "hook_score": f.get("\u94a9\u5b50\u8bc4\u5206", 0),
                    "emotion_arc": str(f.get("\u60c5\u7eea\u66f2\u7ebf", "")),
                    "dominant_emotion": str(f.get("\u4e3b\u5bfc\u60c5\u7eea", "")),
                    "emotion_intensity": f.get("\u60c5\u7eea\u5f3a\u5ea6", 0),
                    "cta_type": str(f.get("CTA\u7c7b\u578b", "")),
                    "content_score": f.get("\u5185\u5bb9\u8bc4\u5206", 0),
                    "part1_text": "", "part1_analysis": "", "part2_text": "", "part2_analysis": "", "part3_text": "", "part3_analysis": ""},
                {"step": "viral", "status": "done", "detail": "",
                    "viral_elements": f.get("\u7206\u6b3e\u5143\u7d20", [])},
                {"step": "optimize", "status": "done", "detail": "",
                    "optimization": str(f.get("\u4f18\u5316\u65b9\u5411", "")).split("\n")},
                {"step": "optimize_script", "status": "done", "detail": "",
                    "optimized_script": str(f.get("\u4f18\u5316\u540e\u6587\u6848", ""))},
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/task/<task_id>")
def api_task_detail(task_id):
    """返回单个任务的完整数据"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task)

@app.route("/library")
def library_page():
    """拆解库页面"""
    return render_template("library.html")

@app.route("/api/task/<task_id>/select-frames", methods=["POST"])
def api_select_frames(task_id):
    """User submits frame selections. Resumes the waiting pipeline."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    if task.get("status") != "waiting_selection":
        return jsonify({"error": "task is not waiting for selection"}), 400
    data = request.get_json()
    selections = data.get("selections", {})
    if not selections:
        return jsonify({"error": "no selections provided"}), 400
    task["frame_selections"] = selections
    task["status"] = "running"
    return jsonify({"ok": True, "message": "selections received"})

@app.route("/api/task/<task_id>/generate-image", methods=["POST"])
def api_generate_image(task_id):
    """Generate reference image from selected keyframe + user prompt using gpt-image-2-pro."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    data = request.get_json()
    frame_url = data.get("frame_url", "")
    prompt = data.get("prompt", "")
    if not frame_url or not prompt:
        return jsonify({"error": "frame_url and prompt required"}), 400

    # Convert URL to local path
    frame_path = str(DATA_DIR / frame_url.replace("/data/", ""))
    if not Path(frame_path).exists():
        return jsonify({"error": "frame not found"}), 404

    config = load_config()
    video_id = task.get("video_id", "unknown")
    output_dir = str(DATA_DIR / f"ref_{video_id}")

    result = generate_reference_image(frame_path, prompt, config, output_dir)
    if result:
        task.setdefault("ref_images", []).append({
            "source_frame": frame_url,
            "prompt": prompt,
            "image_url": result["image_url"],
            "image_path": result.get("image_path", ""),
        })
        return jsonify({"ok": True, **result})
    return jsonify({"error": "image generation failed"}), 500

@app.route("/api/task/<task_id>/generate-video", methods=["POST"])
def api_generate_video(task_id):
    """Generate video using Seedance with user-specified duration and prompt."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    data = request.get_json()
    prompt = data.get("prompt", "")
    duration = data.get("duration", 5)
    image_url = data.get("image_url", "")  # Optional: use reference image

    if not prompt:
        # Fallback to optimized script
        prompt = task.get("result", {}).get("optimized_script", "")

    config = load_config()
    task["video_gen_status"] = "generating"
    task["video_gen_duration"] = duration

    def _gen():
        try:
            llm_cfg = config.get("llm", {})
            api_key = llm_cfg.get("api_key", "")
            base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
            model = "doubao-seedance-2-0-fast-260128"

            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            url = f"{base_url.rstrip('/')}/video/generations"

            video_prompt = f"短视频画面：{prompt[:300]}。风格：高清、明亮、产品特写。时长：{duration}秒。"
            payload = {"model": model, "prompt": video_prompt}

            # If reference image provided, add it
            if image_url:
                import base64
                img_path = str(DATA_DIR / image_url.replace("/data/", ""))
                if Path(img_path).exists():
                    with open(img_path, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode()
                    payload["image"] = img_b64

            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            vid = result.get("id", "")

            if not vid:
                task["video_gen_status"] = "failed"
                return

            # Poll
            poll_url = f"{url}/{vid}"
            for _ in range(24):  # 6 min max
                time.sleep(15)
                r = requests.get(poll_url, headers=headers, timeout=15)
                r.raise_for_status()
                d = r.json()
                st = d.get("status", "")
                if st in ("completed", "succeeded", "done"):
                    vdata = d.get("data", [])
                    vurl = vdata[0].get("url", "") if vdata else ""
                    task["video_gen_status"] = "done"
                    task["video_gen_url"] = vurl
                    task["video_gen_prompt"] = video_prompt
                    return
                if st in ("failed", "error"):
                    task["video_gen_status"] = "failed"
                    return
            task["video_gen_status"] = "timeout"
        except Exception as e:
            print(f"[VIDEO_GEN] Error: {e}", file=sys.stderr)
            task["video_gen_status"] = "failed"

    import threading
    threading.Thread(target=_gen, daemon=True).start()
    return jsonify({"ok": True, "message": "video generation started", "duration": duration})

@app.route("/api/task/<task_id>/video-status")
def api_video_status(task_id):
    """Check video generation status."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify({
        "status": task.get("video_gen_status", "idle"),
        "video_url": task.get("video_gen_url", ""),
        "duration": task.get("video_gen_duration", 5),
        "prompt": task.get("video_gen_prompt", ""),
    })

@app.route("/api/rerun", methods=["POST"])
def api_rerun():
    data = request.get_json()
    task_id = data.get("task_id", "")
    step = data.get("step", "")
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    task["status"] = "running"
    url = task.get("url", "")
    analysis_executor.submit(run_analysis, task_id, url, [step])
    return jsonify({"ok": True})

# === AI Generation Endpoints (GPT-5.5) ===
def ai_url(path):
    """拼接接口地址"""
    return AI_BASE_URL.rstrip("/") + "/" + path.lstrip("/")

def ai_headers(json_body=True):
    """生成请求头"""
    headers = {
        "Authorization": "Bearer " + AI_API_KEY
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers

def get_error_detail(resp):
    """提取错误信息"""
    try:
        return resp.json()
    except Exception:
        return resp.text[:1000]

def extract_image_url(data):
    """
    兼容不同图片接口返回格式：
    1. { data: [ { url: "..." } ] }
    2. { data: [ { b64_json: "..." } ] }
    3. { url: "..." }
    4. { b64_json: "..." }
    """
    item = None

    if isinstance(data, dict):
        if isinstance(data.get("data"), list) and len(data.get("data")) > 0:
            item = data.get("data")[0]
        elif isinstance(data.get("data"), dict):
            item = data.get("data")
        else:
            item = data

    if isinstance(item, str):
        if item.startswith("http") or item.startswith("data:image"):
            return item

    if not isinstance(item, dict):
        return None

    image_url = item.get("url") or item.get("image_url")
    if image_url:
        return image_url

    b64 = (
        item.get("b64_json")
        or item.get("base64")
        or item.get("image")
    )

    if b64:
        if isinstance(b64, str) and b64.startswith("data:image"):
            return b64
        return "data:image/png;base64," + b64

    return None

def find_value_by_keys(obj, keys):
    """递归查找指定 key 的值"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                return v
        for k, v in obj.items():
            found = find_value_by_keys(v, keys)
            if found is not None:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = find_value_by_keys(item, keys)
            if found is not None:
                return found

    return None

def find_video_url(obj):
    """递归查找视频地址"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()

            if isinstance(v, str):
                value = v.strip()
                if (
                    ("video" in key or "url" in key or "output" in key)
                    and (
                        value.startswith("http://")
                        or value.startswith("https://")
                        or value.startswith("data:video")
                    )
                ):
                    return value

            found = find_video_url(v)
            if found:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = find_video_url(item)
            if found:
                return found

    return None

def extract_task_id(data):
    """兼容不同视频任务创建接口的任务 ID 字段"""
    task_id = find_value_by_keys(data, [
        "id",
        "task_id",
        "taskId",
        "job_id",
        "jobId"
    ])

    if task_id is None:
        return None

    return str(task_id)

def normalize_status(data):
    """统一任务状态字段"""
    status = find_value_by_keys(data, [
        "status",
        "task_status",
        "taskStatus",
        "state"
    ])

    if status is None:
        return "processing"

    return str(status).lower()

def is_success_status(status):
    """判断是否成功"""
    return status in [
        "success",
        "succeeded",
        "completed",
        "complete",
        "done",
        "finished"
    ]

def is_failed_status(status):
    """判断是否失败"""
    return status in [
        "failed",
        "fail",
        "error",
        "canceled",
        "cancelled",
        "timeout"
    ]

def create_seedance_video_task(prompt, duration, ref_images=None):
    """
    创建 Seedance 视频任务。ref_images: list of image URLs or base64
    支持多张参考图。
    """
    try:
        duration = int(str(duration).replace("s", "").strip())
    except (ValueError, TypeError):
        duration = 5

    # Process all reference images
    image_list = []
    if ref_images:
        for img in ref_images:
            if img.startswith("data:"):
                image_list.append(img)
            elif img.startswith("/data/"):
                import pathlib, base64
                local = DATA_DIR / img.replace("/data/", "")
                if local.exists():
                    with open(local, "rb") as f:
                        b = base64.b64encode(f.read()).decode()
                    ext = local.suffix.lower().lstrip(".")
                    mime = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg","jpeg") else "image/webp"
                    image_list.append(f"data:{mime};base64,{b}")

    # Use /video/generations with images array (supports multiple)
    payload = {
        "model": VIDEO_MODEL,
        "prompt": prompt,
        "duration": duration
    }
    if image_list:
        payload["images"] = image_list

    try:
        resp = requests.post(
            ai_url("/video/generations"),
            headers=ai_headers(True),
            json=payload,
            timeout=30
        )
        print(f"Seedance request: {len(image_list)} images, prompt len={len(prompt)}")
        print(f"Seedance response: {resp.status_code} - {resp.text[:200]}")
        if resp.status_code >= 400:
            return {"ok": False, "error": f"API error {resp.status_code}: {resp.text[:100]}"}

        data = resp.json()
        provider_task_id = extract_task_id(data)
        video_url = find_video_url(data)

        if video_url:
            return {"ok": True, "provider_task_id": provider_task_id or str(uuid.uuid4()), "poll_path": "/video/generations/{id}", "video_url": video_url, "raw": data}
        if provider_task_id:
            return {"ok": True, "provider_task_id": provider_task_id, "poll_path": "/video/generations/{id}", "video_url": None, "raw": data}

        return {"ok": False, "error": f"No task ID: {data}"}

    except Exception as e:
        print(f"Seedance exception: {e}")
        return {"ok": False, "error": str(e)}

def poll_seedance_video_task(provider_task_id, poll_path):
    """查询视频任务状态"""
    path = poll_path.replace("{id}", provider_task_id)

    resp = requests.get(
        ai_url(path),
        headers=ai_headers(True),
        timeout=30
    )

    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": get_error_detail(resp)
        }

    data = resp.json()
    status = normalize_status(data)
    video_url = find_video_url(data)

    return {
        "ok": True,
        "status": status,
        "video_url": video_url,
        "raw": data
    }

@app.route("/api/ai-generate-image", methods=["POST"])
def api_standalone_generate_image():
    """
    AI 图片生成接口。

    支持：
    1. 纯提示词生图
    2. 上传参考图 + 提示词生图

    前端用 multipart/form-data 提交：
    - prompt
    - size
    - reference_image，可选
    """
    try:
        prompt = request.form.get("prompt", "").strip()
        size = request.form.get("size", "1024x1024").strip()
        ref_file = request.files.get("reference_image")
        ref_url = request.form.get("reference_url", "").strip()

        # If reference_url provided, download it as a file
        if ref_url and not ref_file:
            try:
                ref_resp = requests.get(ref_url, timeout=30)
                if ref_resp.status_code == 200:
                    class MockFile:
                        pass
                    mf = MockFile()
                    mf.filename = "reference.png"
                    mf.stream = _io.BytesIO(ref_resp.content)
                    mf.mimetype = ref_resp.headers.get("content-type", "image/png")
                    ref_file = mf
            except Exception:
                pass

        allowed_sizes = ["1024x1024", "1024x1792", "1792x1024"]

        if not prompt:
            return jsonify({
                "success": False,
                "message": "请输入图片生成提示词"
            }), 400

        if size not in allowed_sizes:
            return jsonify({
                "success": False,
                "message": "图片尺寸不合法"
            }), 400

        # 有参考图：走图片编辑接口
        if ref_file and ref_file.filename:
            data = {
                "model": IMAGE_MODEL,
                "prompt": prompt,
                "size": size,
                "n": "1",
                "response_format": "b64_json"
            }

            files = {
                "image": (
                    ref_file.filename,
                    ref_file.stream,
                    ref_file.mimetype or "image/png"
                )
            }

            resp = requests.post(
                ai_url("/images/edits"),
                headers=ai_headers(False),
                data=data,
                files=files,
                timeout=240
            )

        # 无参考图：走普通图片生成接口
        else:
            payload = {
                "model": IMAGE_MODEL,
                "prompt": prompt,
                "size": size,
                "n": 1,
                "response_format": "b64_json"
            }

            resp = requests.post(
                ai_url("/images/generations"),
                headers=ai_headers(True),
                json=payload,
                timeout=240
            )

        if resp.status_code >= 400:
            return jsonify({
                "success": False,
                "message": "图片生成失败",
                "detail": get_error_detail(resp)
            }), resp.status_code

        result = resp.json()
        image_url = extract_image_url(result)

        if not image_url:
            return jsonify({
                "success": False,
                "message": "图片生成成功，但未解析到图片地址",
                "raw": result
            }), 500

        # Save image locally
        local_url = save_image_locally(image_url)
        
        return jsonify({
            "success": True,
            "image_url": local_url,
            "original_url": image_url,
            "raw": result
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": "图片生成异常",
            "detail": str(e)
        }), 500

@app.route("/api/ai-generate-video", methods=["POST", "GET"])
def api_standalone_generate_video():
    """
    AI 视频生成接口。

    POST:
    - 创建视频任务
    - 返回本地 task_id

    GET:
    - 根据 task_id 轮询任务状态
    - 成功后返回 video_url
    """
    try:
        # 创建任务
        if request.method == "POST":
            body = request.get_json(silent=True) or {}

            prompt = str(body.get("prompt", "")).strip()
            duration = int(str(body.get("duration", 5)).replace("s", "").strip())

            allowed_durations = None  # allow any duration

            if not prompt:
                return jsonify({
                    "success": False,
                    "message": "请输入视频生成提示词"
                }), 400

            if duration < 1 or duration > 60:
                return jsonify({
                    "success": False,
                    "message": "视频时长请设置在 1-60 秒之间"
                }), 400

            ref_images = body.get("ref_images", [])
            result = create_seedance_video_task(prompt, duration, ref_images=ref_images)

            if not result.get("ok"):
                import logging
                logging.error(f"Seedance error: {result.get('error')}")
                print(f"Seedance error: {result.get('error')}")
                return jsonify({
                    "success": False,
                    "message": f"视频任务创建失败: {result.get('error', '')}",
                    "detail": result.get("error")
                }), 500

            local_task_id = str(uuid.uuid4())

            AI_VIDEO_TASKS[local_task_id] = {
                "provider_task_id": result.get("provider_task_id"),
                "poll_path": result.get("poll_path"),
                "created_at": int(time.time()),
                "status": "submitted",
                "video_url": result.get("video_url")
            }

            # 如果接口同步返回了视频地址，直接标记成功
            if result.get("video_url"):
                AI_VIDEO_TASKS[local_task_id]["status"] = "succeeded"
                # Save video locally
                local_v = save_video_locally(result["video_url"])
                AI_VIDEO_TASKS[local_task_id]["video_url"] = local_v
                result["video_url"] = local_v

            return jsonify({
                "success": True,
                "task_id": local_task_id,
                "provider_task_id": result.get("provider_task_id"),
                "status": AI_VIDEO_TASKS[local_task_id]["status"],
                "video_url": result.get("video_url")
            })

        # 查询任务
        task_id = request.args.get("task_id", "").strip()

        if not task_id:
            return jsonify({
                "success": False,
                "message": "缺少 task_id"
            }), 400

        task = AI_VIDEO_TASKS.get(task_id)

        if not task:
            return jsonify({
                "success": False,
                "message": "任务不存在或服务已重启"
            }), 404

        # 已完成则直接返回
        if task.get("video_url"):
            return jsonify({
                "success": True,
                "task_id": task_id,
                "status": "succeeded",
                "video_url": task.get("video_url")
            })

        provider_task_id = task.get("provider_task_id")
        poll_path = task.get("poll_path")

        result = poll_seedance_video_task(provider_task_id, poll_path)

        if not result.get("ok"):
            return jsonify({
                "success": False,
                "message": "视频任务查询失败",
                "detail": result.get("error")
            }), 500

        status = result.get("status") or "processing"
        video_url = result.get("video_url")

        if video_url:
            status = "succeeded"
            # Save video locally
            local_video = save_video_locally(video_url)
            task["video_url"] = local_video
            task["original_url"] = video_url
            video_url = local_video

        task["status"] = status

        if is_failed_status(status):
            return jsonify({
                "success": False,
                "task_id": task_id,
                "status": status,
                "message": "视频生成失败",
                "raw": result.get("raw")
            })

        return jsonify({
            "success": True,
            "task_id": task_id,
            "status": status,
            "video_url": video_url,
            "raw": result.get("raw")
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": "视频生成异常",
            "detail": str(e)
        }), 500

@app.route("/api/competitor/search", methods=["POST"])
def api_competitor_search():
    """Search Douyin for competitor videos by keyword."""
    data = request.get_json(force=True) or {}
    keyword = data.get("keyword", "").strip()
    limit = int(data.get("limit", 20))
    sort = data.get("sort", "relevance")
    if not keyword:
        return jsonify({"success": False, "message": "keyword required"}), 400
    try:
        results = search_douyin_videos(keyword, limit=limit, sort=sort)
        return jsonify({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/upload-ref-image", methods=["POST"])
def api_upload_ref_image():
    """Upload a reference image (person or product) for video generation."""
    import base64
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400
    
    f = request.files["image"]
    image_type = request.form.get("type", "person")  # "person" or "product"
    
    # Save to data directory
    ref_dir = DATA_DIR / "ref_images"
    ref_dir.mkdir(parents=True, exist_ok=True)
    
    ext = f.filename.rsplit(".", 1)[-1] if "." in f.filename else "png"
    filename = f"ref_{image_type}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = ref_dir / filename
    f.save(str(filepath))
    
    return jsonify({
        "success": True,
        "url": f"/data/ref_images/{filename}",
        "filename": filename,
        "type": image_type
    })

@app.route("/api/describe-image", methods=["POST"])
def api_describe_image():
    """Use LLM to describe a reference image for prompt generation."""
    import requests as http_requests, base64
    data = request.get_json() or {}
    image_url = data.get("image_url", "")
    desc_type = data.get("type", "person")  # "person" or "product"
    
    if not image_url:
        return jsonify({"error": "No image URL"}), 400
    
    config = load_config()
    llm_cfg = config.get("llm", {})
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "gpt-5.5")
    
    # Read image and encode to base64
    if image_url.startswith("/data/"):
        local_path = str(DATA_DIR / image_url.replace("/data/", ""))
    else:
        local_path = image_url
    
    try:
        with open(local_path, "rb") as img_f:
            img_b64 = base64.b64encode(img_f.read()).decode()
        # Extract file extension for MIME type
        ext = local_path.rsplit(".", 1)[-1].lower() if "." in local_path else "png"
        if ext == "jpg":
            ext = "jpeg"
    except Exception as e:
        return jsonify({"error": f"Cannot read image: {e}"}), 400
    
    if desc_type == "person":
        sys_prompt = "你是专业的视频选角导演。请描述图片中人物的外貌特征，用于AI视频生成。包括：性别、大致年龄、肤色、发型发色、脸型、五官特征、妆容、穿着风格、体型、气质。如果图片中没有明确的人物，请描述你看到的内容。150-250字，直接输出。"
    else:
        sys_prompt = "你是专业的产品摄影师。请描述图片中产品的外观特征，用于AI视频生成。包括：产品类型、颜色、形状、材质、包装设计、独特细节。如果图片中没有明确的产品，请描述你看到的内容。150-250字，直接输出。"
    
    try:
        resp = http_requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{img_b64}"}},
                    {"type": "text", "text": "请精确描述这张图片中" + ("人物的外貌特征" if desc_type == "person" else "产品的外观特征")}
                ]}
            ], "max_tokens": 500},
            timeout=60
        )
        if resp.status_code >= 400:
            return jsonify({"error": f"LLM error: {resp.status_code}"}), 500
        result = resp.json()
        desc = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return jsonify({"description": desc.strip(), "type": desc_type})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/generated-assets")
def api_generated_assets():
    """List AI-generated images and videos only (not dissect downloads)."""
    assets = []
    data_dir = Path(str(DATA_DIR))
    
    # AI generated videos (seedance)
    for f in sorted(data_dir.glob("seedance_*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        assets.append({
            "type": "video",
            "name": f.name,
            "url": f"/data/{f.name}",
            "size": f.stat().st_size,
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
        })
    
    # AI generated images
    for f in sorted(data_dir.glob("ai_img_*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
            assets.append({
                "type": "image",
                "name": f.name,
                "url": f"/data/{f.name}",
                "size": f.stat().st_size,
                "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
            })
    
    return jsonify(assets)

@app.route("/api/dissect-videos")
def api_dissect_videos():
    """List downloaded/dissected videos for reference selection."""
    assets = []
    data_dir = Path(str(DATA_DIR))
    
    for f in sorted(data_dir.glob("video_*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        assets.append({
            "name": f.name,
            "url": f"/data/{f.name}",
            "size": f.stat().st_size,
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
        })
    
    return jsonify(assets)

@app.route("/api/delete-asset", methods=["POST"])
def api_delete_asset():
    """Delete a generated image or video."""
    data = request.get_json() or {}
    filename = data.get("filename", "").strip()
    asset_type = data.get("type", "video")
    
    if not filename:
        return jsonify({"error": "No filename"}), 400
    
    if asset_type == "image":
        filepath = DATA_DIR / "ref_images" / filename
    else:
        filepath = DATA_DIR / filename
    
    # Safety: only allow deleting from data directory
    try:
        filepath = filepath.resolve()
        data_resolved = DATA_DIR.resolve()
        if not str(filepath).startswith(str(data_resolved)):
            return jsonify({"error": "Invalid path"}), 400
    except Exception:
        return jsonify({"error": "Invalid path"}), 400
    
    if not filepath.exists():
        return jsonify({"error": "File not found"}), 404
    
    try:
        filepath.unlink()
        # Also delete associated meta file
        meta = filepath.with_suffix('.json').with_name(filepath.stem.replace('video_', 'meta_') + '.json')
        if meta.exists():
            meta.unlink()
        return jsonify({"success": True, "deleted": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/burn-subtitle", methods=["POST"])
def api_burn_subtitle():
    """Burn subtitles into a video using PIL overlay + ffmpeg."""
    import subprocess
    from PIL import Image, ImageDraw, ImageFont
    data = request.get_json(force=True) or {}
    video_url = data.get("video_url", "").strip()
    text = data.get("text", "").strip()
    position = data.get("position", "bottom")  # top / center / bottom
    font_size = int(data.get("font_size", 28))
    
    if not video_url:
        return jsonify({"error": "No video URL"}), 400
    if not text:
        return jsonify({"error": "No subtitle text"}), 400
    
    # Resolve local file path
    if video_url.startswith("/data/"):
        src_path = DATA_DIR / video_url.replace("/data/", "")
    else:
        return jsonify({"error": "Invalid video URL"}), 400
    
    if not src_path.exists():
        return jsonify({"error": "Video not found"}), 404
    
    # Get video dimensions using ffprobe
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src_path)],
            capture_output=True, text=True, timeout=10
        )
        dims = probe.stdout.strip().split(",")
        vw, vh = int(dims[0]), int(dims[1])
    except Exception:
        vw, vh = 720, 1280
    
    # Create subtitle overlay image with PIL
    img = Image.new("RGBA", (vw, vh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Try to load a CJK font
    font = None
    font_paths = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
    ]
    for fp in font_paths:
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    
    # Measure text
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    # Position
    pad = 12
    if position == "top":
        ty = int(vh * 0.05)
    elif position == "center":
        ty = (vh - th) // 2
    else:  # bottom
        ty = int(vh * 0.85)
    tx = (vw - tw) // 2
    
    # Draw background box
    box_x1 = tx - pad * 2
    box_y1 = ty - pad
    box_x2 = tx + tw + pad * 2
    box_y2 = ty + th + pad
    draw.rounded_rectangle([box_x1, box_y1, box_x2, box_y2], radius=8, fill=(0, 0, 0, 160))
    
    # Draw text with outline
    for ox in [-2, -1, 0, 1, 2]:
        for oy in [-2, -1, 0, 1, 2]:
            draw.text((tx + ox, ty + oy), text, font=font, fill=(0, 0, 0, 255))
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))
    
    # Save overlay PNG
    overlay_name = f"overlay_{int(time.time()*1000)}.png"
    overlay_path = DATA_DIR / overlay_name
    img.save(str(overlay_path), "PNG")
    
    # Output file
    ts = int(time.time() * 1000)
    out_name = f"sub_{ts}.mp4"
    out_path = DATA_DIR / out_name
    
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(src_path),
            "-i", str(overlay_path),
            "-filter_complex", "overlay=0:0",
            "-c:a", "copy",
            "-c:v", "mpeg4", "-q:v", "4",
            str(out_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        
        # Clean up overlay
        try:
            overlay_path.unlink()
        except Exception:
            pass
        
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='ignore')[-300:]
            return jsonify({"error": f"ffmpeg error: {err}"}), 500
        
        return jsonify({
            "success": True,
            "video_url": f"/data/{out_name}",
            "filename": out_name
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "ffmpeg timeout"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/extract-clip", methods=["POST"])
def api_extract_clip():
    """Extract a time segment from a reference video using ffmpeg."""
    import subprocess
    data = request.get_json(force=True) or {}
    video_url = data.get("video_url", "").strip()
    start = data.get("start", 0)
    end = data.get("end", 0)
    
    if not video_url:
        return jsonify({"error": "No video URL"}), 400
    
    try:
        start = float(start)
        end = float(end)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid time range"}), 400
    
    if end <= start:
        return jsonify({"error": "End time must be after start time"}), 400
    
    # Resolve local file path
    if video_url.startswith("/data/"):
        src_path = DATA_DIR / video_url.replace("/data/", "")
    else:
        return jsonify({"error": "Invalid video URL"}), 400
    
    if not src_path.exists():
        return jsonify({"error": "Video file not found"}), 404
    
    # Output file
    ts = int(time.time() * 1000)
    out_name = f"clip_{ts}_{int(start)}_{int(end)}.mp4"
    out_path = DATA_DIR / out_name
    
    duration = end - start
    
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(src_path),
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(out_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            return jsonify({"error": f"ffmpeg error: {result.stderr.decode('utf-8', errors='ignore')[-200:]}"}), 500
        
        return jsonify({
            "success": True,
            "clip_url": f"/data/{out_name}",
            "filename": out_name,
            "duration": duration
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "ffmpeg timeout"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/competitor")
def competitor():
    competitor_path = Path(__file__).parent.parent / "preview" / "competitor.html"
    return send_file(str(competitor_path))

if __name__ == "__main__":
    print("=" * 50)
    print("  短视频拆解 Web 应用")
    print("  http://localhost:5000")
    print("=" * 50)
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
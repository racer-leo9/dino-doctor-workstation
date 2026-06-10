# -*- coding: utf-8 -*-
"""
Main orchestrator: analyze a video and push results to Feishu Bitable.
Pipeline: download -> transcribe -> extract frames -> LLM/rule analysis -> push to Feishu.
Analysis layer: LLM 优先，规则引擎兜底。所有输出简体中文。
"""
import sys, os, json, time, re
from pathlib import Path
from datetime import datetime

# Add scripts dir to path
scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

import yaml
from download_video import download_video
from transcribe import transcribe_video
from extract_frames import extract_frames
from feishu_upload import FeishuHelper
from num_helper import safe_int
import llm_analyzer


def load_config():
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"
    if not config_path.exists():
        for p in [Path.cwd() / "config" / "settings.yaml", Path(__file__).parent.parent / "config" / "settings.yaml"]:
            if p.exists():
                config_path = p
                break
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# =========================================================================
# 规则引擎（LLM 不可用时的 fallback）
# =========================================================================

def _rule_analyze_structure(transcript_text, video_title=""):
    """规则引擎：文案三段式拆解。"""
    text = transcript_text.strip()
    if not text:
        return {"error": "Empty transcript"}

    sentences = []
    for sep in ["。", "！", "？", "，", "\n"]:
        if sep in text:
            parts = text.split(sep)
            sentences = [p.strip() for p in parts if p.strip()]
            break
    if not sentences:
        sentences = [text]

    total = len(sentences)
    if total < 3:
        return {
            "part1_label": "Part 1: Hook/Opening", "part1_text": text,
            "part1_analysis": f"Opening section ({total} sentences).",
            "part2_label": "Part 2: Core Content", "part2_text": "",
            "part2_analysis": "", "part3_label": "Part 3: CTA/Resolution",
            "part3_text": "", "part3_analysis": "", "total_sentences": total,
            "hook_type": "未知", "hook_score": 5, "emotion_arc": "未知",
            "dominant_emotion": "未知", "emotion_intensity": 5,
            "title_formula": "未知", "cta_type": "未知", "content_score": 5,
        }

    hook_end = max(1, total // 5)
    cta_start = max(hook_end + 1, total - total // 6)

    hook = "。".join(sentences[:hook_end]) + "。"
    content = "。".join(sentences[hook_end:cta_start]) + "。"
    cta = "。".join(sentences[cta_start:]) + "。"

    return {
        "part1_label": "Part 1: Hook/Opening",
        "part1_text": hook,
        "part1_analysis": f"Opening section ({hook_end} sentences). Designed to grab attention in first 3 seconds.",
        "part2_label": "Part 2: Core Content",
        "part2_text": content,
        "part2_analysis": f"Main content section ({cta_start - hook_end} sentences). Delivers value, builds narrative.",
        "part3_label": "Part 3: CTA/Resolution",
        "part3_text": cta,
        "part3_analysis": f"Closing section ({total - cta_start} sentences). Call to action or resolution.",
        "total_sentences": total,
        "hook_type": "未知", "hook_score": 5, "emotion_arc": "好奇→信任",
        "dominant_emotion": "好奇", "emotion_intensity": 5,
        "title_formula": "未知", "cta_type": "未知", "content_score": 5,
    }


def _rule_analyze_key_frames(frame_paths):
    """规则引擎：关键帧角色标注。"""
    roles = [
        ("钩子画面", "Opening hook - captures attention in the first impression"),
        ("痛点", "Problem setup - establishes the pain point or curiosity"),
        ("卖点", "Key turning point - the most dramatic or informative moment"),
        ("使用演示", "Core value delivery - delivers the main insight or solution"),
        ("效果", "Emotional peak - the highest emotional impact moment"),
        ("CTA", "Closing/CTA - reinforces the message and drives action"),
    ]
    analysis = []
    for i, path in enumerate(frame_paths):
        stage, role = roles[i] if i < len(roles) else (f"Frame {i+1}", f"Frame {i+1}")
        analysis.append({"frame": i + 1, "path": str(path), "role": role, "stage": stage})
    return analysis


def _rule_extract_viral_elements(video_meta, transcript_text, structure):
    """规则引擎：爆款元素提取。"""
    elements = []
    title = video_meta.get("title", "")
    duration = video_meta.get("duration", 0)

    if re.search(r"\d+[个种条招]", title):
        elements.append("数字量化标题: 用具体数字制造确定感")
    if re.search(r"千万|必|一定|绝对", title):
        elements.append("紧迫感词汇: 制造稀缺和急迫感")
    if re.search(r"你|我", title):
        elements.append("人称代词: 建立与观众的直接连接")
    if re.search(r"\?|？", title):
        elements.append("疑问句式: 激发好奇心")
    if len(title) <= 15:
        elements.append("短标题冲击: 简洁有力")
    if 15 <= duration <= 60:
        elements.append("黄金时长: 15-60秒完播率最优区间")

    text = transcript_text
    if any(w in text for w in ["你", "你们", "大家"]):
        elements.append("第二人称叙事: 增强代入感和互动感")
    if any(w in text for w in ["因为", "所以", "首先", "然后"]):
        elements.append("逻辑连接词: 内容结构清晰有条理")
    if any(w in text for w in ["震惊", "没想到", "居然", "竟然"]):
        elements.append("情绪触发词: 制造惊喜和意外感")
    if any(w in text for w in ["免费", "不花钱", "0元"]):
        elements.append("零成本暗示: 降低行动门槛")
    if re.search(r"\d+", text):
        elements.append("数据支撑: 用具体数字增强可信度")

    return elements if elements else ["基础内容: 视频具备基本传播要素"]


def _rule_generate_optimization(title, structure, viral_elements):
    """规则引擎：优化建议。"""
    suggestions = []
    hook_type = structure.get("hook_type", "未知")
    hook_score = structure.get("hook_score", 5)
    content_score = structure.get("content_score", 5)

    if hook_score < 7:
        suggestions.append("钩子优化: 开头3秒加入更强的好奇钩子（疑问句/数字/反常识）")
    if content_score < 7:
        suggestions.append("内容密度: 增加具体案例、数据或对比，提升说服力")
    suggestions.append("情绪节奏: 在文案中段设置转折点，制造情绪波动")
    suggestions.append("信任建设: 加入使用前后对比、用户证言或权威背书")
    suggestions.append("CTA强化: 结尾明确行动指令，降低用户行动成本")
    suggestions.append("标题迭代: 测试2-3个不同钩子类型的标题变体")
    return suggestions


def _rule_generate_optimized_script(title, transcript_text, structure, optimization):
    """规则引擎：优化后文案模板。"""
    hook = structure.get("part1_text", "")
    content = structure.get("part2_text", "")
    cta = structure.get("part3_text", "")

    optimized = f"""【优化后文案 - {title}】

【钩子 - 前3秒】
{hook[:50] if hook else '(建议用疑问句/数字/痛点切入)'}

【核心内容】
{content if content else '(保持原有核心价值点，增加逻辑连接词)'}

【行动号召 - 结尾】
{cta if cta else '(增加: 觉得有用的话点个赞收藏一下，关注我看更多干货!)'}

---
优化建议:
"""
    for i, s in enumerate(optimization, 1):
        optimized += f"{i}. {s}\n"
    return optimized


# =========================================================================
# 统一分析入口：LLM 优先 → 规则兜底
# =========================================================================

def analyze_structure(transcript_text, video_title="", config=None):
    """文案结构拆解。"""
    if config and config.get("llm", {}).get("enabled") and config["llm"].get("api_key"):
        print("  [LLM] 调用大模型分析文案结构...", file=sys.stderr)
        result = llm_analyzer.llm_analyze_structure(transcript_text, video_title, config)
        if result:
            print("  [LLM] 结构拆解完成 ✓", file=sys.stderr)
            return result
        print("  [LLM] 降级到规则引擎", file=sys.stderr)
    return _rule_analyze_structure(transcript_text, video_title)


def analyze_key_frames(transcript_text, video_title, frame_paths, config=None):
    """关键帧分析（多模态优先）。"""
    if config and config.get("llm", {}).get("enabled") and config["llm"].get("api_key"):
        # Try multimodal first (send images to mimo-v2.5)
        print("  [LLM] calling multimodal model for key frame analysis...", file=sys.stderr)
        result = llm_analyzer.llm_analyze_key_frames_multimodal(transcript_text, video_title, frame_paths, config)
        if result:
            for i, item in enumerate(result):
                if i < len(frame_paths):
                    item["path"] = str(frame_paths[i])
            print("  [LLM] multimodal key frame analysis done", file=sys.stderr)
            return result
        # Fallback to text-only
        print("  [LLM] multimodal failed, falling back to text-only...", file=sys.stderr)
        result = llm_analyzer.llm_analyze_key_frames(transcript_text, video_title, len(frame_paths), config)
        if result:
            for i, item in enumerate(result):
                if i < len(frame_paths):
                    item["path"] = str(frame_paths[i])
            print("  [LLM] text-only key frame analysis done", file=sys.stderr)
            return result
        print("  [LLM] falling back to rule engine", file=sys.stderr)
    return _rule_analyze_key_frames(frame_paths)


def extract_viral_elements(video_meta, transcript_text, structure, config=None):
    """爆款元素提取。"""
    if config and config.get("llm", {}).get("enabled") and config["llm"].get("api_key"):
        print("  [LLM] 调用大模型提取爆款元素...", file=sys.stderr)
        result = llm_analyzer.llm_extract_viral_elements(video_meta, transcript_text, structure, config)
        if result:
            print("  [LLM] 爆款元素提取完成 ✓", file=sys.stderr)
            return result
        print("  [LLM] 降级到规则引擎", file=sys.stderr)
    return _rule_extract_viral_elements(video_meta, transcript_text, structure)


def generate_optimization(title, structure, viral_elements, config=None):
    """优化建议。"""
    if config and config.get("llm", {}).get("enabled") and config["llm"].get("api_key"):
        print("  [LLM] 调用大模型生成优化建议...", file=sys.stderr)
        result = llm_analyzer.llm_generate_optimization(title, structure, viral_elements, config)
        if result:
            print("  [LLM] 优化建议生成完成 ✓", file=sys.stderr)
            return result
        print("  [LLM] 降级到规则引擎", file=sys.stderr)
    return _rule_generate_optimization(title, structure, viral_elements)


def generate_optimized_script(title, transcript_text, structure, optimization, config=None):
    """优化后文案。"""
    if config and config.get("llm", {}).get("enabled") and config["llm"].get("api_key"):
        print("  [LLM] 调用大模型重写文案...", file=sys.stderr)
        result = llm_analyzer.llm_generate_optimized_script(title, transcript_text, structure, optimization, config)
        if result:
            print("  [LLM] 文案重写完成 ✓", file=sys.stderr)
            return result
        print("  [LLM] 降级到规则引擎", file=sys.stderr)
    return _rule_generate_optimized_script(title, transcript_text, structure, optimization)


# =========================================================================
# 主流水线
# =========================================================================

def run_full_analysis(url):
    """Execute the complete analysis pipeline."""
    data_dir = Path(__file__).parent.parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    config = load_config()

    print("=" * 55)
    print("  Video Analysis Pipeline")
    llm_enabled = config.get("llm", {}).get("enabled") and config["llm"].get("api_key")
    print(f"  分析引擎: {'LLM + 规则兜底' if llm_enabled else '纯规则引擎'}")
    print("=" * 55)

    # Step 1: Download
    print("\n[1/5] Downloading video...")
    meta = download_video(url, str(data_dir))
    if not meta or not meta.get("video_path"):
        print("ERROR: Download failed")
        return None

    video_path = meta["video_path"]
    video_id = meta["video_id"]
    print(f"  -> {video_path} ({meta.get('duration', 0)}s)")

    # Step 2: Transcribe
    print("\n[2/5] Transcribing audio...")
    transcript = transcribe_video(video_path, str(data_dir))
    transcript_text = transcript["text"] if transcript else ""
    print(f"  -> {len(transcript_text)} characters transcribed")

    # Step 3: Extract frames
    print("\n[3/5] Extracting key frames...")
    frame_result = extract_frames(video_path, count=6, output_dir=str(data_dir / f"frames_{video_id}"), config=config, transcript_text=transcript_text)
    frame_paths = [f["path"] for f in frame_result.get("frames", [])]
    print(f"  -> {len(frame_paths)} frames extracted")

    # Step 4: Analysis (LLM-first)
    print("\n[4/5] Analyzing content...")
    title = meta.get("title", "Unknown")

    structure = analyze_structure(transcript_text, title, config)
    frame_analysis = analyze_key_frames(transcript_text, title, frame_paths, config)
    viral_elements = extract_viral_elements(meta, transcript_text, structure, config)
    optimization = generate_optimization(title, structure, viral_elements, config)
    optimized_script = generate_optimized_script(title, transcript_text, structure, optimization, config)

    # Format structure analysis text
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

    # Format key frames text
    frames_text = "\n".join(
        f"[帧{f.get('frame', i+1)}] {f.get('role', '')} ({f.get('stage', '')})"
        for i, f in enumerate(frame_analysis)
    )

    # Build record fields (包含 LLM 新增的维度)
    fields = {
        "视频主题": title,
        "视频平台": meta.get("platform", "unknown"),
        "视频链接": url,
        "视频时长_秒": meta.get("duration", 0),
        "原文案": transcript_text,
        "文案结构分析": structure_text,
        "核心画面": frames_text,
        "爆款元素": viral_elements if isinstance(viral_elements, list) else [str(viral_elements)],
        "优化方向": "\n".join(f"{i+1}. {s}" for i, s in enumerate(optimization)),
        "优化后文案": optimized_script,
        "拆解状态": "已完成",
        "拆解时间": int(datetime.now().timestamp() * 1000),
        # LLM 新增字段
        "钩子类型": structure.get("hook_type", ""),
        "钩子评分": safe_int(structure.get("hook_score", 0)),
        "情绪曲线": structure.get("emotion_arc", ""),
        "主导情绪": structure.get("dominant_emotion", ""),
        "情绪强度": safe_int(structure.get("emotion_intensity", 0)),
        "标题公式": structure.get("title_formula", ""),
        "CTA类型": structure.get("cta_type", ""),
        "内容评分": safe_int(structure.get("content_score", 0)),
    }

    # Step 5: Push to Feishu
    print("\n[5/5] Pushing to Feishu Bitable...")
    helper = FeishuHelper(
        config["feishu"]["app_id"],
        config["feishu"]["app_secret"],
        config["feishu"]["bitable"]["app_token"],
        config["feishu"]["bitable"]["table_id"],
    )

    record_id = helper.create_record_with_files(fields, video_path=video_path, frame_paths=frame_paths)

    result = {
        "success": bool(record_id),
        "record_id": record_id,
        "video_id": video_id,
        "video_path": video_path,
        "frame_paths": frame_paths,
        "transcript_length": len(transcript_text),
        "viral_elements_count": len(viral_elements) if isinstance(viral_elements, list) else 0,
        "engine": "llm" if llm_enabled else "rule",
        "fields": fields,
    }

    # Save result
    result_path = data_dir / f"result_{video_id}.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n{'=' * 55}")
    if record_id:
        print(f"  Analysis complete! Record ID: {record_id}")
    else:
        print("  Analysis complete but Feishu push failed.")
        print("  Results saved locally.")
    print(f"  Result: {result_path}")
    print(f"{'=' * 55}")

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_and_push.py <VIDEO_URL>", file=sys.stderr)
        sys.exit(1)
    url = sys.argv[1]
    run_full_analysis(url)




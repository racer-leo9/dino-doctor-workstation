# -*- coding: utf-8 -*-
"""
LLM multimodal short video analysis module (enhanced)
Uses gpt-5.5 for text + image analysis via OpenAI-compatible API.
All outputs in simplified Chinese.
"""
import json, sys, re, base64
from typing import Optional
from pathlib import Path

# ---------------------------------------------------------------------------
# API core
# ---------------------------------------------------------------------------

def _call_llm(messages: list, config: dict, temperature: float = 0.3) -> Optional[str]:
    try:
        import requests
    except ImportError:
        print("[LLM] requests not installed", file=sys.stderr)
        return None

    llm_cfg = config.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    model = llm_cfg.get("model", "gpt-4o-mini")

    if not api_key:
        print("[LLM] api_key missing", file=sys.stderr)
        return None

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages, "temperature": temperature}

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[LLM] call failed: {e}", file=sys.stderr)
        return None


def _image_to_base64(image_path: str) -> Optional[str]:
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def _parse_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try: return json.loads(text[start:end + 1])
        except: pass
    return None


SYSTEM_PROMPT = (
    "You are a senior short video operations expert and content analyst. "
    "All outputs MUST be in simplified Chinese. Return results in JSON format."
)


# ---------------------------------------------------------------------------
# 1. Structure analysis (enhanced)
# ---------------------------------------------------------------------------

def llm_analyze_structure(transcript_text: str, video_title: str, config: dict) -> Optional[dict]:
    prompt = f"""请对以下短视频文案进行精炼拆解，只输出核心结论，每个分析字段控制在1-2句话。

【视频标题】{video_title}

【完整文案】
{transcript_text}

请返回以下 JSON（所有文本必须简体中文）：

{{
  "part1_text": "钩子原文（精确引用）",
  "part1_analysis": "钩子核心机制（1-2句）：用了什么心理钩子、为什么能留人",
  "part2_text": "核心内容原文（精确引用）",
  "part2_analysis": "内容核心逻辑（1-2句）：叙事类型、说服链条、信任手法",
  "part3_text": "CTA原文（精确引用）",
  "part3_analysis": "CTA核心手法（1句）：引导方式和转化设计",
  "hook_type": "钩子类型（悬念/痛点/反常识/数字/提问/共鸣/恐惧/利益/故事/挑战/身份认同/权威）",
  "hook_score": "钩子评分 1-10",
  "hook_detail": "钩子一句话拆解：心理机制+触发情绪+为什么能留住人",
  "emotion_arc": "情绪曲线（如：好奇->焦虑->信任->渴望->行动，标注时间节点）",
  "dominant_emotion": "主导情绪",
  "emotion_intensity": "情绪强度 1-10",
  "cta_type": "CTA类型（直接购买/关注收藏/评论互动/私信咨询/点击链接/引导转发）",
  "cta_detail": "CTA一句话点评：转化路径和门槛高低",
  "content_score": "内容质量评分 1-10",
  "rhetoric": ["修辞手法，每个附一句话说明作用"],
  "psychology_triggers": ["心理触发点，每个附一句话说明机制"],
  "target_audience": "目标受众（年龄段+核心痛点，一句话）",
  "pain_points": ["痛点列表，每个一句话"],
  "selling_points": ["卖点列表，每个一句话说明用户利益"],
  "total_sentences": "总句数"
}}"""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    raw = _call_llm(messages, config, temperature=0.2)
    if not raw:
        return None
    result = _parse_json(raw)
    if result and "part1_text" in result:
        return result
    print("[LLM] structure JSON parse failed", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# 2a. Multimodal key frame analysis (enhanced)
# ---------------------------------------------------------------------------

def llm_analyze_key_frames_multimodal(transcript_text: str, video_title: str, frame_paths: list, config: dict) -> Optional[list]:
    """Multimodal analysis: send key frame images to LLM for visual + text deep analysis."""
    llm_cfg = config.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    model = llm_cfg.get("model", "gpt-4o-mini")

    if not api_key:
        return None

    content = [
        {"type": "text", "text": f"""你是顶级短视频视觉分析专家。以下是视频的 {len(frame_paths)} 个关键帧截图和对应文案。
请对每一帧进行全方位视觉深度分析，包括画面内容、构图美学、色彩策略、文字排版、人物表情/肢体语言、产品展示技巧、视觉引导动线等。

【视频标题】{video_title}

【文案摘要】{transcript_text[:800]}

请返回 JSON 数组，每帧一个对象，分析要尽可能详细和专业：
[
  {{
    "frame": 1,
    "stage": "内容阶段（钩子/痛点呈现/解决方案/产品展示/效果对比/使用演示/社会证明/情感升华/CTA引导）",
    "visual_description": "画面详细描述（至少80字）：人物数量/位置/表情/肢体语言、场景环境、产品摆放、色调冷暖、光影效果、景深、镜头角度（俯拍/平拍/仰拍）、画面层次感",
    "composition": "构图分析：三分法/中心构图/对角线/引导线/框架构图、视觉焦点位置、留白比例、信息层级",
    "color_strategy": "色彩策略：主色调、对比色/互补色运用、色彩情绪传达、品牌色一致性",
    "text_overlay": "画面上的文字内容、字体大小/颜色/位置、文字与画面的融合度、是否有动态文字效果",
    "product_display": "产品展示方式分析：展示角度、使用场景还原、细节特写、对比展示、前后效果",
    "emotional_expression": "人物情绪表达：面部表情、肢体语言、眼神方向、情绪感染力",
    "visual_hooks": "视觉钩子：什么画面元素能在0.5秒内抓住注意力",
    "engagement_score": 8,
    "engagement_reason": "吸引力评分理由（至少30字）",
    "role": "这帧在整体视频结构中的策略意义和作用（至少40字）",
    "improvement": "具体优化建议（至少30字）：构图/色调/文字/产品展示等方面如何改进",
    "similar_brands": "这种视觉风格类似哪些知名品牌/账号的风格"
  }},
  ...
]"""}
    ]

    for i, path in enumerate(frame_paths[:8]):
        b64 = _image_to_base64(path)
        if b64:
            content.append({"type": "text", "text": f"--- 关键帧 {i+1} ---"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    try:
        import requests
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content}
            ], "temperature": 0.3},
            timeout=180
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        result = _parse_json(raw)
        if isinstance(result, list):
            for i, item in enumerate(result):
                if i < len(frame_paths):
                    item["path"] = str(frame_paths[i])
            return result
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, list) and len(v) > 0:
                    for i, item in enumerate(v):
                        if i < len(frame_paths):
                            item["path"] = str(frame_paths[i])
                    return v
    except Exception as e:
        print(f"[LLM] multimodal analysis failed: {e}", file=sys.stderr)

    return None


# ---------------------------------------------------------------------------
# 2b. Text-only key frame analysis (fallback)
# ---------------------------------------------------------------------------

def llm_analyze_key_frames(transcript_text: str, video_title: str, frame_count: int, config: dict) -> Optional[list]:
    prompt = f"""A short video has {frame_count} key frames. Title: {video_title}
Transcript: {transcript_text[:500]}
Please infer content role for each frame, return JSON array:
[{{"frame": 1, "role": "detailed description", "stage": "stage"}}]"""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    raw = _call_llm(messages, config)
    if not raw:
        return None
    result = _parse_json(raw)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for v in result.values():
            if isinstance(v, list):
                return v
    return None


# ---------------------------------------------------------------------------
# 3. Viral element extraction (enhanced)
# ---------------------------------------------------------------------------

def llm_extract_viral_elements(video_meta: dict, transcript_text: str, structure: dict, config: dict) -> Optional[list]:
    prompt = f"""你是短视频爆款分析专家，擅长从数据和心理学角度拆解爆款逻辑。
请从以下短视频中提取所有爆款元素和可复用的内容策略。

【视频标题】{video_meta.get('title', '')}
【平台】{video_meta.get('platform', '')}
【时长】{video_meta.get('duration', 0)}秒
【文案】{transcript_text[:600]}
【钩子类型】{structure.get('hook_type', '未知')} | 评分：{structure.get('hook_score', '?')}/10
【情绪曲线】{structure.get('emotion_arc', '未知')}
【CTA类型】{structure.get('cta_type', '未知')}
【目标受众】{structure.get('target_audience', '未知')}
【痛点】{', '.join(structure.get('pain_points', []))}
【卖点】{', '.join(structure.get('selling_points', []))}

提取5-8个最核心的爆款元素，每个用一句话说清机制和效果。

返回格式：["元素名: 一句话拆解（机制+效果）", ...]"""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    raw = _call_llm(messages, config)
    if not raw:
        return None
    result = _parse_json(raw)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for v in result.values():
            if isinstance(v, list):
                return v
    return None


# ---------------------------------------------------------------------------
# 4. Optimization suggestions (enhanced)
# ---------------------------------------------------------------------------

def llm_generate_optimization(video_title: str, structure: dict, viral_elements: list, config: dict) -> Optional[list]:
    prompt = f"""基于以下短视频分析结果，给出 5 条最核心的优化建议。
每条建议一句话说清：改什么、怎么改、预期效果。

【标题】{video_title}
【钩子】{structure.get('hook_type', '未知')}（{structure.get('hook_score', '?')}/10）-- {structure.get('hook_detail', '')}
【情绪】{structure.get('emotion_arc', '未知')}
【CTA】{structure.get('cta_type', '未知')} -- {structure.get('cta_detail', '')}
【目标受众】{structure.get('target_audience', '未知')}
【痛点】{', '.join(structure.get('pain_points', []))}
【卖点】{', '.join(structure.get('selling_points', []))}
【信任建设】{structure.get('trust_building', '')}
【平台适配】{structure.get('platform_adaptation', '')}
【爆款元素】{', '.join(str(e) for e in viral_elements[:8])}

按优先级排列最重要的5条改进点。

返回 JSON 数组：["建议1（一句话）", "建议2", ...]"""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    raw = _call_llm(messages, config)
    if not raw:
        return None
    result = _parse_json(raw)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for v in result.values():
            if isinstance(v, list):
                return v
    return None


# ---------------------------------------------------------------------------
# 5. Optimized script (enhanced)
# ---------------------------------------------------------------------------

def llm_generate_optimized_script(video_title: str, transcript_text: str, structure: dict, optimization: list, config: dict) -> Optional[str]:
    prompt = f"""请基于原视频文案和优化建议，重写一个更优版本的完整文案。
要求文案质量显著提升，保留原视频的核心价值，同时强化所有薄弱环节。

【原视频标题】{video_title}
【原文案】{transcript_text}
【目标受众】{structure.get('target_audience', '')}
【痛点】{', '.join(structure.get('pain_points', []))}
【卖点】{', '.join(structure.get('selling_points', []))}
【原钩子类型】{structure.get('hook_type', '')}
【原情绪曲线】{structure.get('emotion_arc', '')}

【优化建议】
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(optimization))}

重写要求：
1. 保持核心价值，强化薄弱环节
2. 钩子更抓人（前3秒制造强烈好奇/痛点/悬念）
3. 内容逻辑链更强，加入数据佐证和场景化描述
4. CTA更自然有力，降低行动门槛
5. 口语化、有感染力，适配15-60秒口播节奏

直接返回重写后的文案纯文本（不要JSON）。"""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    return _call_llm(messages, config, temperature=0.7)


# ---------------------------------------------------------------------------
# 6. Competitor analysis (enhanced)
# ---------------------------------------------------------------------------

def llm_competitor_analysis(video_title: str, transcript_text: str, structure: dict, config: dict) -> Optional[dict]:
    prompt = f"""你是短视频竞品分析专家，擅长从内容策略角度分析竞品差异。
基于这个口腔护理类短视频的内容，分析它与同类视频的差异优势和市场机会。

【标题】{video_title}
【文案】{transcript_text[:600]}
【钩子类型】{structure.get('hook_type', '')}
【情绪曲线】{structure.get('emotion_arc', '')}
【目标受众】{structure.get('target_audience', '')}
【痛点】{', '.join(structure.get('pain_points', []))}
【卖点】{', '.join(structure.get('selling_points', []))}

请返回 JSON（每个字段一句话，精准点明关键）：
{{
  "competitive_advantage": "一句话：相比竞品的核心优势",
  "content_positioning": "内容定位（教育型/种草型/测评型/故事型/对比型/专家型/情感型）",
  "unique_selling_angle": "一句话：独特切入角度",
  "improvement_opportunities": ["竞品常用但本视频缺的策略，每个一句话"],
  "market_gap": ["市场空白机会，每个一句话"]
}}"""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    raw = _call_llm(messages, config)
    if not raw:
        return None
    return _parse_json(raw)

# ---------------------------------------------------------------------------
# 7. Product name + title generation
# ---------------------------------------------------------------------------

def llm_generate_title(transcript_text: str, structure: dict, config: dict, viral_elements: list = None) -> Optional[str]:
    """Extract product name from transcript and structure, generate formatted title."""
    prompt = f"""Based on the following short video transcript and analysis, extract the product name and generate a concise title.

【Transcript】{transcript_text[:400]}
【Selling points】{', '.join(structure.get('selling_points', []))}
【Target audience】{structure.get('target_audience', '')}

Rules:
1. Identify the specific product name/brand mentioned in the transcript
2. If no specific brand, describe the product category
3. Return format: "Product Name | Brief Video Description"
4. Keep total length under 30 characters
5. All in simplified Chinese

Example: "碧舒菲漱口水 | 口腔护理好物推荐"
Example: "正畸保持器 | 清洁片使用教程"

Return ONLY the title string, no JSON, no quotes."""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    return _call_llm(messages, config, temperature=0.2)

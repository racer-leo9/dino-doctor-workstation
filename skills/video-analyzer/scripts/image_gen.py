# -*- coding: utf-8 -*-
"""
Image generation module using gpt-image-2-pro.
Generates reference images from keyframes + user prompts.
"""
import sys, json, base64, requests, os
from pathlib import Path


def generate_reference_image(image_path, prompt, config, output_dir=None):
    """
    Generate a reference image based on a keyframe + user prompt.
    
    Args:
        image_path: Path to the source keyframe image
        prompt: User's style/prompt text for image generation
        config: App config dict with LLM settings
        output_dir: Directory to save generated image
    
    Returns:
        dict with image_url, image_path, or None on failure
    """
    llm_cfg = config.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")

    if not api_key:
        print("[IMAGE_GEN] No API key", file=sys.stderr)
        return None

    # Read source image
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"[IMAGE_GEN] Failed to read image: {e}", file=sys.stderr)
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Call gpt-image-2-pro for image editing
    url = f"{base_url.rstrip('/')}/images/edits"
    
    # Build full prompt
    full_prompt = f"Based on this reference frame, generate a high-quality promotional image. Style: {prompt}. Keep the core product/scene composition, enhance visual quality, lighting, and details."

    try:
        print(f"[IMAGE_GEN] Generating with prompt: {prompt[:60]}...", file=sys.stderr)
        
        # Use multipart form upload for image editing
        import io
        img_data = base64.b64decode(img_b64)
        
        files = {
            "image": ("frame.png", io.BytesIO(img_data), "image/png"),
        }
        data = {
            "model": "gpt-image-2-pro",
            "prompt": full_prompt,
            "n": 1,
            "size": "1024x1024",
        }
        headers_multipart = {
            "Authorization": f"Bearer {api_key}",
        }
        
        resp = requests.post(url, headers=headers_multipart, files=files, data=data, timeout=120)
        resp.raise_for_status()
        result = resp.json()

        images = result.get("data", [])
        if not images:
            print("[IMAGE_GEN] No images returned", file=sys.stderr)
            return None

        img_item = images[0]
        
        # Save the generated image
        if output_dir is None:
            output_dir = Path(image_path).parent
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Handle both URL and base64 responses
        img_url = img_item.get("url", "")
        img_b64_resp = img_item.get("b64_json", "")
        
        output_path = output_dir / "ref_gen.png"
        
        if img_url:
            # Download from URL
            img_resp = requests.get(img_url, timeout=30)
            img_resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(img_resp.content)
            # Return local URL
            rel_path = os.path.relpath(str(output_path), str(output_dir.parent.parent)).replace("\\", "/")
            return {
                "image_url": f"/data/{rel_path}",
                "image_path": str(output_path),
                "source_url": img_url,
            }
        elif img_b64_resp:
            # Decode base64
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(img_b64_resp))
            rel_path = os.path.relpath(str(output_path), str(output_dir.parent.parent)).replace("\\", "/")
            return {
                "image_url": f"/data/{rel_path}",
                "image_path": str(output_path),
            }
        
        print("[IMAGE_GEN] No image data in response", file=sys.stderr)
        return None

    except Exception as e:
        print(f"[IMAGE_GEN] Error: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    import yaml
    config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    if len(sys.argv) < 3:
        print("Usage: python image_gen.py <image_path> <prompt>")
        sys.exit(1)
    
    result = generate_reference_image(sys.argv[1], sys.argv[2], config)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.exit(1)
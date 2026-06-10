import json, sys, time, os, re
from pathlib import Path

def extract_video_via_cdp(video_url, output_dir):
    """用现有 Chrome CDP 提取视频 URL 并下载"""
    import requests
    import websocket

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract video ID
    video_id = None
    for pattern in [r'/video/(\d+)', r'modal_id=(\d+)', r'(\d{15,})']:
        m = re.search(pattern, video_url)
        if m:
            video_id = m.group(1)
            break
    if not video_id:
        print("Cannot extract video ID", file=sys.stderr)
        return None

    # Get CDP tabs
    try:
        r = requests.get("http://127.0.0.1:9222/json", timeout=5)
        tabs = r.json()
    except Exception as e:
        print(f"CDP not available: {e}", file=sys.stderr)
        return None

    if not tabs:
        print("No CDP tabs", file=sys.stderr)
        return None

    ws_url = tabs[0]["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, timeout=30)

    # Navigate to video page
    print(f"Navigating to {video_url[:60]}...", file=sys.stderr)
    ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": video_url}}))
    ws.recv()

    # Wait for page load
    time.sleep(8)

    # Enable network monitoring
    ws.send(json.dumps({"id": 2, "method": "Network.enable"}))
    ws.recv()

    # Get video URL from DOM
    ws.send(json.dumps({"id": 3, "method": "Runtime.evaluate", "params": {
        "expression": '(() => { const v = document.querySelector("video"); if(v){v.play(); return v.currentSrc || v.src || "";} return ""; })()'
    }}))
    result = json.loads(ws.recv())
    video_src = result.get("result", {}).get("result", {}).get("value", "")

    if not video_src or not video_src.startswith("http"):
        # Try waiting more and extracting from network
        print("No video src in DOM, checking network...", file=sys.stderr)
        time.sleep(5)
        ws.send(json.dumps({"id": 4, "method": "Runtime.evaluate", "params": {
            "expression": '(() => { const v = document.querySelector("video"); if(v){v.play(); return v.currentSrc || v.src || "";} return ""; })()'
        }}))
        result = json.loads(ws.recv())
        video_src = result.get("result", {}).get("result", {}).get("value", "")

    # Also try getting title
    ws.send(json.dumps({"id": 5, "method": "Runtime.evaluate", "params": {
        "expression": 'document.title || ""'
    }}))
    title_result = json.loads(ws.recv())
    title = title_result.get("result", {}).get("result", {}).get("value", "")

    ws.close()

    if not video_src or not video_src.startswith("http"):
        print(f"No video URL found", file=sys.stderr)
        return None

    print(f"Video URL: {video_src[:80]}...", file=sys.stderr)

    # Download
    out_path = output_dir / f"video_{video_id}.mp4"
    resp = requests.get(video_src, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Referer": "https://www.douyin.com/",
    }, stream=True, timeout=120)

    if resp.status_code == 200:
        total = 0
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
                total += len(chunk)
        if total > 50000:
            print(f"Download OK: {total // 1024}KB", file=sys.stderr)
            meta = {
                "video_id": video_id, "title": title, "duration": 0,
                "platform": "douyin", "url": video_url,
                "video_path": str(out_path), "filesize": total
            }
            with open(output_dir / f"meta_{video_id}.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            return meta

    print(f"Download failed: {resp.status_code}", file=sys.stderr)
    return None

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.douyin.com/video/7555113510512266553"
    out = sys.argv[2] if len(sys.argv) > 2 else r"D:\Backup\Documents\逻辑分析流程\data"
    result = extract_video_via_cdp(url, out)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.exit(1)

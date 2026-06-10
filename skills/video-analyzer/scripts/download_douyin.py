# -*- coding: utf-8 -*-
"""Douyin video downloader - CDP mode (lightweight, no Selenium needed)"""
import subprocess, sys, json, os, re, time
from pathlib import Path

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"


def ensure_cdp_running():
    """Check if Chrome CDP is running; if not, restart Chrome with remote debugging."""
    import requests

    # Already running with CDP?
    try:
        r = requests.get(f"{CDP_URL}/json/version", timeout=3)
        if r.status_code == 200:
            return True
    except Exception:
        pass

    if not os.path.exists(CHROME_EXE):
        print(f"Chrome not found at {CHROME_EXE}", file=sys.stderr)
        return False

    # Kill existing Chrome that lacks CDP
    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                       capture_output=True, timeout=10)
        time.sleep(2)
        print("Killed existing Chrome, restarting with CDP...", file=sys.stderr)
    except Exception as e:
        print(f"Could not kill Chrome: {e}", file=sys.stderr)

    # Launch Chrome with CDP enabled (needs separate user-data-dir)
    cdp_profile = str(Path(__file__).parent.parent.parent / "chrome_cdp_profile")
    try:
        subprocess.Popen(
            [CHROME_EXE, f"--remote-debugging-port={CDP_PORT}",
             "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*",
             f"--user-data-dir={cdp_profile}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            time.sleep(0.5)
            try:
                r = requests.get(f"{CDP_URL}/json/version", timeout=2)
                if r.status_code == 200:
                    print("Chrome CDP auto-started", file=sys.stderr)
                    return True
            except Exception:
                pass
        print("Chrome CDP launch timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Failed to launch Chrome CDP: {e}", file=sys.stderr)
        return False



def normalize_douyin_url(url):
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    # Extract modal_id from any search/discover page URL
    modal_id = params.get("modal_id", [None])[0]
    if modal_id:
        return f"https://www.douyin.com/video/{modal_id}"
    # Handle /note/ URLs
    note_match = re.search(r'/note/(\d+)', url)
    if note_match:
        return f"https://www.douyin.com/video/{note_match.group(1)}"
    return url


def resolve_short_url(url):
    url = normalize_douyin_url(url)
    if "v.douyin.com" not in url:
        return url
    try:
        import requests
        resp = requests.head(url, allow_redirects=True, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
        return resp.url
    except:
        return url


def extract_video_id(url):
    for pattern in [r'/video/(\d+)', r'modal_id=(\d+)', r'(\d{15,})']:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def download_via_cdp(video_url, video_id, output_dir):
    """Use existing Chrome CDP to extract video URL and download"""
    import requests
    import websocket

    if not ensure_cdp_running():
        print("CDP unavailable for download", file=sys.stderr)
        return None

    try:
        r = requests.get(f"{CDP_URL}/json", timeout=5)
        tabs = r.json()
    except Exception as e:
        print(f"CDP not available: {e}", file=sys.stderr)
        return None

    if not tabs:
        print("No CDP tabs", file=sys.stderr)
        return None

    try:
        ws_url = tabs[0]["webSocketDebuggerUrl"]
        ws = websocket.create_connection(ws_url, timeout=60)
    except Exception as e:
        print(f"CDP connect failed: {e}", file=sys.stderr)
        return None

    msg_id = 0
    def send(method, params=None):
        nonlocal msg_id
        msg_id += 1
        msg = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params
        ws.send(json.dumps(msg))
        return msg_id

    def recv_until(target_id=None, timeout=30):
        results = []
        ws.settimeout(timeout)
        try:
            while True:
                data = json.loads(ws.recv())
                results.append(data)
                if target_id and data.get("id") == target_id:
                    break
        except:
            pass
        return results

    try:
        send("Network.enable")
        recv_until(msg_id, 3)
        send("Page.enable")
        recv_until(msg_id, 3)

        print(f"CDP: navigating to {video_url[:60]}...", file=sys.stderr)
        send("Page.navigate", {"url": video_url})
        recv_until(msg_id, 5)

        video_urls = []
        start = time.time()
        ws.settimeout(1)
        while time.time() - start < 15:
            try:
                data = json.loads(ws.recv())
                if data.get("method") == "Network.responseReceived":
                    resp = data["params"]["response"]
                    url = resp.get("url", "")
                    mime = resp.get("mimeType", "")
                    if "video" in mime or "douyinvod" in url or ".mp4" in url:
                        if url not in video_urls:
                            video_urls.append(url)
                            print(f"CDP network: {url[:80]}", file=sys.stderr)
            except:
                pass

        send("Runtime.evaluate", {
            "expression": '(() => { const v = document.querySelector("video"); if(v){v.play(); return v.currentSrc || v.src || "";} return ""; })()'
        })
        results = recv_until(msg_id, 5)
        for r in results:
            val = r.get("result", {}).get("result", {}).get("value", "")
            if val and val.startswith("http") and val not in video_urls:
                video_urls.append(val)
                print(f"CDP DOM: {val[:80]}", file=sys.stderr)

        send("Runtime.evaluate", {"expression": "document.title"})
        results = recv_until(msg_id, 5)
        title = ""
        for r in results:
            title = r.get("result", {}).get("result", {}).get("value", "")

        ws.close()
    except Exception as e:
        print(f"CDP error: {e}", file=sys.stderr)
        try:
            ws.close()
        except:
            pass
        return None

    if not video_urls:
        print("CDP: no video URL found", file=sys.stderr)
        return None

    # Filter out placeholder / background video URLs
    real_urls = [u for u in video_urls if not any(p in u for p in ["uuu_265", "douyin-pc-web", "douyin-web", "placeholder"])]
    if not real_urls:
        real_urls = video_urls  # fallback to all if filter removed everything
    print(f"CDP: downloading from {len(real_urls)} URLs...", file=sys.stderr)
    for url in real_urls:
        if not url.startswith("http"):
            continue
        try:
            resp = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                "Referer": "https://www.douyin.com/",
            }, stream=True, timeout=120)
            if resp.status_code == 200:
                out_path = output_dir / f"video_{video_id}.mp4"
                total = 0
                with open(out_path, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                        total += len(chunk)
                if total > 50000:
                    print(f"CDP download OK: {total // 1024}KB", file=sys.stderr)
                    meta = {
                        "video_id": video_id, "title": title, "duration": 0,
                        "platform": "douyin", "url": video_url,
                        "video_path": str(out_path), "filesize": total
                    }
                    with open(output_dir / f"meta_{video_id}.json", "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)
                    return meta
        except Exception as e:
            print(f"CDP download error: {e}", file=sys.stderr)

    return None


def download_douyin(video_url, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    video_url = resolve_short_url(video_url)
    video_id = extract_video_id(video_url)
    if not video_id:
        print(f"Cannot extract video ID: {video_url}", file=sys.stderr)
        return None
    print(f"Video ID: {video_id}", file=sys.stderr)

    # Try CDP first (lightweight, uses existing Chrome)
    result = download_via_cdp(video_url, video_id, output_dir)
    if result:
        return result

    print("CDP failed, no fallback available", file=sys.stderr)
    return None


# Alias for compatibility
download_video = download_douyin


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python download_douyin.py <URL> [output_dir]", file=sys.stderr)
        sys.exit(1)
    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else r"D:\Backup\Documents\逻辑分析流程\data"
    result = download_douyin(url, out)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.exit(1)




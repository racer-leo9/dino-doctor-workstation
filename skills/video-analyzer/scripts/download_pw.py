# -*- coding: utf-8 -*-
"""Playwright-based Douyin video downloader - no CDP or cookies needed."""
import asyncio, json, os, re, sys, time
from pathlib import Path

# URLs that are placeholder / site assets, NOT real user videos
BLACKLIST_PATTERNS = [
    "uuu_265", "douyin-pc-web", "douyin-web",
    "placeholder", "loading", "spinner",
    ".mp3", ".m4a",
]


def is_valid_video_url(url):
    """Check if URL is likely a real user video, not a site placeholder."""
    url_lower = url.lower()
    for pat in BLACKLIST_PATTERNS:
        if pat in url_lower:
            return False
    # Must be HTTP and have video-like indicators
    if not url.startswith("http"):
        return False
    return True


async def _download_async(video_url, output_dir):
    from playwright.async_api import async_playwright

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        video_urls = []

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            cl = int(response.headers.get("content-length", "0"))
            if ("video" in ct or "douyinvod" in url or "v26" in url or "v3-web" in url) and cl > 100000:
                if url not in video_urls and is_valid_video_url(url):
                    video_urls.append(url)
                    print(f"  [network] {cl//1024}KB {url[:100]}", file=sys.stderr)

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

        print(f"Navigating to {video_url[:80]}...", file=sys.stderr)
        try:
            await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Navigation warning: {e}", file=sys.stderr)

        # Wait and try to play video
        for attempt in range(6):
            await asyncio.sleep(2)
            # Try to find and play the video
            src = await page.evaluate("""
                () => {
                    // Find all video elements
                    const videos = document.querySelectorAll('video');
                    for (const v of videos) {
                        // Skip small/placeholder videos
                        if (v.duration > 0 && v.duration < 99999) {
                            const s = v.src || v.currentSrc || '';
                            if (s && s.startsWith('http') && s.length > 50) return s;
                        }
                        const s = v.src || v.currentSrc || '';
                        if (s && s.startsWith('http') && s.length > 50) return s;
                    }
                    // Check source elements
                    const sources = document.querySelectorAll('video source');
                    for (const s of sources) {
                        const src = s.src || '';
                        if (src.startsWith('http') && src.length > 50) return src;
                    }
                    return '';
                }
            """)
            if src and src.startswith("http") and src not in video_urls and is_valid_video_url(src):
                video_urls.append(src)
                print(f"  [DOM src] {src[:100]}", file=sys.stderr)

            # Click play if needed
            if attempt == 1:
                try:
                    await page.click('video', timeout=2000)
                except:
                    pass
                try:
                    play_btn = page.locator('[class*="play"], [class*="Play"], .xgplayer-start')
                    if await play_btn.count() > 0:
                        await play_btn.first.click(timeout=2000)
                except:
                    pass

        # Extract from page JSON data (SSR data)
        if not video_urls:
            html = await page.content()
            # Look for playAddr in SSR data
            for pat in [
                r'"playAddr"\s*:\s*\[\s*\{\s*"src"\s*:\s*"([^"]+)"',
                r'"play_addr"\s*:\s*\{\s*"url_list"\s*:\s*\[\s*"([^"]+)"',
                r'"download_addr"\s*:\s*\{\s*"url_list"\s*:\s*\[\s*"([^"]+)"',
            ]:
                found = re.findall(pat, html)
                for u in found:
                    u = u.replace('\\u002F', '/').replace('\\/', '/')
                    if u.startswith("http") and u not in video_urls and is_valid_video_url(u):
                        video_urls.append(u)
                        print(f"  [JSON] {u[:100]}", file=sys.stderr)
                if video_urls:
                    break

        # Also scan for any large video CDN URLs in HTML
        if not video_urls:
            all_urls = re.findall(r'"(https?://[^"]+)"', html)
            for u in all_urls:
                u = u.replace('\\u002F', '/').replace('\\/', '/')
                if ("douyinvod" in u or "v26" in u or "v3-web" in u) and u not in video_urls and is_valid_video_url(u):
                    video_urls.append(u)
                    print(f"  [HTML scan] {u[:100]}", file=sys.stderr)

        # Get title
        title = await page.evaluate("() => document.title || ''")
        title = re.sub(r'\s*[-|·].*$', '', title).strip()
        # Also try meta tags
        if not title or '抖音' in title:
            title = await page.evaluate("""
                () => {
                    const el = document.querySelector('[class*="title"], h1, [data-e2e="video-desc"]');
                    return el ? el.innerText.trim() : '';
                }
            """)

        await browser.close()

    # Filter: only keep URLs > 100KB (real videos are at least that)
    valid_urls = [u for u in video_urls if is_valid_video_url(u)]
    if not valid_urls:
        print(f"No valid video URL found (captured {len(video_urls)} total)", file=sys.stderr)
        return None

    print(f"Found {len(valid_urls)} candidate URLs, downloading...", file=sys.stderr)

    # Download the first valid URL
    import requests
    for url in valid_urls:
        print(f"Trying: {url[:100]}...", file=sys.stderr)
        try:
            resp = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.douyin.com/",
            }, stream=True, timeout=120)
            if resp.status_code == 200:
                vid_match = re.search(r'/(\d{15,})', video_url)
                video_id = vid_match.group(1) if vid_match else str(int(time.time()))
                out_path = output_dir / f"video_{video_id}.mp4"
                total = 0
                with open(out_path, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                        total += len(chunk)
                if total > 100000:  # At least 100KB
                    print(f"Download OK: {total // 1024}KB", file=sys.stderr)
                    return {
                        "video_id": video_id, "title": title, "duration": 0,
                        "uploader": "", "platform": "douyin", "url": video_url,
                        "video_path": str(out_path), "filesize": total,
                    }
                else:
                    print(f"  Too small ({total} bytes), skipping", file=sys.stderr)
                    os.remove(out_path)
        except Exception as e:
            print(f"  Error: {e}", file=sys.stderr)

    return None


def download_via_playwright(video_url, output_dir):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, _download_async(video_url, output_dir)).result()
            return result
    except RuntimeError:
        pass
    return asyncio.run(_download_async(video_url, output_dir))


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.douyin.com/video/7621099617526680241"
    out = sys.argv[2] if len(sys.argv) > 2 else r"D:\Backup\Documents\逻辑分析流程\data"
    result = download_via_playwright(url, out)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Failed", file=sys.stderr)
        sys.exit(1)
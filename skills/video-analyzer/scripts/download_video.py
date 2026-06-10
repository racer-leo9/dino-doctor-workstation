# -*- coding: utf-8 -*-
"""Download video from URL. Detects platform and uses appropriate downloader."""
import subprocess, sys, json, os, re, uuid, unicodedata
from pathlib import Path


def is_douyin_url(url):
    return bool(re.search(r'douyin\.com|iesdouyin\.com', url))


def normalize_url(url):
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    modal_id = params.get("modal_id", [None])[0]
    if modal_id:
        return f"https://www.douyin.com/video/{modal_id}"
    note_match = re.search(r'/note/(\d+)', url)
    if note_match:
        return f"https://www.douyin.com/video/{note_match.group(1)}"
    return url


def sanitize_filename(name, max_len=60):
    name = unicodedata.normalize('NFKD', name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = re.sub(r'[\s]+', '_', name).strip('_.')
    if len(name) > max_len:
        name = name[:max_len]
    return name or 'video'


def download_video(url, output_dir=None):
    if output_dir is None:
        output_dir = Path(__file__).parent.parent.parent / "data"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if is_douyin_url(url):
        url = normalize_url(url)

        # Method 1: CDP downloader
        try:
            from download_douyin import download_video as dl_douyin
            result = dl_douyin(url, str(output_dir))
            if result:
                return result
        except Exception as e:
            print(f"Douyin CDP failed: {e}", file=sys.stderr)

    # Method 2: yt-dlp
    yt_cmd = [sys.executable, "-m", "yt_dlp"]
    info_cmd = yt_cmd + ["--dump-json", "--no-download", "--encoding", "utf-8", url]
    info = {}
    try:
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=60, encoding='utf-8', errors='replace')
        if info_result.returncode == 0 and info_result.stdout.strip():
            info = json.loads(info_result.stdout)
    except Exception as e:
        print(f"yt-dlp info exception: {e}", file=sys.stderr)

    video_id = info.get("id") or str(uuid.uuid4().hex[:12])
    title = info.get("title", "")
    duration = info.get("duration", 0)
    uploader = info.get("uploader", "")
    platform = info.get("extractor_key") or info.get("extractor", "unknown")
    safe_title = sanitize_filename(title) if title else video_id
    output_template = str(output_dir / f"video_{safe_title}_{video_id}.%(ext)s")

    dl_cmd = yt_cmd + [
        "-f", "best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--no-playlist",
        "--encoding", "utf-8",
        "--socket-timeout", "30",
        "--no-check-certificates",
        url,
    ]
    print(f"Downloading via yt-dlp: {title or url}", file=sys.stderr)
    result = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=300, encoding='utf-8', errors='replace')

    if result.returncode != 0:
        print(f"yt-dlp error: {result.stderr[:500]}", file=sys.stderr)
        return None

    video_path = None
    for pattern in [f"video_*_{video_id}.*", f"video_{video_id}.*", f"video_{safe_title}_{video_id}.*"]:
        for f in output_dir.glob(pattern):
            if f.suffix in (".mp4", ".mkv", ".webm", ".flv"):
                video_path = str(f)
                break
        if video_path:
            break

    if not video_path:
        candidates = sorted(output_dir.glob("video_*.mp4"), key=os.path.getmtime, reverse=True)
        if candidates:
            video_path = str(candidates[0])

    if not video_path or not os.path.exists(video_path):
        return None

    filesize = os.path.getsize(video_path)
    if filesize < 1000:
        return None

    metadata = {
        "video_id": video_id, "title": title, "duration": duration,
        "uploader": uploader, "platform": platform, "url": url,
        "video_path": video_path, "filesize": filesize,
    }
    meta_path = output_dir / f"meta_{video_id}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"Download OK: {filesize // 1024}KB -> {video_path}", file=sys.stderr)
    return metadata


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python download_video.py <VIDEO_URL>", file=sys.stderr)
        sys.exit(1)
    url = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    result = download_video(url, output_dir)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Download failed", file=sys.stderr)
        sys.exit(1)
# -*- coding: utf-8 -*-
"""通过 OpenCLI 抓取抖音视频公开数据"""
import subprocess, sys, json, re, time
from pathlib import Path


def scrape_douyin_data(video_url, video_id=None):
    """用 OpenCLI 浏览器抓取抖音视频页面数据"""
    if not video_id:
        m = re.search(r'/video/(\d+)', video_url)
        if m:
            video_id = m.group(1)
    
    result = {
        "video_id": video_id,
        "url": video_url,
        "title": "",
        "author": "",
        "author_url": "",
        "publish_time": "",
        "duration": "",
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "favorites": 0,
        "hashtags": [],
        "top_comments": [],
        "related_videos": [],
        "scrape_ok": False
    }
    
    try:
        # Step 1: Open page in OpenCLI browser session
        print("OpenCLI: opening page...", file=sys.stderr)
        r = _run_opencli(["browser", "douyin_data", "open", video_url], timeout=30)
        if not r:
            print("OpenCLI: failed to open page", file=sys.stderr)
            return result
        
        # Wait for page load
        time.sleep(6)
        
        # Step 2: Extract page content
        print("OpenCLI: extracting data...", file=sys.stderr)
        r = _run_opencli(["browser", "douyin_data", "extract"], timeout=20)
        if r:
            data = json.loads(r) if r.strip().startswith('{') else {"content": r}
            content = data.get("content", r)
            result.update(_parse_page_content(content))
            result["scrape_ok"] = True
        
        # Step 3: Get author info
        r = _run_opencli(["browser", "douyin_data", "find", '--css', 'a[href*="user/MS4w"]', "--limit", "3"], timeout=10)
        if r:
            try:
                find_data = json.loads(r)
                entries = find_data.get("entries", [])
                for e in entries:
                    text = e.get("text", "").strip()
                    href = e.get("attrs", {}).get("href", "")
                    if text and len(text) < 30 and not result["author"]:
                        result["author"] = text
                        if href.startswith("//"):
                            href = "https:" + href
                        result["author_url"] = href
                        break
            except:
                pass
        
        print(f"OpenCLI: done - {result['likes']} likes, {result['comments']} comments", file=sys.stderr)
        
    except Exception as e:
        print(f"OpenCLI error: {e}", file=sys.stderr)
    
    return result


def _parse_page_content(content):
    """从页面文本中解析数据"""
    data = {
        "title": "",
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "favorites": 0,
        "hashtags": [],
        "publish_time": "",
        "duration": "",
        "top_comments": [],
        "related_videos": []
    }
    
    if not content:
        return data
    
    lines = content.split("\n")
    
    # Title: line containing hashtags
    for line in lines:
        line = line.strip()
        if "#" in line and ("牙膏" in line or "伢典" in line or len(line) > 20):
            # Clean title
            title = re.sub(r'\[.*?\]\(.*?\)', '', line).strip()
            title = re.sub(r'!\[.*?\]\(.*?\)', '', title).strip()
            if title and len(title) > 10:
                data["title"] = title
                break
    
    # Find the 4 stat numbers (likes, comments, shares, favorites)
    # They appear as a group of 4 numbers
    stat_pattern = re.findall(r'\n(\d{1,6})\n.*?\n(\d{1,6})\n.*?\n(\d{1,6})\n.*?\n(\d{1,6})\n', content)
    if stat_pattern:
        nums = [int(x) for x in stat_pattern[0]]
        data["likes"] = nums[0]
        data["comments"] = nums[1]
        data["shares"] = nums[2]
        data["favorites"] = nums[3]
    else:
        # Try another pattern - numbers on separate lines near keywords
        numbers = re.findall(r'\n(\d{1,6})\n', content)
        if len(numbers) >= 4:
            data["likes"] = int(numbers[0])
            data["comments"] = int(numbers[1])
            data["shares"] = int(numbers[2])
            data["favorites"] = int(numbers[3])
    
    # Publish time
    time_match = re.search(r'发布时间[：:]\s*([\d-]+\s*[\d:]+)', content)
    if time_match:
        data["publish_time"] = time_match.group(1)
    
    # Duration
    dur_match = re.search(r'(\d{2}:\d{2})\s*/\s*(\d{2}:\d{2})', content)
    if dur_match:
        data["duration"] = dur_match.group(2)
    
    # Hashtags
    hashtags = re.findall(r'(#[^\s\[\]#]+)', content)
    data["hashtags"] = list(set(hashtags))[:10]
    
    # Parse comments
    comment_lines = re.findall(r'\[([^\]]+)\]\(//www\.douyin\.com/user/.*?\)\n\n(.*?)\n\n.*?(\d+[周天月年]前·.*?)\n\n(\d+)', content)
    for name, text, time_str, likes in comment_lines:
        text_clean = re.sub(r'!\[.*?\]\(.*?\)', '', text).strip()
        if text_clean:
            data["top_comments"].append({
                "user": name.strip(),
                "text": text_clean[:100],
                "time": time_str.strip(),
                "likes": int(likes) if likes.isdigit() else 0
            })
    
    # Related videos (from sidebar recommendations)
    related = re.findall(r'\n\n([^\n]{10,60})\n\n(?:3s 后播放)', content)
    data["related_videos"] = [r.strip() for r in related[:5]]
    
    return data


def _run_opencli(args, timeout=30):
    """执行 opencli 命令"""
    cmd = [r"C:\Users\Administrator\AppData\Roaming\npm\opencli.cmd"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            print(f"opencli error: {result.stderr[:200]}", file=sys.stderr)
            return None
    except subprocess.TimeoutExpired:
        print(f"opencli timeout after {timeout}s", file=sys.stderr)
        return None
    except Exception as e:
        print(f"opencli exception: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.douyin.com/video/7636406289506533041"
    result = scrape_douyin_data(url)
    print(json.dumps(result, ensure_ascii=False, indent=2))

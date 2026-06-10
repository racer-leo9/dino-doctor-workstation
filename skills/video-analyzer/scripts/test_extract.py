import sys, json
sys.path.insert(0, r"D:\Backup\Documents\逻辑分析流程\skills\video-analyzer\scripts")

from extract_frames import extract_frames
import yaml

with open(r"D:\Backup\Documents\逻辑分析流程\config\settings.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

with open(r"D:\Backup\Documents\逻辑分析流程\data\transcript_7375701199506525492.json", "r", encoding="utf-8") as f:
    transcript = json.load(f)

video_path = r"D:\Backup\Documents\逻辑分析流程\data\video_7375701199506525492.mp4"
output_dir = r"D:\Backup\Documents\逻辑分析流程\data\test_frames_v2"

print("=== Testing extract_frames v2 ===")
result = extract_frames(
    video_path,
    count=6,
    output_dir=output_dir,
    config=config,
    transcript_text=transcript["text"]
)

print("")
print("=== Result ===")
print("Frames:", result.get("frame_count", 0))
print("Stages:", len(result.get("stages", [])))
for s in result.get("stages", []):
    print("  %s: %.1fs - %.1fs" % (s["name"], s["start"], s["end"]))
for f in result.get("frames", []):
    q = f.get("quality", {})
    print("  Frame %d: %.1fs [%s] quality=%.0f blur=%.0f face=%.0f" % (
        f["index"], f["timestamp"], f["stage"],
        q.get("total", 0), q.get("blur", 0), q.get("face", 0)
    ))
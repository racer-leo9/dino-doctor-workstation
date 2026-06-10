# -*- coding: utf-8 -*-
"""Ensure required tools (ffmpeg, yt-dlp) are in PATH."""
import os, shutil
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent.parent.parent / "tools"

def ensure_tools():
    """Add tools directory to PATH if not already present."""
    tools = str(TOOLS_DIR)
    if tools not in os.environ.get("PATH", ""):
        os.environ["PATH"] = tools + ";" + os.environ.get("PATH", "")
    # Also check common install locations
    for p in [r"D:\Backup\Documents\逻辑分析流程\tools"]:
        if p not in os.environ.get("PATH", "") and Path(p).exists():
            os.environ["PATH"] = p + ";" + os.environ.get("PATH", "")

# Auto-run on import
ensure_tools()

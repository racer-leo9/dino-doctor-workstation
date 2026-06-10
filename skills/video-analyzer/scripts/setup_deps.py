# -*- coding: utf-8 -*-
"""Install all dependencies for the video analyzer skill."""
import subprocess, sys, shutil

def run(cmd):
    print(f"  > {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    WARN: {r.stderr.strip()[:200]}")
    return r.returncode == 0

def main():
    print("="*50)
    print("  Video Analyzer - Dependency Setup")
    print("="*50)

    py = sys.executable

    # 1. Python packages
    print("\n[1/3] Installing Python packages...")
    run(f'"{py}" -m pip install --upgrade yt-dlp openai-whisper requests pyyaml Pillow')

    # 2. ffmpeg
    print("\n[2/3] Checking ffmpeg...")
    if shutil.which("ffmpeg"):
        print("  ffmpeg already installed.")
    else:
        print("  ffmpeg not found. Installing via winget...")
        if not run("winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements"):
            print("  Auto-install failed. Please install ffmpeg manually:")
            print("  https://www.gyan.dev/ffmpeg/builds/")
            print("  Or: choco install ffmpeg / winget install Gyan.FFmpeg")

    # 3. yt-dlp
    print("\n[3/3] Checking yt-dlp...")
    if shutil.which("yt-dlp"):
        print("  yt-dlp already installed.")
    else:
        print("  yt-dlp not found in PATH. It was installed via pip.")
        print("  If not found, run: pip install yt-dlp")

    print("\n" + "="*50)
    print("  Setup complete!")
    print("="*50)

if __name__ == "__main__":
    main()

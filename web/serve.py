# -*- coding: utf-8 -*-
"""Production server using waitress (Windows-compatible, multi-threaded)."""
import sys, os

# Add paths
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'video-analyzer', 'scripts'))

from app import app
from waitress import serve

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    print(f"Starting production server on port {port} (4 threads, 120s timeout)")
    serve(
        app,
        host="0.0.0.0",
        port=port,
        threads=4,
        channel_timeout=120,
        recv_bytes=65536,
        ident="video-dissect",
    )
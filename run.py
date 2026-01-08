#!/usr/bin/env python3
"""Run the Base Rate Arbitrage Scanner."""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
import uvicorn

load_dotenv()

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 8000))

    print(f"Starting Base Rate Arbitrage Scanner at http://{host}:{port}")
    uvicorn.run(
        "src.web.app:app",
        host=host,
        port=port,
        reload=True
    )

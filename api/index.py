"""Vercel entry point: the FastAPI app (app/main.py) as one Python function.

vercel.json sends every path here, so the API keeps its normal URLs
(/connections, /auth/login, /health, ...).
"""
import sys
from pathlib import Path

# the project root (where app/ is), whatever folder Vercel runs this from
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401  (Vercel serves the ASGI `app`)

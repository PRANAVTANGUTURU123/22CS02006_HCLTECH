"""Server entry point: .\\.venv\\Scripts\\python -m uvicorn backend.main:app --port 8000"""
import logging

from backend.api import create_app
from backend.settings import get_settings

logging.basicConfig(level=get_settings().log_level)
app = create_app()

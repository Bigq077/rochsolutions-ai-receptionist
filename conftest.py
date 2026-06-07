"""
Root conftest.py — loaded by pytest before any test module.
Calls load_dotenv() so environment variables from .env (including
ANTHROPIC_API_KEY, ASSEMBLYAI_API_KEY, etc.) are available to all tests
without needing to import app.main.
"""
from pathlib import Path
from dotenv import load_dotenv

# Use explicit path so the .env is found regardless of CWD when pytest starts.
load_dotenv(Path(__file__).parent / ".env", override=True)

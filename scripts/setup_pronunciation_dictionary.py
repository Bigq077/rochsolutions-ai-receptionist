"""
One-time setup script: create an ElevenLabs pronunciation dictionary
for Alcester (/ˈɔːlstər/) and Redditch, then write the returned IDs to
config/pronunciation_dict.json so tts_stream.py can load them at startup.

Run once:
    python scripts/setup_pronunciation_dictionary.py
"""
import json
import os
import sys
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Resolve project root and load .env if present
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Try to load .env so ELEVENLABS_API_KEY is available without exporting it
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # dotenv not installed — rely on environment variable being set

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
if not ELEVENLABS_API_KEY:
    sys.exit("ERROR: ELEVENLABS_API_KEY is not set. Export it or add it to .env")

# ---------------------------------------------------------------------------
# Pronunciation rules
# ---------------------------------------------------------------------------
RULES = [
    {
        "string_to_replace": "Alcester",
        "type": "phoneme",
        "alphabet": "cmu-arpabet",
        "phoneme": "AO1 L S T ER0",
    },
    {
        "string_to_replace": "alcester",
        "type": "phoneme",
        "alphabet": "cmu-arpabet",
        "phoneme": "AO1 L S T ER0",
    },
    {
        "string_to_replace": "Redditch",
        "type": "phoneme",
        "alphabet": "cmu-arpabet",
        "phoneme": "R EH1 D IH0 CH",
    },
    {
        "string_to_replace": "redditch",
        "type": "phoneme",
        "alphabet": "cmu-arpabet",
        "phoneme": "R EH1 D IH0 CH",
    },
]

# ---------------------------------------------------------------------------
# Create the dictionary
# ---------------------------------------------------------------------------
url = "https://api.elevenlabs.io/v1/pronunciation-dictionaries/add-from-rules"
headers = {
    "xi-api-key":   ELEVENLABS_API_KEY,
    "Content-Type": "application/json",
}
payload = {
    "name":        "theorem_health_places",
    "description": "Alcester (/ˈɔːlstər/) and Redditch pronunciation corrections",
    "rules":       RULES,
}

print("Creating pronunciation dictionary...")
print(f"  Endpoint : {url}")
print(f"  Rules    : {len(RULES)}")

response = httpx.post(url, json=payload, headers=headers, timeout=30.0)

print(f"\nHTTP status: {response.status_code}")
print(f"Response body:\n{json.dumps(response.json(), indent=2)}")

if response.status_code not in (200, 201):
    sys.exit(f"ERROR: API returned {response.status_code}")

data = response.json()
pronunciation_dictionary_id = data.get("id") or data.get("pronunciation_dictionary_id")
version_id = data.get("version_id")

if not pronunciation_dictionary_id or not version_id:
    sys.exit(
        f"ERROR: could not extract IDs from response.\n"
        f"Keys returned: {list(data.keys())}"
    )

print(f"\npronunciation_dictionary_id : {pronunciation_dictionary_id}")
print(f"version_id                  : {version_id}")

# ---------------------------------------------------------------------------
# Write IDs to config/pronunciation_dict.json
# ---------------------------------------------------------------------------
out_path = PROJECT_ROOT / "config" / "pronunciation_dict.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_data = {
    "pronunciation_dictionary_id": pronunciation_dictionary_id,
    "version_id": version_id,
}
out_path.write_text(json.dumps(out_data, indent=2) + "\n")
print(f"\nWritten to: {out_path}")
print(json.dumps(out_data, indent=2))

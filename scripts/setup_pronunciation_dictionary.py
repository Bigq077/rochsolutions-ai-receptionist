"""
One-time setup script: create an ElevenLabs pronunciation dictionary
using ALIAS tags (compatible with ALL models including eleven_flash_v2_5)
for Alcester and Redditch, then write the returned IDs to
config/pronunciation_dict.json so tts_stream.py can load them at startup.

Alias approach:
  ElevenLabs substitutes the alias string internally before synthesis.
  "Alcester" → "AWL-stuh" produces /ˈɔːlstər/ (correct British English).
  "Redditch" → "Reditch" removes the doubled-d artefact.

NOTE: Phoneme/CMU-Arpabet tags are NOT used here because eleven_flash_v2_5
does not support them. Alias tags work on all models.

Run once:
    python scripts/setup_pronunciation_dictionary.py

The hook in app/media_streams/tts_stream.py (_get_pron_dict_locator) will
automatically load the written IDs and inject them into every TTS request.
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
# Pronunciation rules — ALIAS type (works on eleven_flash_v2_5 and all models)
# ---------------------------------------------------------------------------
def _alias_both_cases(word: str, alias: str) -> list:
    """Alias rule for both the capitalised and lowercased forms of a word."""
    return [
        {"string_to_replace": word,          "type": "alias", "alias": alias},
        {"string_to_replace": word.lower(),  "type": "alias", "alias": alias.lower()},
    ]


RULES = [
    # ── Theorem places (Alcester / Redditch) ───────────────────────────────
    # "AWL-stuh" → /ˈɔːlstər/; "Reditch" removes the doubled-d artefact.
    *_alias_both_cases("Alcester", "AWL-stuh"),
    *_alias_both_cases("Redditch", "Reditch"),
    # ── Joint Venture Physiotherapy (jv_v1) terms (P8) ──────────────────────
    # ElevenLabs (eleven_flash_v2_5) mispronounced these; alias respellings
    # steer it to the intended British pronunciation. Adjust the alias if a
    # term still sounds off after a test call.
    *_alias_both_cases("acupuncture",   "AK-yoo-punk-cher"),
    *_alias_both_cases("physiotherapy", "fizzee-oh-THERR-uh-pee"),
    *_alias_both_cases("Lythgoe",       "LITH-goh"),
    *_alias_both_cases("Walkden",       "WAWK-den"),
    *_alias_both_cases("Worsley",       "WURZ-lee"),
]

# ---------------------------------------------------------------------------
# Create the dictionary via ElevenLabs REST API
# ---------------------------------------------------------------------------
url = "https://api.elevenlabs.io/v1/pronunciation-dictionaries/add-from-rules"
headers = {
    "xi-api-key":   ELEVENLABS_API_KEY,
    "Content-Type": "application/json",
}
payload = {
    "name":        "susie_pronunciations",
    "description": (
        "Pronunciation corrections for Susie AI receptionist "
        "(eleven_flash_v2_5, alias rules): Alcester/Redditch + "
        "Joint Venture Physiotherapy terms (acupuncture, physiotherapy, "
        "Lythgoe, Walkden, Worsley)"
    ),
    "rules":       RULES,
}

print("Creating pronunciation dictionary...")
print(f"  Endpoint : {url}")
print(f"  Rules    : {len(RULES)}")
print(f"  Body     :\n{json.dumps(payload, indent=2)}")
print()

response = httpx.post(url, json=payload, headers=headers, timeout=30.0)

print(f"HTTP status: {response.status_code}")
print(f"Response body:\n{json.dumps(response.json(), indent=2)}")
print()

if response.status_code not in (200, 201):
    sys.exit(f"ERROR: API returned {response.status_code} — dictionary NOT created.")

data = response.json()

# ---------------------------------------------------------------------------
# Extract IDs — ElevenLabs returns "id" not "pronunciation_dictionary_id"
# ---------------------------------------------------------------------------
pronunciation_dictionary_id = data.get("id") or data.get("pronunciation_dictionary_id")
version_id                  = data.get("version_id")

if not pronunciation_dictionary_id or not version_id:
    print(f"ERROR: could not extract IDs. Keys returned: {list(data.keys())}", file=sys.stderr)
    print(f"Full response: {json.dumps(data, indent=2)}", file=sys.stderr)
    sys.exit(1)

print(f"pronunciation_dictionary_id : {pronunciation_dictionary_id}")
print(f"version_id                  : {version_id}")

# ---------------------------------------------------------------------------
# Write IDs to config/pronunciation_dict.json
# ---------------------------------------------------------------------------
out_path = PROJECT_ROOT / "config" / "pronunciation_dict.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_data = {
    "pronunciation_dictionary_id": pronunciation_dictionary_id,
    "version_id":                  version_id,
}
out_path.write_text(json.dumps(out_data, indent=2) + "\n", encoding="utf-8")

print(f"\nWritten to config/pronunciation_dict.json")
print(f"ID:      {pronunciation_dictionary_id}")
print(f"Version: {version_id}")

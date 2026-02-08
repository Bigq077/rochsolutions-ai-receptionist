# app/clinic_loader.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
CLINICS_DIR = BASE_DIR / "clinics"

def load_clinic(clinic_id: str) -> Dict[str, Any]:
    clinic_id = (clinic_id or "").strip().lower()
    if not clinic_id:
        clinic_id = "demo"

    clinic_path = CLINICS_DIR / clinic_id / "clinic.json"
    knowledge_path = CLINICS_DIR / clinic_id / "knowledge.md"

    if not clinic_path.exists():
        raise FileNotFoundError(f"Clinic config not found: {clinic_path}")

    clinic = json.loads(clinic_path.read_text(encoding="utf-8"))
    clinic["clinic_id"] = clinic_id
    clinic["knowledge_md"] = knowledge_path.read_text(encoding="utf-8") if knowledge_path.exists() else ""

    return clinic

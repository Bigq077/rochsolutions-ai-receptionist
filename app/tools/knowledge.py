# app/tools/knowledge.py
from typing import Dict, Any


def build_clinic_knowledge(clinic: Dict[str, Any]) -> str:
    """
    Single source of truth for FAQ answers.
    This becomes the "knowledge blob" for your LLM.
    """
    services = clinic.get("services", [])
    insurers = clinic.get("common_insurers", [])

    return "\n".join(
        [
            f"Clinic name: {clinic.get('display_name', 'Clinic')}",
            f"Location: {clinic.get('address', '')}",
            f"Parking: {clinic.get('parking', '')}",
            f"Opening hours: {clinic.get('hours_summary', '')}",
            f"Pricing: {clinic.get('pricing_summary', '')}",
            f"Services: {', '.join(services) if services else ''}",
            f"Insurance: {clinic.get('insurance_note', '')}",
            f"Common insurers: {', '.join(insurers) if insurers else ''}",
            f"Cancellation policy: {clinic.get('cancellation_policy', '')}",
            f"What to bring: {clinic.get('what_to_bring', '')}",
        ]
    ).strip()

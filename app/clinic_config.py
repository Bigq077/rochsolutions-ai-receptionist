CLINICS = {
    "demo": {
        "display_name": "RochSolutions Clinic (Demo)",
        "timezone": "Europe/London",
        "calendar_id": "primary",

        # booking rules
        "slot_minutes": 30,
        "days_ahead": 7,
        "working_hours": {
            "mon": (9, 18),
            "tue": (9, 18),
            "wed": (9, 18),
            "thu": (9, 18),
            "fri": (9, 18),
            "sat": None,
            "sun": None,
        },

        # FAQ / info
        "address": "Demo address — set per clinic",
        "parking": "Demo parking — set per clinic",
        "hours_summary": "Mon–Fri 9am–6pm",
        "pricing_summary": "Initial £50–£90, follow-up £40–£75 (varies by clinic).",
        "services": ["Physiotherapy", "Sports therapy", "MSK rehab", "Injury assessment"],
        "insurance_note": "Most clinics provide receipts for insurance claims; coverage depends on your policy.",
        "common_insurers": ["Bupa", "AXA Health", "Vitality", "Aviva", "WPA", "Cigna", "Simplyhealth"],

        # policies
        "cancellation_policy": "Please give 24h notice to avoid late cancellation fees (varies by clinic).",
        "what_to_bring": "Photo ID, any scans/reports, and comfortable clothing.",
    }
}

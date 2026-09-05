"""A clinic with red-flag screens ON, for testing the screening machinery.

WHY THIS EXISTS. The screening tests used to reach for `get_clinic("jv_v1")`,
because JV happened to be a live clinic that had the six screens switched on.
That was always a coincidence rather than a design: the tests are not about JV,
they are about the machinery — trigger matching, the yes/no grader, orphan
detection, the re-ask cap, the booking backstop.

On 2026-09-05 the coincidence ran out. northgate's screens went off in the
morning and the suite did not notice, because JV was still carrying the
fixture. When JV followed the same afternoon, 217 tests failed and 89 errored
in one step — not because anything regressed, but because the last clinic the
tests could point at had stopped screening.

The machinery is still in the codebase and is still one config key from live,
so it must stay tested. It just needs a subject of its own instead of
borrowing whichever clinic happens to be configured for it today.

WHAT THIS IS NOT. It is not a clinic. It has no phone number, it is not in
`app/clinics/`, and nothing routes to it. It is jv_v1's screen definitions —
the six real, Marcus-reviewed screens — with `enabled` forced True so the code
under test actually runs. If those definitions are ever edited, these tests
still measure the real thing.

DEEP COPY IS LOAD-BEARING. `get_clinic()` returns a cached dict; mutating it in
place would leak `enabled: True` into every later test in the same session and
quietly re-enable screening for assertions that are checking it is OFF.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

from app.clinic_config import get_clinic


def screening_clinic(clinic_id: str = "jv_v1") -> Dict[str, Any]:
    """*clinic_id*'s config with red-flag screening forced ON.

    Defaults to jv_v1 because its six screens are the reviewed set the whole
    suite was written against.
    """
    clinic = copy.deepcopy(get_clinic(clinic_id) or {})
    clinic.setdefault("clinical_screening", {})["enabled"] = True
    # The fluency MANDATE was the other key flipped on 2026-09-05, and the
    # prompt-render tests assert the mandated wording. Restoring both puts the
    # clinic back in the shape the machinery tests were written against --
    # which is the point of the fixture. `mandatory` defaults to True when
    # absent, so this is the pre-flip default, not an invention.
    clinic.setdefault("condition_knowledge", {})["mandatory"] = True
    return clinic


def screening_clinic_json(clinic_id: str = "jv_v1") -> Dict[str, Any]:
    """The same thing for tests that read `clinic.json` off disk directly.

    A couple of files load the raw file rather than going through
    `get_clinic()`, to assert against the definitions as written. They still
    need `enabled` forced on for the classifier to run.
    """
    import json
    from pathlib import Path

    raw = json.loads(
        Path(f"app/clinics/{clinic_id}/clinic.json").read_text(encoding="utf-8")
    )
    raw.setdefault("clinical_screening", {})["enabled"] = True
    raw.setdefault("condition_knowledge", {})["mandatory"] = True
    return raw

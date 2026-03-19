"""
healer.py — Per-test autonomous diagnosis and fixing agent.

After every test:
  1. build_diagnosis()  → prints a structured summary of what happened
  2. run_fixer_agent()  → Claude uses tool-use to read code, edit files,
                          and decide whether to retry or skip

The fixer can handle any class of failure:
  • Infrastructure (ngrok dead, 0 turns, cold server)   → retry immediately
  • Test expectation drift (Susie said X, test expects Y) → edit all_scenarios.py, retry
  • Susie server bug (wrong flow, wrong phrase, timer bug) → edit app/ files,
                                                             git push, wait for
                                                             Render redeploy, retry
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path

import httpx
import anthropic

from .config import ANTHROPIC_API_KEY, RENDER_SERVER_URL
from .transcript import build_transcript

logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parents[2]
MAX_REDEPLOYS_PER_RUN = 5   # cap on git-push+Render-redeploy cycles
MAX_TOOL_ITERATIONS   = 20  # max Claude tool-use calls per fixer invocation

# ── Claude tools available to the fixer agent ─────────────────────────────────

_FIXER_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a source file from the repository. "
            "Returns numbered lines starting at `offset` (0-based), up to `limit` lines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":   {"type": "string",  "description": "File path relative to repo root, e.g. app/media_streams/connection.py"},
                "offset": {"type": "integer", "description": "Line number to start from (0-based)", "default": 0},
                "limit":  {"type": "integer", "description": "Max lines to return (default 150)",   "default": 150},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for a regex pattern in Python source files. Returns matching lines with file:line context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern":   {"type": "string", "description": "Regex to search for"},
                "directory": {"type": "string", "description": "Subdirectory to search (relative to repo root)", "default": "app"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace an exact substring in a file. "
            "`old_string` must appear exactly once. "
            "Provide enough surrounding context to make it unique."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":       {"type": "string", "description": "File path relative to repo root"},
                "old_string": {"type": "string", "description": "Exact string to replace (must be unique in file)"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "reason":     {"type": "string", "description": "One-line explanation of why this change fixes the bug"},
            },
            "required": ["path", "old_string", "new_string", "reason"],
        },
    },
    {
        "name": "done",
        "description": "Signal that investigation and fixing is complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["retry", "retry_after_redeploy", "skip"],
                    "description": (
                        "retry = try the test again now (no server change or only test-file changes), "
                        "retry_after_redeploy = you edited app/ files which need a Render redeploy first, "
                        "skip = cannot safely fix this right now"
                    ),
                },
                "summary": {"type": "string", "description": "One sentence describing what was done or why skipping"},
            },
            "required": ["action", "summary"],
        },
    },
]

_FIXER_SYSTEM = """You are an autonomous bug-fixing agent embedded inside the Susie automated test runner.

Susie is an AI phone receptionist for a physiotherapy clinic, deployed on Render.
The test runner calls Susie via Twilio, records what Susie says, and evaluates it.

When a test fails you will receive:
- A structured diagnosis
- The test scenario (what the caller says and what was expected)
- A summary of what Susie actually said

Your job:
1. Use read_file / search_code to understand the relevant code
2. Identify the precise root cause
3. Use edit_file to apply the minimal fix
4. Call done() with the appropriate action

═══ IMPORTANT RULES ═══
• Only edit files in: app/media_streams/  OR  tests/auto/scenarios/
• NEVER change a scenario's "id" or "responses" keys
• NEVER delete scenarios
• Make the MINIMAL change — do not refactor, do not add comments
• If you edited app/ files → call done(action="retry_after_redeploy")
  (Render will redeploy automatically from the git push; takes ~2-3 min)
• If you only edited tests/auto/scenarios/ → call done(action="retry")
  (no server redeploy needed)
• If the failure is clearly infrastructure (0 turns, ngrok died, timeout,
  cold server) → call done(action="retry") WITHOUT editing anything
• If you cannot safely fix it → call done(action="skip")

═══ KEY FILES ═══
app/media_streams/connection.py  — SilenceHandler (re-ask logic), greeting constant, WebSocket pipeline
app/media_streams/flow.py        — FlowEngine, booking steps, slot formatting, LLM prompts
tests/auto/scenarios/all_scenarios.py  — test scenario definitions and expected values
"""


# ── Healer class ──────────────────────────────────────────────────────────────

class Healer:
    """
    Stateful healer — tracks redeploy count so we don't thrash Render.
    One instance per test run.
    """

    def __init__(self):
        self.redeploy_count = 0
        self._client = (
            anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            if ANTHROPIC_API_KEY else None
        )

    # ── public API ────────────────────────────────────────────────────────────

    def build_diagnosis(self, result: dict, scenario: dict) -> str:
        """Return a formatted per-test diagnosis block (always, pass or fail)."""
        evaluation  = result.get("evaluation", {})
        passed      = evaluation.get("passed", False)
        status      = "✓ PASS" if passed else "✗ FAIL"
        turns       = result.get("turns", 0)
        end_reason  = result.get("end_reason", "unknown")
        duration    = result.get("duration_seconds", 0.0)

        lines = [
            "═" * 58,
            f"[{scenario['id']}]  {scenario['name']}  →  {status}",
            f"  turns={turns}  end_reason={end_reason}  duration={duration:.1f}s",
        ]

        # Transcript (capped at 25 lines)
        transcript = evaluation.get("transcript") or build_transcript(result)
        if transcript:
            lines.append("  Transcript:")
            for tline in transcript.strip().split("\n")[:25]:
                lines.append(f"    {tline}")
        else:
            lines.append("  Transcript: (empty — no turns recorded)")

        if not passed:
            # Failed checks
            checks      = evaluation.get("checks", {})
            fail_checks = [k for k, v in checks.items() if v is False]
            if fail_checks:
                lines.append("  Failed checks:")
                for c in fail_checks:
                    lines.append(f"    • {c}")

            detail = evaluation.get("detail", "")
            if detail:
                lines.append(f"  Evaluator detail: {detail}")

            # Quick root-cause classification (human-readable)
            if turns == 0 or end_reason in ("timeout", "timeout_no_speech", "ngrok_died"):
                lines.append("  Quick diagnosis: Infrastructure failure — no conversation happened")
            elif "flow_completed" in fail_checks and end_reason == "completed":
                lines.append("  Quick diagnosis: Call ended prematurely (Twilio hung up before scenario finished)")
            elif any("greeting" in c for c in fail_checks):
                lines.append("  Quick diagnosis: Greeting phrase mismatch")
            elif "reask_fired" in fail_checks:
                lines.append("  Quick diagnosis: Silence re-ask did not fire when expected")
            elif "booking_confirmed" in fail_checks:
                lines.append("  Quick diagnosis: Booking confirmation phrase missing or undetected")
            elif fail_checks:
                lines.append(f"  Quick diagnosis: See failed checks above ({fail_checks[0]})")

        lines.append("═" * 58)
        return "\n".join(lines)

    async def run_fixer_agent(self, diagnosis: str, result: dict, scenario: dict) -> str:
        """
        Run the autonomous fixer.
        Returns one of: "retry", "skip".
        ("retry" covers both immediate retry and retry-after-redeploy —
        the redeploy wait happens inside this function.)
        """
        if not self._client:
            logger.warning("[healer] No ANTHROPIC_API_KEY — skipping fixer")
            return "skip"

        # Fast path: infrastructure failures don't need code analysis
        turns      = result.get("turns", 0)
        end_reason = result.get("end_reason", "unknown")
        if turns == 0 or end_reason in ("timeout", "timeout_no_speech", "ngrok_died"):
            print("  Fixer: Infrastructure failure — retrying without code change")
            return "retry"

        print("  Fixer: Analysing failure and searching for root cause...")

        user_msg = (
            f"DIAGNOSIS:\n{diagnosis}\n\n"
            f"SCENARIO:\n{json.dumps({k: v for k, v in scenario.items() if k not in ('responses',)}, indent=2, default=str)}\n\n"
            f"WHAT SUSIE ACTUALLY SAID:\n"
            + "\n".join(f"  Turn {t['turn']}: {t['text'][:120]}" for t in result.get("susie_said", []))
        )

        messages = [{"role": "user", "content": user_msg}]

        server_files_changed: list[str] = []
        test_files_changed:   list[str] = []
        final_action = None
        final_summary = ""

        for _iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response = await asyncio.to_thread(
                    self._client.messages.create,
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=_FIXER_SYSTEM,
                    tools=_FIXER_TOOLS,
                    messages=messages,
                )
            except Exception as exc:
                logger.error("[healer] Claude API error: %r", exc)
                return "skip"

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break

            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                rc, is_err = self._run_tool(block.name, block.input,
                                            server_files_changed, test_files_changed)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     rc,
                    **({"is_error": True} if is_err else {}),
                })

                if block.name == "done":
                    final_action  = block.input.get("action", "skip")
                    final_summary = block.input.get("summary", "")

            messages.append({"role": "user", "content": tool_results})

            if final_action:
                break

        if not final_action:
            final_action = "skip"
            final_summary = "Fixer reached iteration limit without a decision"

        print(f"  Fixer decision: {final_action} — {final_summary}")

        # Push any edits and (if needed) wait for Render
        all_changed = server_files_changed + test_files_changed
        if all_changed:
            if server_files_changed and self.redeploy_count >= MAX_REDEPLOYS_PER_RUN:
                logger.warning("[healer] Max redeploys (%d) reached — reverting server changes",
                               MAX_REDEPLOYS_PER_RUN)
                for f in server_files_changed:
                    subprocess.run(["git", "checkout", "--", f],
                                   cwd=str(REPO_ROOT), capture_output=True)
                return "skip"

            await self._git_push(all_changed)

            if server_files_changed:
                self.redeploy_count += 1
                print(f"  Waiting for Render to redeploy "
                      f"({self.redeploy_count}/{MAX_REDEPLOYS_PER_RUN})...")
                await self._wait_for_render()

            return "retry"

        return "retry" if final_action == "retry" else "skip"

    # ── tool executor ─────────────────────────────────────────────────────────

    def _run_tool(
        self,
        name: str,
        inp:  dict,
        server_changed: list[str],
        test_changed:   list[str],
    ) -> tuple[str, bool]:
        """Execute one tool call. Returns (result_text, is_error)."""
        try:
            if name == "read_file":
                path   = REPO_ROOT / inp["path"]
                text   = path.read_text(encoding="utf-8")
                lines  = text.split("\n")
                offset = int(inp.get("offset", 0))
                limit  = int(inp.get("limit",  150))
                chunk  = lines[offset : offset + limit]
                numbered = "\n".join(
                    f"{offset + i + 1}: {l}" for i, l in enumerate(chunk)
                )
                return (
                    f"Lines {offset+1}–{offset+len(chunk)} of {len(lines)}:\n{numbered}",
                    False,
                )

            elif name == "search_code":
                import re as _re
                pattern   = inp["pattern"]
                directory = REPO_ROOT / inp.get("directory", "app")
                try:
                    rx = _re.compile(pattern)
                except _re.error:
                    rx = _re.compile(_re.escape(pattern))
                matches: list[str] = []
                for pyfile in sorted(Path(directory).rglob("*.py")):
                    try:
                        for lineno, line in enumerate(
                            pyfile.read_text(encoding="utf-8", errors="replace").splitlines(),
                            start=1,
                        ):
                            if rx.search(line):
                                rel = pyfile.relative_to(REPO_ROOT)
                                matches.append(f"{rel}:{lineno}: {line.rstrip()}")
                                if len(matches) >= 60:
                                    break
                    except Exception:
                        pass
                    if len(matches) >= 60:
                        break
                return ("\n".join(matches) if matches else "(no matches)", False)

            elif name == "edit_file":
                file_path = inp["path"]
                # Safety check
                if not (file_path.startswith("app/media_streams/")
                        or file_path.startswith("tests/auto/scenarios/")):
                    return (
                        f"ERROR: {file_path} is not in an editable directory. "
                        "Only app/media_streams/ and tests/auto/scenarios/ are allowed.",
                        True,
                    )
                path    = REPO_ROOT / file_path
                old     = inp["old_string"]
                new     = inp["new_string"]
                reason  = inp.get("reason", "")
                content = path.read_text(encoding="utf-8")
                count   = content.count(old)
                if count == 0:
                    return (f"ERROR: old_string not found in {file_path}", True)
                if count > 1:
                    return (
                        f"ERROR: old_string found {count} times — must be unique. "
                        "Add more surrounding context.", True,
                    )
                path.write_text(content.replace(old, new, 1), encoding="utf-8")
                if file_path.startswith("app/"):
                    server_changed.append(file_path)
                else:
                    test_changed.append(file_path)
                logger.info("[healer] Edited %s: %s", file_path, reason)
                return (f"✓ Edit applied to {file_path}. Reason: {reason}", False)

            elif name == "done":
                return ("Acknowledged.", False)

            else:
                return (f"Unknown tool: {name}", True)

        except Exception as exc:
            return (f"Error running {name}: {exc}", True)

    # ── git + Render helpers ──────────────────────────────────────────────────

    async def _git_push(self, files: list[str]):
        """Stage, commit, and push the given files."""
        print(f"  Pushing fix: {', '.join(files)}")
        try:
            subprocess.run(
                ["git", "add", "--"] + files,
                cwd=str(REPO_ROOT), check=True, capture_output=True,
            )
            msg = "Auto-fix: " + ", ".join(Path(f).name for f in files[:3])
            subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=str(REPO_ROOT), check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=str(REPO_ROOT), check=True, capture_output=True,
            )
            logger.info("[healer] Pushed: %s", files)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode() if exc.stderr else str(exc)
            logger.error("[healer] Git push failed: %s", stderr)

    async def _wait_for_render(self, timeout: int = 300):
        """Poll /health until Render comes back up after redeploy."""
        url = f"{RENDER_SERVER_URL}/health"
        # Give Render time to start the build
        await asyncio.sleep(40)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(url)
                if resp.status_code < 500:
                    logger.info("[healer] Render ready (HTTP %d)", resp.status_code)
                    await asyncio.sleep(10)  # extra settle time
                    return
            except Exception:
                pass
            await asyncio.sleep(10)
        logger.warning("[healer] Render redeploy wait timed out after %ds", timeout)

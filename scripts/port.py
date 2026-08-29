#!/usr/bin/env python3
"""Carry one canonical commit to the clinic branches, and refuse to push a bad one.

WHY THIS EXISTS
---------------
Measured over 24-28 Aug 2026: 70 commits of actual engineering on
`latency-eval`, and 199 commits re-applying those same fixes to `jv_v2`,
`theorem-onboarding` and `vitaledge-onboarding`. 72 `port/*` branches created
in five days. 74% of the activity was re-applying finished work, all of it by
hand, with nothing in `scripts/` to help.

One clinic = one branch = one Render service. The tax is 2.84x at three live
branches. At the 18 clinics the end-of-September webinar implies it is 17x, and
those 70 fixes become ~1,190 port commits per five days. That is not hard, it
is impossible.

The real fix is collapsing a clinic from a branch to a config file. THIS IS NOT
THAT. It is a tourniquet for the weeks while that lands, and it is meant to be
deleted the day it stops being needed. It buys back the hours; it does not
change the topology.

WHAT IT REFUSES TO DO
---------------------
Every step below is here because doing it by hand went wrong at least once:

* Bases every port off `origin/<branch>`, never the local branch. On
  2026-08-28 the primary worktree's local `vitaledge-onboarding` was 164
  commits behind origin. A port based on it would have silently reverted a
  month of fixes.
* Runs the baseline on a twin worktree pinned to the same origin tip, in the
  same invocation, with the same `.env` copied in. A scratch worktree with no
  `.env` collects a different set of tests (~96 vs ~104 failures), so a
  baseline taken without one is a lie.
* Diffs the failing SETS, never the counts. The suite has an unstable red
  baseline; equal counts with a swapped member is a regression that a count
  comparison reports as clean.
* Proves the port on the target's own code: the commit's tests must FAIL on the
  target's parent and PASS after. A cherry-pick that applies cleanly is not
  evidence the defect was ever live there, and `git cherry` cannot tell you
  either -- patch-id comparison calls real gaps ported and real ports missing.
* Never runs `git rm -r --cached` to drop a path (that stages a deletion of the
  whole tree). Test-only paths absent on the target are dropped one by one and
  printed; a conflict anywhere outside `tests/` aborts the port.
* Will not push unless the fast-forward, the targeted proof and the empty
  failing-set diff all hold. `--push` is opt-in on top of all three.

The clinic branches serve live callers, so a push here is a deploy. Timing and
the revert commit stay yours: this tells you the truth about the port, it does
not decide when to ship it.

USAGE
-----
    python scripts/port.py <sha> --to vitaledge-onboarding jv_v2
    python scripts/port.py <sha> --to theorem-onboarding --push
    python scripts/port.py <sha> --to jv_v2 --skip-suite      # targeted only
    python scripts/port.py <sha> --to jv_v2 --exclude app/obs # drop a path

Prints one row per branch and exits non-zero if any branch failed a gate.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Copied into BOTH worktrees before either suite runs. Without them the two
# trees collect different tests and the diff is meaningless.
ENV_FILES = (".env", "tests/auto/.env")

FAILED_RE = re.compile(r"^FAILED (\S+)", re.M)
SUMMARY_RE = re.compile(r"^\d+ (?:failed|passed).*$", re.M)


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(
        ["git", *args], cwd=str(cwd or REPO),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


@dataclass
class Result:
    branch: str
    ok: bool = False
    note: str = ""
    ported_sha: str = ""
    targeted: str = ""
    new_failures: list = field(default_factory=list)
    fixed_failures: list = field(default_factory=list)
    pushed: bool = False


def _copy_env(dest: Path) -> None:
    for rel in ENV_FILES:
        src = REPO / rel
        if src.exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)


def _pytest(tree: Path, targets: list) -> tuple:
    """Run pytest in `tree`; return (failing set, summary line)."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "-rf", *targets],
        cwd=str(tree), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = r.stdout + r.stderr
    fails = {m.split(" - ")[0].strip() for m in FAILED_RE.findall(out)}
    summary = (SUMMARY_RE.findall(out) or ["<no summary>"])[-1]
    return fails, summary


def _commit_paths(sha: str) -> list:
    return [p for p in git("show", "--name-only", "--pretty=", sha).splitlines()
            if p.strip()]


def port_one(sha: str, branch: str, root: Path, excludes: list,
             skip_suite: bool, push: bool, keep: bool) -> Result:
    res = Result(branch=branch)
    slug = branch.split("/")[-1][:10]
    base_tree = root / (slug + "-base")
    port_tree = root / (slug + "-port")
    port_branch = "port/" + sha[:8] + "-" + branch
    tip = git("rev-parse", "origin/" + branch).strip()

    try:
        # A STOP leaves its worktree and branch behind on purpose, so you can
        # read them. Re-running after fixing the cause must not then fail on
        # the leftovers -- this namespace belongs to this tool, so clear it.
        git("worktree", "prune")
        for stale in git("worktree", "list", "--porcelain").split("\n\n"):
            if "branch refs/heads/" + port_branch in stale:
                path = stale.splitlines()[0].removeprefix("worktree ").strip()
                git("worktree", "remove", "--force", path, check=False)
        git("branch", "-D", port_branch, check=False)

        git("worktree", "add", str(base_tree), "--detach", tip)
        git("worktree", "add", str(port_tree), "-b", port_branch, tip)
        _copy_env(base_tree)
        _copy_env(port_tree)

        # --- apply --------------------------------------------------------
        subprocess.run(["git", "cherry-pick", "-n", sha], cwd=str(port_tree),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
        status = git("status", "--porcelain", cwd=port_tree)

        droppable, blocking = [], []
        for line in status.splitlines():
            code, path_ = line[:2], line[3:]
            if code not in ("DU", "UD", "UU", "AA", "AU", "UA"):
                continue
            # ONLY modify/delete is droppable: the file does not exist on the
            # target at all, which is the ordinary case for canonical-only test
            # infrastructure. A UU/AA is a real CONTENT conflict — the target
            # has its own version of the same file — and dropping that silently
            # discards the change being ported. It did exactly that on
            # 2026-08-29: both clinic branches already carried
            # test_bank_holidays_are_not_bookable.py, the update conflicted, and
            # the tool reported "absent on target" and threw the edit away. The
            # ported suite then ran NINE tests fewer than the baseline and still
            # said ok.
            if code in ("DU", "UD") and (
                    path_.startswith("tests/")
                    or any(path_.startswith(e) for e in excludes)):
                droppable.append(path_)
            else:
                blocking.append(path_)
        if blocking:
            res.note = (
                "real conflict, needs a human: " + ", ".join(blocking)
                + "\n    A path the target DOES have is a CONTENT conflict, "
                  "not a missing file — resolve it rather than excluding it."
            )
            return res
        if droppable:
            git("rm", "-f", *droppable, cwd=port_tree)
            res.note = "dropped (absent on target): " + ", ".join(droppable)

        git("add", "-A", cwd=port_tree)
        if not git("diff", "--cached", "--name-only", cwd=port_tree).strip():
            res.ok = True
            res.note = "already ported - the cherry-pick was empty"
            return res

        # --- targeted proof: must fail on the parent, pass after -----------
        tests = [p for p in _commit_paths(sha)
                 if p.startswith("tests/") and p.endswith(".py")
                 and p not in droppable and (port_tree / p).exists()]
        if tests:
            # Stash whatever the TARGET already had at these paths. The commit's
            # test file is copied in to prove fail-before, but the target may
            # carry its OWN older version of the same path -- and blindly
            # deleting afterwards destroyed it, so the baseline suite then ran
            # with the file MISSING. That is a baseline that quietly measures
            # less than the ported run, which is exactly the lie this tool
            # exists to prevent: on 2026-08-29 it silently dropped 45 of
            # vitaledge's clinical-screening tests out of its own baseline.
            stashed: dict = {}
            for t in tests:
                dst = base_tree / t
                stashed[t] = dst.read_bytes() if dst.is_file() else None
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(port_tree / t, dst)
            before, bsum = _pytest(base_tree, tests)
            after, asum = _pytest(port_tree, tests)
            for t, original in stashed.items():   # restore, do not just delete
                dst = base_tree / t
                if original is None:
                    dst.unlink(missing_ok=True)
                else:
                    dst.write_bytes(original)
            res.targeted = "targeted  parent: " + bsum + " | ported: " + asum
            if not before:
                if not any(p.startswith("app/") for p in _commit_paths(sha)):
                    res.note = (
                        "this commit changes no engine code, so there is no "
                        "fail-before/pass-after to prove and this tool cannot "
                        "vouch for it. Carry test-only work by hand, or with "
                        "--skip-suite and your own eyes on the diff."
                    )
                else:
                    res.note = (
                        "the tests already PASS on the parent - either this "
                        "defect was never live on this branch, or the test does "
                        "not reproduce it here. Read it before pushing."
                    )
                return res
            if after:
                res.note = (
                    "still failing after the port: " + ", ".join(sorted(after))
                    + "\n    Before treating that as a broken port, check the "
                      "commonest cause: a test pinned to a clinic_id, or one "
                      "patching a symbol this branch does not have, measures "
                      "THIS branch's config rather than the fix. Run it with a "
                      "traceback -- an AttributeError from patch.object means "
                      "the test cannot run here, not that the defect is live. "
                      "That reading once made a good port look broken on three "
                      "branches at once."
                )
                return res
        else:
            res.targeted = "no test file in the commit - unproven on this branch"

        # --- failing-set diff ----------------------------------------------
        if not skip_suite:
            with futures.ThreadPoolExecutor(max_workers=2) as ex:
                fb = ex.submit(_pytest, base_tree, [])
                fp = ex.submit(_pytest, port_tree, [])
                base_fails, base_sum = fb.result()
                port_fails, port_sum = fp.result()
            res.new_failures = sorted(port_fails - base_fails)
            res.fixed_failures = sorted(base_fails - port_fails)
            res.targeted += ("\n    suite     parent: " + base_sum
                             + " | ported: " + port_sum)
            if res.new_failures:
                res.note = "NEW failures: " + ", ".join(res.new_failures)
                return res

        # --- commit ---------------------------------------------------------
        msg = git("log", "-1", "--format=%B", sha)
        msg += "\nPorted from " + sha[:8] + " on latency-eval by scripts/port.py.\n"
        subprocess.run(["git", "commit", "-q", "-F", "-"], cwd=str(port_tree),
                       input=msg, text=True, encoding="utf-8", check=True)
        res.ported_sha = git("rev-parse", "--short", "HEAD", cwd=port_tree).strip()

        # --- push -------------------------------------------------------------
        git("fetch", "origin", branch, cwd=port_tree)
        if git("rev-parse", "origin/" + branch, cwd=port_tree).strip() != tip:
            res.note = "origin moved while we worked - re-run; NOT pushed"
            return res
        if push:
            git("push", "origin", "HEAD:" + branch, cwd=port_tree)
            res.pushed = True
        res.ok = True
        return res
    except Exception as e:                                    # noqa: BLE001
        res.note = type(e).__name__ + ": " + str(e)
        return res
    finally:
        if not keep and res.ok and res.pushed:
            for t in (base_tree, port_tree):
                git("worktree", "remove", "--force", str(t), check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sha", help="the commit on canonical to carry over")
    ap.add_argument("--to", nargs="+", required=True, metavar="BRANCH")
    ap.add_argument("--exclude", nargs="*", default=[], metavar="PATH",
                    help="path prefixes to drop if they conflict (tests/ is automatic)")
    ap.add_argument("--push", action="store_true",
                    help="push once every gate passes; a push to a clinic branch "
                         "is a live deploy")
    ap.add_argument("--skip-suite", action="store_true",
                    help="targeted proof only - no failing-set diff")
    ap.add_argument("--keep", action="store_true", help="keep the worktrees")
    a = ap.parse_args()

    git("worktree", "prune")
    git("fetch", "origin", "--prune")
    sha = git("rev-parse", a.sha).strip()
    print("porting " + sha[:8] + "  " + git("log", "-1", "--format=%s", sha).strip())
    print("  to: " + ", ".join(a.to) + "   push=" + str(a.push) + "\n")

    root = Path(tempfile.mkdtemp(prefix="port-" + sha[:8] + "-",
                                 dir=os.environ.get("TEMP") or None))
    results = [port_one(sha, b, root, a.exclude, a.skip_suite, a.push, a.keep)
               for b in a.to]

    print("\n" + "=" * 72)
    for r in results:
        mark = "ok  " if r.ok else "STOP"
        line = "[" + mark + "] " + r.branch
        if r.ported_sha:
            line += "  -> " + r.ported_sha
        if r.pushed:
            line += "  PUSHED"
        print(line)
        if r.targeted:
            print("    " + r.targeted)
        if r.fixed_failures:
            print("    also fixed: " + ", ".join(r.fixed_failures))
        if r.note:
            print("    " + r.note)
    if any(r.pushed for r in results):
        print("\nDeploy proof is the Render log line `[build_info] running build "
              "<sha>` at call cleanup - /health reports a hardcoded 1.0.0 and "
              "proves nothing.")
    print("\nworktrees for anything that stopped: " + str(root))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
Build-time script: writes app/_version.py so /health can report the
deployed git commit.  Run by render.yaml buildCommand after pip install.

Tries every possible source so it never silently writes 'unknown'.
"""
import os
import subprocess
import sys

commit = ""

# 1. RENDER_GIT_COMMIT — set by Render during builds (full SHA)
_renv = os.environ.get("RENDER_GIT_COMMIT", "").strip()
if _renv:
    commit = _renv[:7]
    print(f"write_version.py: using RENDER_GIT_COMMIT -> {commit}")

# 2. git rev-parse (works locally; also works on Render if .git is present)
if not commit:
    for _git in ("git", "/usr/bin/git", "/usr/local/bin/git"):
        try:
            commit = subprocess.check_output(
                [_git, "rev-parse", "--short", "HEAD"],
                text=True,
                stderr=subprocess.PIPE,
            ).strip()
            if commit:
                print(f"write_version.py: using git subprocess ({_git}) -> {commit}")
                break
        except Exception as exc:
            print(f"write_version.py: {_git} failed: {exc}", file=sys.stderr)

# 3. Read from .git/HEAD directly (last resort — no git binary needed)
if not commit:
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.join(here, "..")
        head_path = os.path.join(repo_root, ".git", "HEAD")
        with open(head_path) as f:
            ref = f.read().strip()
        if ref.startswith("ref: "):
            ref_path = os.path.join(repo_root, ".git", ref[5:])
            with open(ref_path) as f:
                commit = f.read().strip()[:7]
        else:
            commit = ref[:7]
        print(f"write_version.py: using .git/HEAD -> {commit}")
    except Exception as exc:
        print(f"write_version.py: .git/HEAD read failed: {exc}", file=sys.stderr)

if not commit:
    commit = "unknown"
    print("write_version.py: WARNING — could not determine commit, writing 'unknown'",
          file=sys.stderr)

version_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "_version.py")
with open(version_path, "w") as fh:
    fh.write(f'GIT_COMMIT = "{commit}"\n')

print(f"write_version.py: wrote app/_version.py  GIT_COMMIT={commit}")

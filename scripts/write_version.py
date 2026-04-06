"""
Build-time script: writes app/_version.py so /health can report the
deployed git commit.  Run by render.yaml buildCommand after pip install.

Resolution order:
  1. RENDER_GIT_COMMIT env var  (set automatically by Render during builds)
  2. git subprocess             (works locally where the env var is absent)
"""
import os
import subprocess

commit = os.environ.get("RENDER_GIT_COMMIT", "")[:7]

if not commit:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        commit = "unknown"

version_path = os.path.join(os.path.dirname(__file__), "..", "app", "_version.py")
with open(version_path, "w") as fh:
    fh.write(f'GIT_COMMIT = "{commit}"\n')

print(f"write_version.py: GIT_COMMIT={commit}")

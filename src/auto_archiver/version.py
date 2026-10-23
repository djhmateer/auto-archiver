"""Version information for the auto_archiver package.

The upstream version comes from pyproject.toml. When running from a git checkout,
the HEAD commit datetime (UTC) is added as a local version, e.g. 1.2.9+20261023.1430
"""

import os
import subprocess
from importlib.metadata import version as get_version

VERSION_SHORT = get_version("auto_archiver")


def _git_commit_datetime() -> str:
    try:
        return subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format-local:%Y%m%d.%H%M"],
            cwd=os.path.dirname(__file__),
            env={**os.environ, "TZ": "UTC"},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        # no git or not a git checkout (e.g. Docker image)
        return ""


_commit = _git_commit_datetime()
__version__ = f"{VERSION_SHORT}+{_commit}" if _commit else VERSION_SHORT

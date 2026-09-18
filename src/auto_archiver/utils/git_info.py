"""Reads the git commit hash of the running code, for inclusion in output as evidence of exact provenance."""

import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional, TypedDict


class GitInfo(TypedDict):
    commit_hash: Optional[str]
    commit_url: Optional[str]


def _run_git(args: list[str]) -> Optional[str]:
    try:
        repo_dir = Path(__file__).resolve().parent
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _remote_to_commit_url(remote_url: str, commit_hash: str) -> Optional[str]:
    # git@github.com:owner/repo.git -> https://github.com/owner/repo/commit/<hash>
    m = re.match(r"^git@([^:]+):(.+?)(\.git)?$", remote_url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}/commit/{commit_hash}"
    # https://github.com/owner/repo.git -> https://github.com/owner/repo/commit/<hash>
    m = re.match(r"^(https?://[^/]+/.+?)(\.git)?$", remote_url)
    if m:
        return f"{m.group(1)}/commit/{commit_hash}"
    return None


@lru_cache(maxsize=1)
def get_git_info() -> GitInfo:
    """
    Returns the full commit hash of the running code and a browsable URL to it (based on the
    'origin' remote), or None for both if this isn't a git checkout or git isn't available.
    Cached for the lifetime of the process, since the running code can't change mid-process.
    """
    commit_hash = _run_git(["rev-parse", "HEAD"])
    if not commit_hash:
        return {"commit_hash": None, "commit_url": None}

    remote_url = _run_git(["config", "--get", "remote.origin.url"])
    commit_url = _remote_to_commit_url(remote_url, commit_hash) if remote_url else None

    return {"commit_hash": commit_hash, "commit_url": commit_url}

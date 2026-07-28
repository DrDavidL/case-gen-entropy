"""Build provenance, baked into the image at build time.

Exists because `deploy-aca.sh redeploy` silently no-op'd for four months: it
rebuilt the `:v1` tag and told Container Apps to run `:v1`, which was already
what it was running, so no new revision was created and the old image kept
serving. Nothing in the UI or API disagreed with the assumption that a deploy
had happened.

The fix for the deploy is a unique tag per build. The fix for *noticing* is
this: surface the commit and build time everywhere a human or a script can
read them.
"""

import os

UNKNOWN = "unknown"


def get_build_info() -> dict:
    """Build identity for this running image.

    Values come from Docker build args (see Dockerfile.backend). A local run
    without them reports "unknown", which is honest — it means nobody stamped
    this process, not that it is current.
    """
    return {
        "git_sha": os.getenv("GIT_SHA", UNKNOWN),
        "build_time": os.getenv("BUILD_TIME", UNKNOWN),
        "image_tag": os.getenv("IMAGE_TAG", UNKNOWN),
    }


def format_build_stamp(info: dict | None = None) -> str:
    """Single-line human-readable stamp, e.g. '39c3eaa · built 2026-07-28 17:26 UTC'."""
    info = info or get_build_info()
    sha = info.get("git_sha", UNKNOWN)
    built = info.get("build_time", UNKNOWN)
    if sha == UNKNOWN and built == UNKNOWN:
        return "build unknown (not stamped at image build)"
    return f"{sha} · built {built}"

"""Fail if a built package contains the owner's private files or lacks what `deal-scout` needs.

    uv build && uv run python scripts/check_dist.py [dist-folder]

Lists every member of dist/*.whl and dist/*.tar.gz. The repo is public and the owner's wishes, tokens and listings
sit next to the code, so a build must never carry criteria.md, .env, .state.json, inbox/, listings/, logs/, .venv/,
_site/ or IMG_* files. Standard library only.
"""

import sys
import tarfile
import zipfile
from pathlib import Path

FORBIDDEN_FILES = ("criteria.md", ".state.json")
FORBIDDEN_FOLDERS = ("inbox", "listings", "logs", ".venv", "_site")
WHEEL_MUST_HAVE = ("deal_scout/_domains/campervan/domain.json", "deal_scout/cli.py")


def forbidden(member: str) -> bool:
    """Whether an archive member is one of the private files or folders."""
    parts = member.strip("/").split("/")
    name = parts[-1]
    if name in FORBIDDEN_FILES or name.startswith("IMG_"):
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return any(part in FORBIDDEN_FOLDERS for part in parts)


def members(path: Path) -> list[str]:
    """List the member names of a wheel (zip) or a source distribution (tar.gz)."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as z:
            return z.namelist()
    with tarfile.open(path) as t:
        return t.getnames()


def problems(path: Path, names: list[str]) -> list[str]:
    """Return what is wrong with one archive's member list."""
    found = [f"{path.name}: contains {n}" for n in names if forbidden(n)]
    if path.suffix == ".whl":
        found += [f"{path.name}: lacks {need}" for need in WHEEL_MUST_HAVE if need not in names]
    return found


def main(argv: list[str]) -> int:
    """Check every built archive in the dist folder; print the verdict and return the exit code."""
    dist = Path(argv[1]) if len(argv) > 1 else Path("dist")
    archives = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    if not any(a.suffix == ".whl" for a in archives) or not any(a.suffix == ".gz" for a in archives):
        print(f"need a wheel and an sdist in {dist}/ (run: uv build)")
        return 1
    bad: list[str] = []
    for archive in archives:
        names = members(archive)
        print(f"{archive.name}: {len(names)} files")
        bad += problems(archive, names)
    for line in bad:
        print("FAIL", line)
    if not bad:
        print("OK: no private files in the build")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

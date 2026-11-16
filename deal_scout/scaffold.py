"""Set up a data folder: the inbox, listings and logs folders, a .env, and a copy of each shipped domain.

Used by `deal-scout init`. It only ever creates what is missing and never overwrites or deletes anything, so it is
safe to run again. Standard library only.
"""

import shutil
from pathlib import Path

from deal_scout.config import PACKAGE_DIR

FOLDERS = ("inbox", "listings", "logs")
PRIVATE = "criteria.md"  # the buyer's own wishes: never copied from a shipped domain, only made from the example
EXAMPLE = "criteria.example.md"


def env_example(package_dir: Path = PACKAGE_DIR) -> Path:
    """Return the .env template: the checkout's .env.example, or the copy shipped inside the installed package."""
    checkout = package_dir.parent / ".env.example"
    return checkout if checkout.is_file() else package_dir / "_env.example"


def init_home(home: Path, builtin: Path, template: Path) -> list[str]:
    """Create what is missing in the data folder `home`; return a line for each thing created."""
    made: list[str] = []
    for name in FOLDERS:
        if not (home / name).is_dir():
            (home / name).mkdir(parents=True)
            made.append(f"{name}/")
    env = home / ".env"
    if not env.exists() and template.is_file():
        shutil.copyfile(template, env)
        env.chmod(0o600)
        made.append(".env")
    for source in sorted(p for p in builtin.iterdir() if (p / "domain.json").is_file()) if builtin.is_dir() else []:
        target = home / "domains" / source.name
        if not target.exists():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(PRIVATE, "__pycache__"))
            made.append(f"domains/{source.name}/")
        if (target / EXAMPLE).is_file() and not (target / PRIVATE).exists():
            shutil.copyfile(target / EXAMPLE, target / PRIVATE)
            made.append(f"domains/{source.name}/{PRIVATE}")
    return made

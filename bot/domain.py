"""A domain is the kind of thing being bought: a used campervan today, a convertible or a TV tomorrow.

Everything specific to it lives in domains/<name>/ and the code only reads it:

    domain.json        item name, seller language, the spec list, the categories, market grouping
    instructions.md    domain rules added to the analysis prompt (how to score, what to ask the seller)
    criteria.md        the buyer's own wishes (git-ignored; start from criteria.example.md)
    checklist.md       what to check on any listing of this kind
    card-template.md   the layout of a listing card
    <reference files>  knowledge the reviewer reads every run (listed in domain.json)

The active domain is DOMAIN in .env (default: campervan). Standard library only.
"""
import json
from collections.abc import Iterable
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent


class DomainError(Exception):
    """The requested domain is missing or incomplete."""


class Domain:
    """One domain's settings and rule files, read from its directory."""

    def __init__(self, directory: Path | str):
        """Read domain.json and instructions.md from the directory."""
        self.dir = Path(directory)
        self.name = self.dir.name
        self.rel = f"domains/{self.name}"  # how Claude Code finds the files from the project folder
        path = self.dir / "domain.json"
        if not path.exists():
            raise DomainError(f"no {path}: see domains/README.md")
        cfg = json.loads(path.read_text(encoding="utf-8"))
        self.item = cfg["item"]                                  # "used campervan"
        self.unit = cfg.get("unit", "item")                       # "van": "which van it is about"
        self.seller_language = cfg.get("seller_language", "English")
        self.title_example = cfg.get("title_example", "")
        self.specs = list(cfg["specs"])
        self.categories = {k: v for k, v in (cfg.get("categories") or {}).items()}
        self.reference_files = dict(cfg.get("reference_files") or {})  # file name -> what it is
        self.market = dict(cfg.get("market") or {})
        instructions = self.dir / "instructions.md"
        self.instructions = instructions.read_text(encoding="utf-8").strip() if instructions.exists() else ""

    def has_criteria(self) -> bool:
        """Whether the buyer has written criteria.md (it is git-ignored, so it may be missing)."""
        return (self.dir / "criteria.md").exists()


def load(name: str, roots: Iterable[Path | str] | None = None) -> Domain:
    """Load the domain called `name` from each root's domains/ folder in turn (data folder first, then the code's)."""
    for root in list(roots or []) + [CODE_ROOT]:
        if (Path(root) / "domains" / name / "domain.json").exists():
            return Domain(Path(root) / "domains" / name)
    raise DomainError(f"domain {name!r} not found in domains/")

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deal_scout.config import ROOT


class DomainError(Exception):
    """The requested domain is missing or incomplete."""


@dataclass(frozen=True)
class Domain:
    """One domain's settings and rule files, read from its directory."""

    dir: Path
    name: str
    rel: str  # how Claude Code finds the files from the project folder
    item: str  # "used campervan"
    unit: str  # "van": "which van it is about"
    seller_language: str
    title_example: str
    specs: list[str]
    categories: dict[str, dict[str, Any]]
    reference_files: dict[str, str]  # file name -> what it is
    market: dict[str, Any]
    instructions: str

    @classmethod
    def from_dir(cls, directory: Path | str) -> "Domain":
        """Read domain.json and instructions.md from the directory."""
        folder = Path(directory)
        name = folder.name
        path = folder / "domain.json"
        if not path.exists():
            raise DomainError(f"no {path}: see domains/README.md")
        cfg = json.loads(path.read_text(encoding="utf-8"))
        instructions = folder / "instructions.md"
        return cls(
            dir=folder,
            name=name,
            rel=f"domains/{name}",
            item=cfg["item"],
            unit=cfg.get("unit", "item"),
            seller_language=cfg.get("seller_language", "English"),
            title_example=cfg.get("title_example", ""),
            specs=list(cfg["specs"]),
            categories={k: v for k, v in (cfg.get("categories") or {}).items()},
            reference_files=dict(cfg.get("reference_files") or {}),
            market=dict(cfg.get("market") or {}),
            instructions=instructions.read_text(encoding="utf-8").strip() if instructions.exists() else "",
        )

    def has_criteria(self) -> bool:
        """Whether the buyer has written criteria.md (it is git-ignored, so it may be missing)."""
        return (self.dir / "criteria.md").exists()


def load(name: str, roots: Iterable[Path | str] | None = None) -> Domain:
    """Load the domain called `name` from each root's domains/ folder in turn (data folder first, then the code's)."""
    for root in list(roots or []) + [ROOT]:
        if (Path(root) / "domains" / name / "domain.json").exists():
            return Domain.from_dir(Path(root) / "domains" / name)
    raise DomainError(f"domain {name!r} not found in domains/")

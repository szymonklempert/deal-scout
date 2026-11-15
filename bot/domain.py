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
import os
from pathlib import Path

DEFAULT = "campervan"
CODE_ROOT = Path(__file__).resolve().parent.parent


class DomainError(Exception):
    pass


class Domain:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.name = self.dir.name
        self.rel = "domains/%s" % self.name  # how Claude Code finds the files from the project folder
        path = self.dir / "domain.json"
        if not path.exists():
            raise DomainError("no %s: see domains/README.md" % path)
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

    def has_criteria(self):
        return (self.dir / "criteria.md").exists()


def load(name=None, roots=None):
    """The domain called `name` (default: DOMAIN from the environment, else campervan), looked up in each root's
    domains/ folder in turn (the data folder first, then the code's own)."""
    name = name or os.environ.get("DOMAIN") or DEFAULT
    for root in list(roots or []) + [CODE_ROOT]:
        if (Path(root) / "domains" / name / "domain.json").exists():
            return Domain(Path(root) / "domains" / name)
    raise DomainError("domain %r not found in domains/" % name)

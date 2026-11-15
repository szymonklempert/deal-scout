"""The inbox folder: one inbox/<NNN>/ per listing, with its photos, notes and links."""
import time
from collections.abc import Sequence
from pathlib import Path


class Inbox:
    """inbox/<NNN>/ holds photo_01.jpg..., notes.txt, links.txt and meta.json for one listing."""

    def __init__(self, root: Path) -> None:
        """Open (and create if needed) the inbox folder under the project root."""
        self.dir = root / "inbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def next_number(self) -> int:
        """Return the number the next new listing gets."""
        nums = [int(p.name) for p in self.dir.iterdir() if p.is_dir() and p.name.isdigit()]
        return (max(nums) + 1) if nums else 1

    def folder(self, n: int) -> Path:
        """Return listing n's folder, creating it if needed."""
        path = self.dir / f"{n:03d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_photo(self, n: int, data: bytes, ext: str = ".jpg") -> Path:
        """Save an image as the listing's next photo_NN file and return its path."""
        folder = self.folder(n)
        count = len(list(folder.glob("photo_*")))
        path = folder / f"photo_{count + 1:02d}{ext}"
        path.write_bytes(data)
        return path

    def add_note(self, n: int, text: str, when: float) -> None:
        """Append a timestamped note to the listing's notes.txt."""
        with open(self.folder(n) / "notes.txt", "a", encoding="utf-8") as f:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(when))
            f.write(f"[{stamp}] {text}\n")

    def add_links(self, n: int, links: Sequence[str]) -> list[str]:
        """Append the links not seen before to links.txt and return the new ones."""
        path = self.folder(n) / "links.txt"
        known = set(path.read_text(encoding="utf-8").split()) if path.exists() else set()
        new = [u for u in links if u not in known]
        if new:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(new) + "\n")
        return new

    def summary(self, n: int) -> tuple[int, list[str]]:
        """Return the listing's photo count and its saved links."""
        folder = self.dir / f"{n:03d}"
        photos = len(list(folder.glob("photo_*")))
        links = (folder / "links.txt").read_text(encoding="utf-8").split() if (folder / "links.txt").exists() else []
        return photos, links

    def latest(self, count: int = 10) -> list[int]:
        """Return the numbers of the most recent listings, oldest first."""
        nums = sorted(int(p.name) for p in self.dir.iterdir() if p.is_dir() and p.name.isdigit())
        return nums[-count:]

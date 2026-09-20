"""Vault location and path-safety.

Every path that arrives from a tool call is untrusted: the model can propose
anything, including `../../.ssh/id_rsa`. All access funnels through
`VaultPaths.resolve`, which refuses anything that escapes the vault root.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class VaultPathError(ValueError):
    """Raised when a requested path escapes the vault or does not exist."""


@dataclass(frozen=True)
class VaultPaths:
    root: Path
    events_folder: str = "Events"
    daily_folder: str = "Daily"
    inbox_note: str = "Inbox.md"

    @classmethod
    def from_env(cls) -> VaultPaths:
        raw = os.environ.get("OBSIDIAN_VAULT_PATH")
        if not raw:
            raise VaultPathError(
                "OBSIDIAN_VAULT_PATH is not set. Copy .env.example to .env and point "
                "it at your vault (or at ./fixtures/vault to develop against fixtures)."
            )
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise VaultPathError(f"OBSIDIAN_VAULT_PATH is not a directory: {root}")

        # Obsidian already records where daily notes live. Prefer its answer over
        # a guessed default, so a vault using "Schedules" works without config.
        from vaultlib.dayplanner import daily_notes_folder

        daily = (
            os.environ.get("OBSIDIAN_DAILY_FOLDER")
            or daily_notes_folder(root)
            or "Daily"
        )
        return cls(
            root=root,
            events_folder=os.environ.get("OBSIDIAN_EVENTS_FOLDER", "Events"),
            daily_folder=daily,
            inbox_note=os.environ.get("OBSIDIAN_INBOX_NOTE", "Inbox.md"),
        )

    def resolve(self, relative: str, *, must_exist: bool = True) -> Path:
        """Resolve a vault-relative path, refusing anything outside the vault.

        Symlinks are resolved before the containment check, so a symlink inside
        the vault pointing outside it is rejected too.
        """
        candidate = (self.root / relative).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise VaultPathError(f"Could not resolve path {relative!r}: {exc}") from exc

        if resolved != self.root and self.root not in resolved.parents:
            raise VaultPathError(
                f"Path {relative!r} resolves outside the vault and was refused."
            )
        if must_exist and not resolved.exists():
            raise VaultPathError(f"No such note in the vault: {relative!r}")
        return resolved

    def relative(self, path: Path) -> str:
        """Vault-relative POSIX string, the form all tools speak in."""
        return path.resolve().relative_to(self.root).as_posix()

    def markdown_files(self) -> list[Path]:
        """Every real note in the vault.

        Skips Obsidian's config folder and the templates folder: a template's
        placeholder tasks and headings are not things the user actually has to do,
        and surfacing them as real content is pure noise to the model.
        """
        skip = {".obsidian"}
        if templates := self.templates_folder:
            skip.add(templates)
        return sorted(
            p
            for p in self.root.rglob("*.md")
            if not (skip & set(p.parts)) and not p.name.startswith(".")
        )

    @property
    def templates_folder(self) -> str | None:
        """The templates folder, per Obsidian's own config."""
        try:
            data = json.loads(
                (self.root / ".obsidian" / "templates.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        folder = data.get("folder")
        return folder.strip("/") if isinstance(folder, str) and folder.strip("/") else None

    @property
    def events_dir(self) -> Path:
        return self.root / self.events_folder

    @property
    def daily_dir(self) -> Path:
        return self.root / self.daily_folder

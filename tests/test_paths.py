import pytest

from vaultlib.paths import VaultPathError, VaultPaths


def test_resolves_note_inside_vault(vault):
    resolved = vault.resolve("Inbox.md")
    assert resolved.name == "Inbox.md"
    assert vault.relative(resolved) == "Inbox.md"


def test_resolves_nested_note(vault):
    assert vault.relative(vault.resolve("Projects/Checkout Redesign.md")) == (
        "Projects/Checkout Redesign.md"
    )


@pytest.mark.parametrize(
    "escape",
    [
        "../../../etc/passwd",
        "../../.ssh/id_rsa",
        "Projects/../../../../etc/hosts",
        "/etc/passwd",
    ],
)
def test_refuses_paths_escaping_the_vault(vault, escape):
    """The model can propose any path it likes; containment is enforced here."""
    with pytest.raises(VaultPathError, match="outside the vault"):
        vault.resolve(escape, must_exist=False)


def test_symlink_out_of_vault_is_refused(vault, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("secret")
    link = vault.root / "escape-hatch.md"
    link.symlink_to(outside)
    try:
        with pytest.raises(VaultPathError, match="outside the vault"):
            vault.resolve("escape-hatch.md")
    finally:
        link.unlink()


def test_missing_note_raises(vault):
    with pytest.raises(VaultPathError, match="No such note"):
        vault.resolve("Does Not Exist.md")


def test_missing_note_allowed_when_not_required(vault):
    assert vault.resolve("New Note.md", must_exist=False).name == "New Note.md"


def test_markdown_files_skips_obsidian_config(vault):
    files = vault.markdown_files()
    assert files, "fixture vault should contain notes"
    assert all(".obsidian" not in path.parts for path in files)
    assert all(path.suffix == ".md" for path in files)


def test_templates_folder_excluded_from_scans(tmp_path):
    """A template's placeholder tasks are not real work; they must not surface."""
    root = tmp_path / "v"
    (root / ".obsidian").mkdir(parents=True)
    (root / "Templates").mkdir()
    (root / "Notes").mkdir()
    (root / ".obsidian" / "templates.json").write_text('{"folder": "Templates"}')
    (root / "Templates" / "Daily.md").write_text("- [ ] placeholder\n")
    (root / "Notes" / "real.md").write_text("- [ ] actual work\n")

    paths = VaultPaths(root=root)
    assert paths.templates_folder == "Templates"
    found = [paths.relative(p) for p in paths.markdown_files()]
    assert found == ["Notes/real.md"]


def test_no_templates_config_scans_everything(tmp_path):
    root = tmp_path / "v"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes" / "real.md").write_text("x")
    paths = VaultPaths(root=root)
    assert paths.templates_folder is None
    assert len(paths.markdown_files()) == 1


def test_from_env_requires_the_variable(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    with pytest.raises(VaultPathError, match="OBSIDIAN_VAULT_PATH is not set"):
        VaultPaths.from_env()


def test_from_env_rejects_non_directory(monkeypatch, tmp_path):
    file_path = tmp_path / "not-a-vault.txt"
    file_path.write_text("x")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(file_path))
    with pytest.raises(VaultPathError, match="not a directory"):
        VaultPaths.from_env()

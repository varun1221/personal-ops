import pytest

from vaultlib.paths import VaultPaths

FIXTURE_VAULT = "fixtures/vault"


@pytest.fixture
def vault(tmp_path_factory) -> VaultPaths:
    """The fixture vault. Tests never touch the real one."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / FIXTURE_VAULT
    return VaultPaths(root=root.resolve())


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Every test gets its own history database. The real one is never touched.

    Set in the environment rather than patched, because the MCP servers under
    test are subprocesses and inherit it from there.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("OPS_STATE_DIR", str(state))
    return state

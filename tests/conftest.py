import pytest

from vaultlib.paths import VaultPaths

FIXTURE_VAULT = "fixtures/vault"


@pytest.fixture
def vault(tmp_path_factory) -> VaultPaths:
    """The fixture vault. Tests never touch the real one."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / FIXTURE_VAULT
    return VaultPaths(root=root.resolve())

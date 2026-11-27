import tomllib
from pathlib import Path

from opendefence_integration_matrix import __version__


def test_version_matches_bumpversion() -> None:
    config = tomllib.loads((Path(__file__).parents[1] / ".bumpversion.toml").read_text())
    assert __version__ == config["tool"]["bumpversion"]["current_version"]

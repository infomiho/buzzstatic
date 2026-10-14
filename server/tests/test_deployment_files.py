import re
from pathlib import Path

import pytest

from server.environment import ENVIRONMENT_BY_NAME

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLI_VARIABLES = {"BUZZ_SERVER", "BUZZ_TOKEN"}
BUZZ_VARIABLE = re.compile(r"\bBUZZ_[A-Z0-9_]+\b")
SHIPPED_FILES = sorted(
    [
        *REPOSITORY_ROOT.glob("docker-compose*.yml"),
        *(REPOSITORY_ROOT / "server").glob("docker-compose*.yml"),
        REPOSITORY_ROOT / "server" / ".env.example",
    ]
)


def relative(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


@pytest.mark.parametrize("path", SHIPPED_FILES, ids=relative)
def test_shipped_files_only_name_variables_the_server_reads(path):
    names = set(BUZZ_VARIABLE.findall(path.read_text()))
    unknown = names - set(ENVIRONMENT_BY_NAME) - CLI_VARIABLES
    assert not unknown, f"{path.name} names variables the server does not read: {sorted(unknown)}"

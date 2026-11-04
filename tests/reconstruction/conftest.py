import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "packages" / "reconstruction-core" / "src", ROOT / "pipelines", Path(__file__).parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory) -> Path:
    import make_fixture

    out = tmp_path_factory.mktemp("rec001_fixture")
    make_fixture.build(out)
    return out

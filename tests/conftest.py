from pathlib import Path

import pytest

from eval.corpus import build_corpus


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> dict[str, Path]:
    return build_corpus(tmp_path_factory.mktemp("corpus"))

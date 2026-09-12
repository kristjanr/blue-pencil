import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

import pytest
import synthetic


@pytest.fixture
def world():
    """A built synthetic series: graph plus profile."""
    graph, profile = synthetic.build()
    yield graph, profile
    graph.close()

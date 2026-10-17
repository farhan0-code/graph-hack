from collections.abc import Iterator

import pytest

from canon_graph.falkordb import FalkorClient
from canon_graph.ids import Namespace
from canon_graph.ingest import GraphWriter


@pytest.fixture(scope="session")
def falkordb() -> FalkorClient:
    client = FalkorClient.from_env()
    if not client.healthy():
        pytest.skip(f"FalkorDB not reachable at {client.base_url}; run `make falkor-up`")
    return client


@pytest.fixture
def verify_writer(falkordb: FalkorClient) -> Iterator[GraphWriter]:
    writer = GraphWriter(falkordb, Namespace.VERIFY)
    writer.purge_namespace()
    yield writer
    writer.purge_namespace()

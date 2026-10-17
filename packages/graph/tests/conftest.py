from collections.abc import Iterator

import pytest

from canon_graph.hydra import FalkorClient
from canon_graph.ids import Namespace
from canon_graph.ingest import GraphWriter


@pytest.fixture(scope="session")
def hydra() -> FalkorClient:
    client = FalkorClient.from_env()
    if not client.healthy():
        pytest.skip(f"FalkorDB not reachable at {client.base_url}; run `make falkordb-up`")
    return client


@pytest.fixture
def verify_writer(hydra: FalkorClient) -> Iterator[GraphWriter]:
    writer = GraphWriter(hydra, Namespace.VERIFY)
    writer.purge_namespace()
    yield writer
    writer.purge_namespace()

from dataclasses import asdict, dataclass

from canon_graph.falkordb import FalkorClient, FalkorMetrics, NamedQuery, Param, QueryResult

ENGINE = "FalkorDB"


@dataclass(frozen=True, slots=True)
class FalkorQueryCard:
    engine: str
    operation: str
    query_name: str
    cypher: str
    parameters: dict[str, Param]
    result_count: int
    client_round_trip_ms: float
    query_id: str
    engine_rows_duration_us: float | None
    engine_ops_observed: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def card_for(result: QueryResult, params: dict[str, Param] | None) -> FalkorQueryCard:
    return FalkorQueryCard(
        engine=ENGINE,
        operation=result.query.operation,
        query_name=result.query.name,
        cypher=result.query.cypher,
        parameters=dict(params or {}),
        result_count=len(result),
        client_round_trip_ms=round(result.client_ms, 3),
        query_id=result.query_id,
        engine_rows_duration_us=None,
        engine_ops_observed=None,
    )


def timed_card(
    client: FalkorClient, query: NamedQuery, params: dict[str, Param] | None = None
) -> tuple[QueryResult, FalkorQueryCard]:
    before = client.metrics()
    result = client.run(query, params)
    after = client.metrics()
    card = card_for(result, params)
    return result, FalkorQueryCard(
        engine=card.engine,
        operation=card.operation,
        query_name=card.query_name,
        cypher=card.cypher,
        parameters=card.parameters,
        result_count=card.result_count,
        client_round_trip_ms=card.client_round_trip_ms,
        query_id=card.query_id,
        engine_rows_duration_us=engine_delta_us(before, after),
        engine_ops_observed=after.query_rows_duration_count - before.query_rows_duration_count,
    )


def engine_delta_us(before: FalkorMetrics, after: FalkorMetrics) -> float:
    return round(after.query_rows_duration_us_sum - before.query_rows_duration_us_sum, 1)

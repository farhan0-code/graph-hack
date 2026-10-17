import os
import time
from dataclasses import dataclass
from itertools import count
from uuid import uuid4

from falkordb import FalkorDB

Cell = int | float | str | bool | None | list["Cell"]
Row = dict[str, Cell]
Param = int | float | str | bool | list["Param"] | dict[str, "Param"]

DEFAULT_HOST = os.environ.get("FALKOR_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("FALKOR_PORT", "6379"))
DEFAULT_GRAPH = os.environ.get("FALKOR_GRAPH", "default")
TRANSIENT_RETRY_SECONDS = 60.0
WRITER_PROBE_ID = 4611686018427387900


class FalkorError(RuntimeError):
    def __init__(self, code: str, message: str, query_name: str) -> None:
        super().__init__(f"{query_name}: {code}: {message}")
        self.code = code
        self.message = message
        self.query_name = query_name


class FalkorUnreachableError(FalkorError):
    def __init__(self, base_url: str, reason: str) -> None:
        super().__init__("unreachable", f"{base_url}: {reason}", "connect")


@dataclass(frozen=True, slots=True)
class NamedQuery:
    name: str
    operation: str
    cypher: str


@dataclass(frozen=True, slots=True)
class QueryResult:
    query: NamedQuery
    query_id: str
    columns: tuple[str, ...]
    rows: tuple[Row, ...]
    client_ms: float
    read_epoch: int | None

    def __len__(self) -> int:
        return len(self.rows)

    def column(self, name: str) -> list[Cell]:
        return [row[name] for row in self.rows]

    def scalar(self) -> Cell:
        if not self.rows:
            return None
        first = self.rows[0]
        return first[self.columns[0]]


@dataclass(frozen=True, slots=True)
class FalkorMetrics:
    query_rows_duration_us_sum: float
    query_rows_duration_count: int
    read_op_seconds_sum: float
    read_op_count: int
    write_op_seconds_sum: float
    write_op_count: int
    query_started: int
    query_completed: int
    query_failed: int
    cache_entries: int
    cache_resident_bytes: int


def _to_cell(value: object) -> Cell:
    if value is None:
        return None
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_to_cell(item) for item in value]
    return str(value)


class FalkorClient:
    """
    FalkorDB client wrapper providing a consistent query and metric interface.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        graph: str = DEFAULT_GRAPH,
        timeout_seconds: float = 60.0,
        # Legacy kwargs kept for call-site compatibility
        base_url: str = "",
        token: str = "",
        metrics_url: str = "",
        cell_id: str = "",
        namespace: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self.graph = graph
        self.timeout_seconds = timeout_seconds
        self.base_url = f"falkordb://{host}:{port}"
        self._session = uuid4().hex[:12]
        self._sequence = count(1)
        self._db: FalkorDB | None = None
        self._g = None
        self._metrics = _MutableMetrics()

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "FalkorClient":
        host = os.environ.get("FALKOR_HOST", DEFAULT_HOST)
        port = int(os.environ.get("FALKOR_PORT", str(DEFAULT_PORT)))
        graph = os.environ.get("FALKOR_GRAPH", DEFAULT_GRAPH)
        return cls(host=host, port=port, graph=graph)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        if self._db is None:
            self._db = FalkorDB(host=self._host, port=self._port)
            self._g = self._db.select_graph(self.graph)

    def next_query_id(self, query_name: str) -> str:
        return f"canon-{self._session}-{next(self._sequence)}-{query_name}"

    # ------------------------------------------------------------------
    # Public query interface
    # ------------------------------------------------------------------

    def run(
        self,
        query: NamedQuery,
        params: dict[str, Param] | None = None,
        consistency: str | None = None,
    ) -> QueryResult:
        deadline = time.monotonic() + TRANSIENT_RETRY_SECONDS
        while True:
            try:
                return self._run_once(query, params)
            except FalkorError as error:
                if time.monotonic() >= deadline:
                    raise
                if error.code != "unreachable":
                    raise
                time.sleep(0.5)

    def _run_once(
        self,
        query: NamedQuery,
        params: dict[str, Param] | None,
    ) -> QueryResult:
        self._connect()
        started = time.perf_counter()
        try:
            self._metrics.started += 1
            result = self._g.query(query.cypher, params or {})
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._metrics.completed += 1
        except Exception as exc:
            self._metrics.failed += 1
            raise FalkorError("query_error", str(exc), query.name) from exc

        header: list[str] = [col[1] for col in (result.header or [])]
        rows: tuple[Row, ...] = tuple(
            {header[i]: _to_cell(cell) for i, cell in enumerate(record)}
            for record in (result.result_set or [])
        )
        columns = tuple(header)
        qid = self.next_query_id(query.name)
        self._metrics.duration_us_sum += elapsed_ms * 1000
        self._metrics.duration_count += 1
        return QueryResult(
            query=query,
            query_id=qid,
            columns=columns,
            rows=rows,
            client_ms=elapsed_ms,
            read_epoch=None,
        )

    # ------------------------------------------------------------------
    # Health / readiness
    # ------------------------------------------------------------------

    def healthy(self) -> bool:
        try:
            self._connect()
            self._g.query("RETURN 1")
            return True
        except Exception:
            return False

    def wait_until_healthy(self, timeout_seconds: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.healthy():
                return True
            time.sleep(0.5)
        return False

    def wait_until_writable(self, timeout_seconds: float = 120.0) -> bool:
        if not self.wait_until_healthy(timeout_seconds):
            return False
        probe = NamedQuery(
            "writer_probe",
            "CREATE writer probe",
            "CREATE (a:WriterProbe {id: $id})-[:PROBES]->(b:WriterProbe {id: $other})",
        )
        cleanup = NamedQuery(
            "writer_probe_cleanup",
            "DETACH DELETE writer probe",
            "MATCH (n:WriterProbe {id: $id}) DETACH DELETE n",
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                self.run(probe, {"id": WRITER_PROBE_ID, "other": WRITER_PROBE_ID + 1})
            except FalkorError:
                time.sleep(0.5)
                continue
            for node_id in (WRITER_PROBE_ID, WRITER_PROBE_ID + 1):
                self.run(cleanup, {"id": node_id})
            return True
        return False

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics(self) -> "FalkorMetrics":
        m = self._metrics
        return FalkorMetrics(
            query_rows_duration_us_sum=m.duration_us_sum,
            query_rows_duration_count=m.duration_count,
            read_op_seconds_sum=0.0,
            read_op_count=0,
            write_op_seconds_sum=0.0,
            write_op_count=0,
            query_started=m.started,
            query_completed=m.completed,
            query_failed=m.failed,
            cache_entries=0,
            cache_resident_bytes=0,
        )


class _MutableMetrics:
    """Simple in-process accumulator."""

    __slots__ = (
        "duration_us_sum",
        "duration_count",
        "started",
        "completed",
        "failed",
    )

    def __init__(self) -> None:
        self.duration_us_sum: float = 0.0
        self.duration_count: int = 0
        self.started: int = 0
        self.completed: int = 0
        self.failed: int = 0

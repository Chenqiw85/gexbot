from __future__ import annotations

from dataclasses import dataclass
from itertools import count

from app.domain.gex import AnalysisInput, AnalysisResult, StrikeGexProxyRow, analyze_gex_proxy


@dataclass(frozen=True)
class StoredAnalysisRun:
    id: int
    input_data: AnalysisInput
    result: AnalysisResult
    persist_strike_rows: bool

    @property
    def model_inputs(self):
        return self.result.model_inputs

    @property
    def net_gex_proxy(self) -> float:
        return self.result.net_gex_proxy


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self._ids = count(1)
        self.analysis_runs: dict[int, StoredAnalysisRun] = {}
        self.strike_rows_by_run: dict[int, list[StrikeGexProxyRow]] = {}

    def save_analysis_run(self, input_data: AnalysisInput, *, persist_strike_rows: bool) -> int:
        run_id = next(self._ids)
        result = analyze_gex_proxy(input_data)
        stored = StoredAnalysisRun(
            id=run_id,
            input_data=input_data,
            result=result,
            persist_strike_rows=persist_strike_rows,
        )
        self.analysis_runs[run_id] = stored
        if persist_strike_rows:
            self.strike_rows_by_run[run_id] = list(result.rows.values())
        return run_id

from __future__ import annotations

from app.domain.gex import AnalysisResult, analyze_gex_proxy
from app.services.persistence import StoredAnalysisRun


class UnsupportedModelVersion(ValueError):
    pass


SUPPORTED_RECOMPUTE_MODEL_VERSIONS = frozenset({"black_scholes_v1.0.0"})


def recompute_from_record(record: StoredAnalysisRun) -> AnalysisResult:
    model_version = record.input_data.model_version
    if model_version not in SUPPORTED_RECOMPUTE_MODEL_VERSIONS:
        raise UnsupportedModelVersion(f"unsupported model_version {model_version}")
    recomputed = analyze_gex_proxy(record.input_data)
    warnings = tuple(sorted(set(recomputed.warnings + record.result.warnings)))
    if warnings == recomputed.warnings:
        return recomputed
    return AnalysisResult(
        rows=recomputed.rows,
        net_gex_proxy=recomputed.net_gex_proxy,
        zero_gamma_proxy=recomputed.zero_gamma_proxy,
        call_wall_proxy=recomputed.call_wall_proxy,
        put_wall_proxy=recomputed.put_wall_proxy,
        excluded_contract_count=recomputed.excluded_contract_count,
        warnings=warnings,
        model_inputs=recomputed.model_inputs,
    )

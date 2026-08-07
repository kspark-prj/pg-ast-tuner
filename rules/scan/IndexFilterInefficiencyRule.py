from typing import Optional

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class IndexFilterInefficiencyRule(BaseRule):
    RULE_ID = "RULE_SCAN_007"
    NAME = "IndexFilterInefficiencyRule"
    DESCRIPTION = (
        "Index Scan 시 Index Cond가 아닌 Index Filter로 인해 과도한 블록을 스캔하는지 진단하며, "
        "TableMetadata의 선행 컬럼 연속성(Prefix Match) 평가를 결합하여 구조적 원인을 분석합니다."
    )
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Index Scan", "Index Only Scan"]
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    MIN_TOTAL_SCANNED = 1000  # 오탐 방지를 위한 최소 스캔 건수 임계값
    MIN_ROWS_REMOVED = 500
    INEFFICIENCY_RATIO = 0.8

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type")
        table_name = node.get("Relation Name")
        index_name = node.get("Index Name")

        rows_removed = node.get("Rows Removed by Index Recheck", 0) + node.get("Rows Removed by Filter", 0)
        actual_rows = node.get("Actual Rows", 0)
        total_scanned = actual_rows + rows_removed

        # 1. 유효성 검사 및 소량 스캔 예외 처리 (Early Stop 보호)
        if not table_name or total_scanned == 0:
            return recommendations

        if (total_scanned < self.MIN_TOTAL_SCANNED) or (rows_removed < self.MIN_ROWS_REMOVED):
            return recommendations

        removal_ratio = rows_removed / total_scanned
        if removal_ratio < self.INEFFICIENCY_RATIO:
            return recommendations

        # 2. [메타데이터 연동] 테이블 인덱스 정보 및 쿼리 컬럼 분석
        metadata_provider = getattr(context, "metadata_provider", None)
        query_cols = []
        if metadata_provider:
            from core.parser import PGPlanAnalyzer
            where_cols, _, _, _ = PGPlanAnalyzer.extract_columns_via_ast_ordered(
                context.raw_query, table_name
            )
            query_cols = [
                c for c in where_cols
                if isinstance(c, str) and not c.startswith("'") and not c.startswith('"')
            ]

        meta_diag = self._evaluate_index_metadata(context, table_name, index_name, query_cols)

        is_backward = node.get("Scan Direction") == "Backward"
        has_parent_limit = self._is_under_limit_node(context, node)

        # 3. 상황별 맞춤 진단 메시지 및 DDL 생성
        if has_parent_limit or is_backward:
            title = f"'{table_name}' LIMIT 정렬 인덱스 스캔 시 필터 비효율 감지"
            severity = "INFO"
            priority = 3
        else:
            title = f"'{table_name}' 인덱스 필터링(Index Filter) 과다 발생"
            severity = "WARNING"
            priority = 2

        # 4. 메타데이터 분석 결과를 반영한 상세 원인(reason) 생성
        reason = self._build_reason_text(index_name, total_scanned, rows_removed, removal_ratio, meta_diag)
        recommendation, recommended_sql = self._build_action_guide(table_name, index_name, query_cols, meta_diag)

        recommendations.append(
            RecommendationModel(
                title=title,
                description=(
                    f"'{index_name}' 인덱스 스캔 중 총 {total_scanned:,}건 중 "
                    f"{rows_removed:,}건({removal_ratio * 100:.1f}%)이 Index Filter 조건에 의해 버려졌습니다."
                ),
                severity=severity,
                priority=priority,
                reason=reason,
                recommendation=recommendation,
                recommended_sql=recommended_sql,
                plan_node=node_type,
                estimated_gain="High" if severity == "WARNING" else "Medium",
                false_positive_risk="Low",
            )
        )

        return recommendations

    def _evaluate_index_metadata(self, context: RuleContext, table_name: str, current_idx_name: str | None, query_cols: list[str]) -> dict:
        """
        TableMetadata를 조회하여 현재 사용 중인 인덱스의 Prefix Matching 여부와
        더 나은 대안 인덱스가 존재하는지 평가합니다.
        """
        result = {
            "has_metadata": False,
            "current_index_cols": [],
            "has_skipped_prefix": False,
            "consecutive_matches": 0,
            "better_alternative": None,
        }

        metadata_provider = getattr(context, "metadata_provider", None)
        if not metadata_provider or not query_cols:
            return result

        try:
            table_meta = metadata_provider.get_table_metadata(table_name)
            if not table_meta:
                return result

            result["has_metadata"] = True

            # 1. 현재 사용 중인 인덱스의 구조적 일치 여부 평가
            for idx in table_meta.indices:
                if idx.index_name == current_idx_name:
                    eval_res = idx.evaluate_match(query_cols)
                    result["current_index_cols"] = idx.columns
                    result["has_skipped_prefix"] = eval_res["has_skipped_prefix"]
                    result["consecutive_matches"] = eval_res["consecutive_prefix_matches"]
                    break

            # 2. 더 높은 점수를 가진 대안 인덱스가 존재하는지 탐색
            best_idx, best_eval = table_meta.find_best_index_with_score(query_cols)
            if best_idx and best_idx.index_name != current_idx_name:
                if best_eval["consecutive_prefix_matches"] > result["consecutive_matches"]:
                    result["better_alternative"] = best_idx.index_name

        except Exception:
            pass

        return result

    def _build_reason_text(
        self,
        index_name: str,
        total: int,
        removed: int,
        ratio: float,
        meta_diag: dict,
    ) -> str:
        base_msg = (
            f"전체 스캔 대상({total:,}건) 중 {ratio * 100:.1f}%({removed:,}건)가 "
            "테이블/인덱스 블록을 읽은 뒤 Index Filter 후처리 단계에서 버려졌습니다."
        )

        if not meta_diag["has_metadata"]:
            return f"{base_msg} WHERE 조건절의 컬럼 구성이 인덱스 선두 컬럼과 일치하지 않아 액세스 범위(Index Cond)를 좁히지 못하고 있습니다."

        cols_str = ", ".join(meta_diag["current_index_cols"])
        if meta_diag["has_skipped_prefix"]:
            return (
                f"{base_msg} 현재 사용된 인덱스 '{index_name}'({cols_str})의 중간 컬럼이 "
                "WHERE 조건에서 누락(Skipped Prefix)되어, 후순위 컬럼들이 Index Cond로 "
                "작동하지 못하고 Index Filter로 다운그레이드되었습니다."
            )

        return (
            f"{base_msg} 현재 인덱스 '{index_name}'({cols_str})는 선두 컬럼 중 "
            f"{meta_diag['consecutive_matches']}개만 Index Cond로 매칭되어 탐색 범위가 지나치게 넓습니다."
        )

    def _build_action_guide(self, table_name: str, index_name: str, query_cols: list[str], meta_diag: dict) -> tuple[str, str]:
        # 대안 인덱스가 이미 테이블에 존재하는 경우
        if meta_diag.get("better_alternative"):
            alt_idx = meta_diag["better_alternative"]
            return (
                f"테이블에 이미 더 높은 일치도의 인덱스 '{alt_idx}'가 존재합니다. "
                "조건절 형태나 쿼리 결합 순서를 검토하여 옵티마이저가 해당 인덱스를 선택하도록 유도하십시오.",
                f"-- 옵티마이저 통계 최신화 권장\nANALYZE {table_name};",
            )

        # 중간 누락(Skipped Prefix)으로 인한 비효율인 경우
        cols_str = ", ".join(query_cols) if query_cols else "필터_조건_컬럼"
        idx_cols_name = "_".join(query_cols) if query_cols else "opt"
        if meta_diag.get("has_skipped_prefix"):
            return (
                f"인덱스 '{index_name}'의 중간 선두 컬럼이 누락되지 않도록 조회 조건을 보완하거나, "
                "현재 조회 조건에 맞춘 신규 복합 인덱스 생성을 권장합니다.",
                f"-- 선두 필터 최적화 복합 인덱스 생성\nCREATE INDEX CONCURRENTLY idx_{table_name}_{idx_cols_name} ON {table_name} ({cols_str});",
            )

        return (
            "WHERE 조건절 중 선택도(Selectivity)가 높은 필터 컬럼이 인덱스 선두에 오도록 인덱스 순서를 재구성하거나 복합 인덱스 설계를 검토하십시오.",
            f"-- 인덱스 재설계 생성\nCREATE INDEX CONCURRENTLY idx_{table_name}_{idx_cols_name} ON {table_name} ({cols_str});",
        )

    def _is_under_limit_node(self, context: RuleContext, node: dict) -> bool:
        parent = getattr(node, "parent", None) or getattr(context, "parent_node", None)
        while parent:
            if parent.get("Node Type") == "Limit":
                return True
            parent = getattr(parent, "parent", None)
        return False

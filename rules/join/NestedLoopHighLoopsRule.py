from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class NestedLoopHighLoopsRule(BaseRule):
    RULE_ID = "RULE_JOIN_004"
    NAME = "NestedLoopHighLoopsRule"
    DESCRIPTION = (
        "Nested Loop 조인 중 내부 테이블 탐색 횟수(Loops)가 과도하게 많아 비효율이 발생하는지 진단하며, "
        "TableMetadata와 연동하여 잦은 루프 속 인덱스 매칭 품질을 평가합니다."
    )
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Nested Loop"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Nested Loop"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Nested Loop")
        plans = node.get("Plans", [])

        if len(plans) >= 2:
            inner_node = plans[1]
            actual_loops = inner_node.get("Actual Loops", 0)

            if actual_loops >= 100000:
                inner_table = inner_node.get("Relation Name", "내부_테이블")
                inner_node_type = inner_node.get("Node Type", "")

                # [메타데이터 연동] 조인 컬럼 파싱
                from core.parser import PGPlanAnalyzer
                _, join_group_cols, _, _ = PGPlanAnalyzer.extract_columns_via_ast_ordered(
                    context.raw_query, inner_table
                )
                join_cols = [
                    c for c in join_group_cols
                    if isinstance(c, str) and not c.startswith("'") and not c.startswith('"')
                ]

                # Index Scan 중이더라도 연속 매칭이 불완전한지 평가
                meta_diag = self._evaluate_inner_loop_index(context, inner_table, join_cols)

                reason, recommendation, recommended_sql = self._build_action_guide(inner_table, actual_loops, inner_node_type, join_cols, meta_diag)

                recommendations.append(
                    RecommendationModel(
                        title="Nested Loop 내부 반복 수행 횟수 과다 및 스캔 비효율",
                        description=f"Nested Loop 조인 시 내부 드라이븐 테이블 탐색이 총 {actual_loops:,}회 반복 실행되었습니다.",
                        severity="HIGH",
                        priority=2,
                        reason=reason,
                        recommendation=recommendation,
                        recommended_sql=recommended_sql,
                        plan_node=node_type,
                        estimated_gain="High",
                        false_positive_risk="Medium",
                    )
                )

        return recommendations

    def _evaluate_inner_loop_index(self, context: RuleContext, table_name: str, join_cols: list[str]) -> dict:
        result = {"is_suboptimal_index": False, "matched_cols": 0, "best_index_name": None}
        metadata_provider = getattr(context, "metadata_provider", None)
        if not metadata_provider or not join_cols:
            return result

        try:
            table_meta = metadata_provider.get_table_metadata(table_name)
            if table_meta:
                best_idx, best_eval = table_meta.find_best_index_with_score(join_cols)
                if best_idx:
                    result["best_index_name"] = best_idx.index_name
                    result["matched_cols"] = best_eval["consecutive_prefix_matches"]
                    # 조인 컬럼 수보다 연속 매칭 수가 적다면 루프마다 불필요한 Index Filter 발생 중
                    if best_eval["consecutive_prefix_matches"] < len(join_cols):
                        result["is_suboptimal_index"] = True
        except Exception:
            pass

        return result

    def _build_action_guide(self, table_name: str, loops: int, node_type: str, join_cols: list[str], meta_diag: dict) -> tuple[str, str, str]:
        cols_str = ", ".join(join_cols) if join_cols else "조인_매핑_컬럼_전체"
        idx_cols_name = "_".join(join_cols) if join_cols else "loop"
        if meta_diag.get("is_suboptimal_index"):
            idx_name = meta_diag["best_index_name"]
            return (
                f"내부 테이블이 {loops:,}회 반복 탐색되는 동안, 사용된 인덱스 '{idx_name}'가 "
                "조인 조건의 일부 컬럼만 선행 매칭(Index Cond)하여 매 루프마다 필터 오버헤드가 발생하고 있습니다.",
                "모든 조인 조건을 선두에 포함하는 완벽한 복합 인덱스를 구성하여 반복 스캔 비용을 최적화하거나, Hash Join 방식으로 유도하십시오.",
                f"-- 조인 키 완전 매칭 인덱스 생성\nCREATE INDEX CONCURRENTLY idx_{table_name}_{idx_cols_name} ON {table_name} ({cols_str});",
            )

        return (
            f"내부 테이블({node_type})이 총 {loops:,}회 반복 실행되었습니다. "
            "인덱스가 존재하더라도 외부 집합 크기가 커서 과도한 루프가 발생하면 랜덤 I/O 비용이 급증합니다.",
            "외부 테이블의 필터링 조건을 강화하여 조인 대상 건수를 줄이거나, 대량 조인에 유리한 Hash Join 형태로 옵티마이저가 수립하도록 통계정보/쿼리를 재정비하십시오.",
            "-- 대량 집합 조인 최적화를 위한 통계 갱신\nANALYZE VERBOSE;",
        )

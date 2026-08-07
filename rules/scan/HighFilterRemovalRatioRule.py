from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class HighFilterRemovalRatioRule(BaseRule):
    RULE_ID = "RULE_SCAN_005"
    NAME = "HighFilterRemovalRatioRule"
    DESCRIPTION = "스캔 노드 이후 Filter 과정에서 버려지는 행의 비율을 분석하고, 메타데이터를 통해 인덱스 필터 누락 원인을 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Seq Scan", "Index Scan", "Bitmap Heap Scan"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Scan")
        table_name = node.get("Relation Name")
        current_idx_name = node.get("Index Name")

        rows_removed = node.get("Rows Removed by Filter", 0)
        actual_rows = node.get("Actual Rows", 0)

        if not table_name or rows_removed == 0:
            return recommendations

        total_scanned = actual_rows + rows_removed

        # 10,000건 이상 읽었는데 90% 이상이 Filter로 버려진 경우
        if total_scanned >= 10000 and (rows_removed / total_scanned) >= 0.9:
            removal_pct = (rows_removed / total_scanned) * 100

            # 메타데이터 연동: 쿼리 컬럼 추출 및 인덱스 평가
            meta_provider = getattr(context, "metadata_provider", None)
            query_cols = []
            if meta_provider:
                from core.parser import PGPlanAnalyzer
                where_cols, _, _, _ = PGPlanAnalyzer.extract_columns_via_ast_ordered(
                    context.raw_query, table_name
                )
                query_cols = [
                    c for c in where_cols
                    if isinstance(c, str) and not c.startswith("'") and not c.startswith('"')
                ]

            reason = "스캔 단계에서 필요한 행만 걸러내지 못해 디스크 I/O 및 CPU 리소스를 낭비하고 있습니다."
            recommendation = "Filter에 사용된 조건 컬럼을 인덱스 조건(Index Cond)으로 수용할 수 있도록 복합 인덱스를 재구성하십시오."
            cols_str = ", ".join(query_cols) if query_cols else "필터_컬럼"
            idx_cols_name = "_".join(query_cols) if query_cols else "opt"
            recommended_sql = f"-- 인덱스 최적화 필요\nCREATE INDEX CONCURRENTLY idx_{table_name}_{idx_cols_name} ON {table_name} ({cols_str});"

            if meta_provider and query_cols:
                try:
                    table_meta = meta_provider.get_table_metadata(table_name)
                    if table_meta:
                        best_idx, best_eval = table_meta.find_best_index_with_score(query_cols)

                        # 1. 대안 인덱스가 있는 경우
                        if best_idx and best_idx.index_name != current_idx_name:
                            reason = (
                                f"옵티마이저가 연속 매칭이 더 우수한 대안 인덱스('{best_idx.index_name}')를 회피하여 Filter 비효율이 발생했습니다."
                            )
                            recommendation = (
                                f"더 적합한 인덱스('{best_idx.index_name}')가 선택되도록 통계 정보를 갱신하거나 쿼리 힌트를 검토하십시오."
                            )
                            recommended_sql = f"ANALYZE VERBOSE {table_name};"

                        # 2. 현재 인덱스에서 Skipped Prefix가 발생한 경우
                        elif current_idx_name:
                            for idx in table_meta.indices:
                                if idx.index_name == current_idx_name:
                                    eval_res = idx.evaluate_match(query_cols)
                                    if eval_res.get("has_skipped_prefix"):
                                        reason = f"현재 인덱스('{current_idx_name}')의 중간 선두 컬럼이 누락(Skipped Prefix)되어, 나머지 컬럼이 Index Filter 처리되고 있습니다."
                                        recommendation = "누락된 선두 컬럼을 WHERE 조건에 추가하거나, 현재 조건에 맞는 완벽한 커버링(Full Cover) 인덱스를 신규 생성하십시오."
                                        recommended_sql = f"CREATE INDEX CONCURRENTLY idx_{table_name}_{idx_cols_name} ON {table_name} ({cols_str});"
                                    break
                except Exception:
                    pass

            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' 과도한 필터 탈락(Filter Removal) 감지",
                    description=f"'{table_name}' 테이블에서 읽어들인 데이터 중 {removal_pct:.1f}%({rows_removed:,}건)가 Filter 조건에 의해 후속 폐기되었습니다.",
                    severity="HIGH",
                    priority=1,
                    reason=reason,
                    recommendation=recommendation,
                    recommended_sql=recommended_sql,
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

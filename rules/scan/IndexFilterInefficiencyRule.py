from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class IndexFilterInefficiencyRule(BaseRule):
    RULE_ID = "RULE_SCAN_007"
    NAME = "IndexFilterInefficiencyRule"
    DESCRIPTION = "Index Scan 시 인덱스 조건(Index Cond)이 아닌 Index Filter로 인해 과도한 인덱스 블록을 스캔하는지 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Index Scan", "Index Only Scan"]
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

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

        if not table_name or total_scanned == 0:
            return recommendations

        # 인덱스 스캔 후 Filter 조건으로 80% 이상의 행이 제거된 경우
        if rows_removed > (total_scanned * 0.8):
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' 인덱스 필터링(Index Filter) 비효율 감지",
                    description=f"'{index_name}' 인덱스를 스캔하는 과정에서 총 {total_scanned:,}건 중 {rows_removed:,}건({(rows_removed / total_scanned) * 100:.1f}%)이 Filter 조건에 의해 버려졌습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="인덱스 선두 컬럼이 조건절에서 누락되어 Index Cond로 액세스 단계를 줄이지 못하고 Index Filter로 후처리되었습니다.",
                    recommendation="조회 조건의 결합 순서에 맞게 인덱스 컬럼 순서를 재조정하거나 복합 인덱스를 구성하십시오.",
                    recommended_sql=f"-- 예시: CREATE INDEX idx_new ON {table_name} (선두_필터_컬럼, 기존_컬럼);",
                    plan_node=node_type,
                    estimated_gain="Medium",
                    false_positive_risk="Low",
                )
            )

        return recommendations

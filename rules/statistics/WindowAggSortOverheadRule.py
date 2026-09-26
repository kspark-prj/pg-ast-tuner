from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class WindowAggSortOverheadRule(BaseRule):
    RULE_ID = "RULE_STAT_008"
    NAME = "WindowAggSortOverheadRule"
    DESCRIPTION = "WindowAgg(윈도우 연산) 수행 시 PARTITION BY / ORDER BY 절을 지원하는 인덱스가 없어 하위 노드에서 정렬 오버헤드가 발생하는지 진단합니다."
    CATEGORY = "STATISTICS"
    TARGET_NODE_TYPES = ["WindowAgg"]
    SUPPORTED_PG_VERSION = "all"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        if node.get("Node Type") != "WindowAgg":
            return False
        sub_plans = node.get("Plans", [])
        return any(sub.get("Node Type") in ("Sort", "Incremental Sort") for sub in sub_plans)

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        sub_plans = node.get("Plans", [])
        sort_node = next(
            (sub for sub in sub_plans if sub.get("Node Type") in ("Sort", "Incremental Sort")),
            None,
        )
        sort_type = sort_node.get("Node Type", "Sort") if sort_node else "Sort"

        recommendations.append(
            RecommendationModel(
                title="WindowAgg 윈도우 함수 정렬 오버헤드 감지",
                description=f"WindowAgg 절 연산을 수행하기 위해 하위 노드에서 {sort_type} 정렬이 수행되었습니다.",
                severity="WARNING",
                priority=2,
                reason="윈도우 함수(OVER (PARTITION BY ... ORDER BY ...))의 구성을 지원하는 인덱스가 부재하여 메모리/디스크 정렬이 강제되었습니다.",
                recommendation="PARTITION BY 컬럼을 선두로 하고 ORDER BY 컬럼을 후순위로 구성한 복합 인덱스를 신설하여 정렬을 제거(Index Ordering)하십시오.",
                recommended_sql="CREATE INDEX idx_window ON table_name (partition_col, order_col);",
                plan_node="WindowAgg",
                estimated_gain="Medium to High",
                false_positive_risk="Low",
            )
        )

        return recommendations

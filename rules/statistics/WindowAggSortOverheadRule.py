from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class WindowAggSortOverheadRule(BaseRule):
    RULE_ID = "RULE_STAT_008"
    NAME = "WindowAggSortOverheadRule"
    DESCRIPTION = "WindowAgg(윈도우 연산) 수행 시 정렬 수행 방식 및 디스크 정렬 오버헤드 여부를 진단합니다."
    CATEGORY = "STATISTICS"
    TARGET_NODE_TYPES = ["WindowAgg"]
    SUPPORTED_PG_VERSION = "all"
    DEFAULT_PRIORITY = 4
    DEFAULT_SEVERITY = "INFO"

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
        if not sort_node:
            return recommendations

        sort_type = sort_node.get("Node Type", "Sort")
        space_type = sort_node.get("Sort Space Type", "Memory")
        method = sort_node.get("Sort Method", "")
        is_disk_sort = space_type == "Disk" or "external" in method.lower()

        if is_disk_sort:
            recommendations.append(
                RecommendationModel(
                    title="WindowAgg 디스크 정렬 오버헤드 감지",
                    description=f"WindowAgg 연산 시 하위 {sort_type} 정렬에서 work_mem 한도를 초과하여 임시 디스크 공간({method})을 사용했습니다.",
                    severity="WARNING",
                    priority=2,
                    confidence="HIGH",
                    reason="정렬 메모리가 부족하여 디스크 I/O가 발생했습니다.",
                    recommendation="해당 세션의 work_mem을 증설하거나, PARTITION BY / ORDER BY 구성을 지원하는 복합 인덱스 생성을 검토하십시오.",
                    recommended_sql="SET work_mem = '64MB';",
                    plan_node="WindowAgg",
                    estimated_gain="Medium to High",
                    false_positive_risk="Low",
                )
            )
        else:
            # 인-메모리 소형 정렬인 경우: 인덱스 생성을 강요하지 않고 INFO 수준으로 안내
            recommendations.append(
                RecommendationModel(
                    title="WindowAgg 윈도우 함수 인-메모리 정렬 수행",
                    description=f"WindowAgg 절 연산을 위해 하위 노드에서 메모리 내 정렬({method if method else 'quicksort'})이 수행되었습니다.",
                    severity="INFO",
                    priority=4,
                    confidence="HIGH",
                    reason="윈도우 함수(OVER 절) 처리를 위해 메모리 내에서 가벼운 정렬이 수행되었습니다.",
                    recommendation="현재 메모리 내에서 신속하게 처리되고 있으므로 별도의 인덱스 생성이나 튜닝은 불필요합니다.",
                    recommended_sql=None,
                    plan_node="WindowAgg",
                    estimated_gain="None",
                    false_positive_risk="Low",
                )
            )

        return recommendations


from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class IncrementalSortSpillRule(BaseRule):
    RULE_ID = "RULE_STAT_007"
    NAME = "IncrementalSortSpillRule"
    DESCRIPTION = "증분 정렬(Incremental Sort) 수행 중 부분 정렬 메모리 초과로 디스크 스필이 발생하는지 진단합니다."
    CATEGORY = "STATISTICS"
    TARGET_NODE_TYPES = ["Incremental Sort"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Incremental Sort"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Incremental Sort")
        sort_space_type = str(node.get("Sort Space Type", "")).lower()

        if "disk" in sort_space_type or node.get("Sort Space Used", 0) > 10000:
            actual_kb = node.get("Sort Space Used", 0)
            recommendations.append(
                RecommendationModel(
                    title="증분 정렬(Incremental Sort) 메모리 부족 감지",
                    description=f"선행 정렬 집합을 기반으로 증분 정렬을 시도했으나, 후속 정렬 처리 중 메모리가 부족하여 디스크 스필({actual_kb} KB)이 일어났습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="이미 일부 정렬된 데이터를 활용하지만, 그룹 내 데이터량이 많아 잔여 컬럼 정렬 시 work_mem 한계를 넘어서고 있습니다.",
                    recommendation="증분 정렬의 완전한 인메모리 처리를 위해 `work_mem` 수치를 올려주거나 전체 정렬 조건 컬럼을 완전히 반영한 다중 컬럼 인덱스를 생성하십시오.",
                    recommended_sql="SET work_mem = '64MB';",
                    plan_node=node_type,
                    estimated_gain="Medium to High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

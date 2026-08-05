from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class MergeJoinSortRule(BaseRule):
    RULE_ID = "RULE_JOIN_003"
    NAME = "MergeJoinSortRule"
    DESCRIPTION = "Merge Join 수행 시 인덱스 부재로 인해 하위 노드에서 불필요한 정렬(Sort) 연산이 발생하는지 진단합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Merge Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Merge Join"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Merge Join")
        plans = node.get("Plans", [])

        # 하위 노드 중 Sort 노드가 포함되어 있는지 확인
        has_sort_node = any(child.get("Node Type") == "Sort" for child in plans)

        if has_sort_node:
            recommendations.append(
                RecommendationModel(
                    title="Merge Join을 위한 명시적 Sort 연산 발생",
                    description="Merge Join 조건절 집합을 정렬하기 위해 하위 연산에서 명시적 Sort 과정이 실행되고 있습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="Merge Join은 정렬된 입력을 필요로 합니다. 인덱스를 통한 정렬 순서 보장이 되지 않아 메모리/디스크 정렬(Sort) 노드가 추가되어 CPU 및 I/O 비용이 급증합니다.",
                    recommendation="조인 키 컬럼에 인덱스를 추가하여 정렬 작업 없이 Index Scan을 통해 정렬된 순서대로 조인을 수행하도록 개선하거나, 데이터 특성에 따라 Hash Join 유도를 검토하십시오.",
                    plan_node=node_type,
                    estimated_gain="Medium to High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

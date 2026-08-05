from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class CrossJoinRule(BaseRule):
    RULE_ID = "RULE_JOIN_007"
    NAME = "CrossJoinRule"
    DESCRIPTION = "조인 조건이 누락되거나 잘못 설정되어 카티시안 곱(Cartesian Product)이 발생하는지 진단합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Nested Loop", "Hash Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "CRITICAL"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Join")

        # Hash Cond, Join Filter, Filter 속성이 전혀 없는 경우 조인 조건 누락 가능성 진단
        has_hash_cond = "Hash Cond" in node
        has_join_filter = "Join Filter" in node
        has_filter = "Filter" in node

        if not (has_hash_cond or has_join_filter or has_filter):
            recommendations.append(
                RecommendationModel(
                    title="카티시안 곱(Cross Join) 발생 감지",
                    description="조인 연산 노드에 명시적인 조인 조건(Join Cond / Filter)이 존재하지 않습니다.",
                    severity="CRITICAL",
                    priority=1,
                    reason="조인 조건절이 누락되어 두 테이블 간의 모든 조합을 탐색하는 Cartesian Product가 수행되고 있습니다. 이는 극심한 메모리 및 CPU 소모를 야기합니다.",
                    recommendation="ON 절 또는 WHERE 절에 두 테이블 간 결합을 위한 올바른 조인 조건이 누락되었는지 SQL 쿼리를 점검하십시오.",
                    plan_node=node_type,
                    estimated_gain="Extreme",
                    false_positive_risk="Medium",
                )
            )

        return recommendations

from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class NestedLoopHighLoopsRule(BaseRule):
    RULE_ID = "RULE_JOIN_004"
    NAME = "NestedLoopHighLoopsRule"
    DESCRIPTION = "Nested Loop 조인 중 내부 테이블 탐색 횟수(Loops)가 과도하게 많아 비효율이 발생하는지 진단합니다."
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

            # 내부 탐색 반복 횟수가 100,000회 이상인 경우 감지
            if actual_loops >= 100000:
                recommendations.append(
                    RecommendationModel(
                        title="Nested Loop 내부 반복 수행 횟수 과다",
                        description=f"Nested Loop 조인 시 내부 드라이븐 테이블 탐색이 총 {actual_loops:,}회 반복 실행되었습니다.",
                        severity="HIGH",
                        priority=2,
                        reason="내부 테이블에 인덱스가 존재하더라도 외부 집합의 크기가 커서 과도하게 반복 탐색이 일어나면 랜덤 I/O 및 CPU overhead가 극대화됩니다.",
                        recommendation="외부 테이블의 필터링 조건을 강화하여 조인 대상 건수를 줄이거나, 대량 조인에 유리한 Hash Join 형태로 옵티마이저가 수립하도록 쿼리/힌트/통계정보를 재정비하십시오.",
                        plan_node=node_type,
                        estimated_gain="High",
                        false_positive_risk="Medium",
                    )
                )

        return recommendations

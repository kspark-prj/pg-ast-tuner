from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class SubqueryScanRepetitionRule(BaseRule):
    RULE_ID = "RULE_SCAN_006"
    NAME = "SubqueryScanRepetitionRule"
    DESCRIPTION = "Subquery Scan 또는 SubPlan이 상위 집합의 크기만큼 반복 수행되어 N+1 스타일의 스캔 병목을 일으키는지 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Subquery Scan"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Subquery Scan"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Subquery Scan")
        actual_loops = node.get("Actual Loops", 1)
        actual_rows = node.get("Actual Rows", 0)

        # 서브쿼리가 1,000회 이상 반복 실행된 케이스 감지
        if actual_loops >= 1000:
            recommendations.append(
                RecommendationModel(
                    title="Subquery Scan 반복 실행 병목 감지",
                    description=f"서브쿼리 스캔(Subquery Scan)이 메인 쿼리 루프에 의해 총 {actual_loops:,}회 반복 실행되었습니다.",
                    severity="HIGH",
                    priority=2,
                    reason="상관 서브쿼리(Correlated Subquery) 또는 미튜닝된 스칼라 서브쿼리로 인해 Outer 테이블 행마다 서브쿼리 스캔이 중복 실행되고 있습니다.",
                    recommendation="서브쿼리를 명시적인 JOIN(LEFT JOIN 등) 형태나 CTE(WITH 절)로 리팩토링하여 집합 연산으로 전환하십시오.",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low",
                )
            )

        return recommendations


from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class JoinCardinalityMisestimationRule(BaseRule):
    RULE_ID = "RULE_JOIN_006"
    NAME = "JoinCardinalityMisestimationRule"
    DESCRIPTION = "조인 노드에서 예측 행 수(Plan Rows)와 실제 행 수(Actual Rows) 간 큰 오차가 발생하는지 진단합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Hash Join", "Nested Loop", "Merge Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Join")

        plan_rows = node.get("Plan Rows", 0)
        actual_rows = node.get("Actual Rows", 0)

        if plan_rows > 0 and actual_rows > 0:
            ratio = actual_rows / plan_rows if actual_rows > plan_rows else plan_rows / actual_rows

            # 예측치와 실제치가 10배 이상 차이나는 경우 감지
            if ratio >= 10.0 and abs(actual_rows - plan_rows) > 1000:
                recommendations.append(
                    RecommendationModel(
                        title="조인 카디널리티(행 수) 예측 실패",
                        description=f"옵티마이저 예측 행 수({plan_rows:,}건)와 실제 처리 행 수({actual_rows:,}건) 간 약 {ratio:.1f}배의 큰 오차가 발생했습니다.",
                        severity="HIGH",
                        priority=1,
                        reason="통계 정보가 오래되었거나 다중 컬럼 조건 간 상관관계를 옵티마이저가 알지 못해 부적절한 조인 방식(예: NL Join vs Hash Join)을 선택했을 가능성이 높습니다.",
                        recommendation="대상 테이블의 ANALYZE를 실행하여 통계 정보를 최신화하고, 필요한 경우 extended statistics(CREATE STATISTICS) 생성을 검토하십시오.",
                        recommended_sql="ANALYZE VERBOSE;",
                        plan_node=node_type,
                        estimated_gain="High",
                        false_positive_risk="Low",
                    )
                )

        return recommendations

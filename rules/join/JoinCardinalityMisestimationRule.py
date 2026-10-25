from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class JoinCardinalityMisestimationRule(BaseRule):
    RULE_ID = "RULE_JOIN_006"
    NAME = "JoinCardinalityMisestimationRule"
    DESCRIPTION = "조인 노드에서 예측 행 수(Plan Rows)와 실제 행 수(Actual Rows) 간 큰 오차가 발생하는지 관찰합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Hash Join", "Nested Loop", "Merge Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 3
    DEFAULT_SEVERITY = "INFO"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Join")

        plan_rows = node.get("Plan Rows", 0)
        actual_rows = node.get("Actual Rows", 0)

        if plan_rows > 0 and actual_rows > 0:
            ratio = (
                actual_rows / plan_rows if actual_rows > plan_rows else plan_rows / actual_rows
            )

            # 예측치와 실제치가 10배 이상 차이나는 경우 감지
            if ratio >= 10.0 and abs(actual_rows - plan_rows) > 1000:
                recommendations.append(
                    RecommendationModel(
                        title="조인 카디널리티(행 수) 추정 오차 관찰",
                        description=f"옵티마이저 예측 행 수({plan_rows:,}건)와 실제 처리 행 수({actual_rows:,}건) 간 약 {ratio:.1f}배의 오차가 관찰되었습니다.",
                        severity="INFO",
                        priority=3,
                        confidence="MEDIUM",
                        reason="통계 정보 미흡 또는 다중 컬럼 조건 간 상관관계로 인해 오차가 발생했을 수 있습니다.",
                        recommendation="현재 실행시간이 충분히 빠르다면 성급한 튜닝은 불필요하며, 데이터 규모 증가에 대비하여 필요 시 대상 테이블의 ANALYZE 실행 또는 통계 최신화 여부를 검토하십시오.",
                        recommended_sql=None,
                        plan_node=node_type,
                        estimated_gain="Low to Medium",
                        false_positive_risk="Medium",
                    )
                )

        return recommendations

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class ConstraintTriggerOverheadRule(BaseRule):
    RULE_ID = "RULE_STR_003"
    NAME = "ConstraintTriggerOverheadRule"
    DESCRIPTION = "DML(INSERT/UPDATE/DELETE) 수행 중 FK 검증 또는 트리거 실행 지연 요소를 진단합니다."
    CATEGORY = "STRUCTURAL"
    TARGET_NODE_TYPES = ["ModifyTable", "Insert", "Update", "Delete"]
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES or "Trigger" in node

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "DML Node")

        # 실행 계획 루트 또는 해당 노드의 Trigger Time / Execution Time 비교
        trigger_time = node.get("Trigger Time", 0.0)
        total_time = node.get("Actual Total Time", 0.0)

        # 트리거 실행 시간이 100ms 이상이면서 전체 실행 시간의 50% 이상을 점유할 때
        if trigger_time > 100.0 and (total_time > 0 and (trigger_time / total_time) > 0.5):
            recommendations.append(
                RecommendationModel(
                    title=f"{node_type} 수행 중 Trigger/FK 제약조건 지연",
                    description=f"총 실행 시간({total_time:.2f}ms) 중 트리거 및 외래키(FK) 검증 연산에 {trigger_time:.2f}ms가 소요되었습니다.",
                    severity="HIGH",
                    priority=1,
                    reason="참조 테이블(Foreign Key)에 조인 키 인덱스가 누락되었거나 Row-level Trigger 로직이 무겁습니다.",
                    recommendation="FK 참조 컬럼에 인덱스가 제대로 생성되어 있는지 점검하고, 대량 Batch DML 시에는 트리거 일시 비활성화를 고려하십시오.",
                    recommended_sql="-- FK 컬럼 인덱스 확인 후 생성 권장",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

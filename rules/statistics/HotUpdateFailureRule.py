from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class HotUpdateFailureRule(BaseRule):
    RULE_ID = "RULE_STR_004"
    NAME = "HotUpdateFailureRule"
    DESCRIPTION = "UPDATE 시 HOT(Heap-Only Tuple) 최적화가 적용되지 못해 인덱스 블록 수정 오버헤드가 발생하는지 진단합니다."
    CATEGORY = "STRUCTURAL"
    TARGET_NODE_TYPES = ["Update"]
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Update"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type")
        table_name = node.get("Relation Name", "Table")

        # UPDATE 대상 행 수
        actual_rows = node.get("Actual Rows", 0)
        # 인덱스 변경이 일어난 비율 등의 커스텀 지표 활용 가능

        if actual_rows > 5000:
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' UPDATE 연산 시 HOT(Heap-Only Tuple) 적용 불가 위험",
                    description=f"'{table_name}' 테이블에 {actual_rows:,}건의 UPDATE가 수행되었습니다. 인덱스가 걸린 컬럼 수정 시 I/O 부하가 급증합니다.",
                    severity="WARNING",
                    priority=2,
                    reason="UPDATE 대상 컬럼에 인덱스가 포함되어 있거나, Fillfactor 여유 공간이 부족하여 HOT Update가 실패했을 가능성이 높습니다.",
                    recommendation="자주 변경되는 컬럼은 인덱스 대상에서 제외하고, 해당 테이블의 FILLFACTOR 비율(예: 80~90)을 낮춰 HOT 공간을 확보하십시오.",
                    recommended_sql=f"ALTER TABLE {table_name} SET (FILLFACTOR = 80);",
                    plan_node=node_type,
                    estimated_gain="Medium",
                    false_positive_risk="Medium",
                )
            )

        return recommendations

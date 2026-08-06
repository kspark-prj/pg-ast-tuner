from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class CTEInliningFailureRule(BaseRule):
    RULE_ID = "RULE_STR_001"
    NAME = "CTEInliningFailureRule"
    DESCRIPTION = "WITH 절(CTE) 사용 시 Materialize 되면서 인라이닝 최적화가 방해받고 있는지 진단합니다."
    CATEGORY = "STRUCTURAL"
    TARGET_NODE_TYPES = ["CTE Scan"]
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "CTE Scan"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type")
        cte_name = node.get("CTE Name", "Unknown")
        actual_rows = node.get("Actual Rows", 0)

        # CTE 스캔으로 인해 대량 데이터가 Temp 구역에 불필요하게 구동된 경우
        if actual_rows > 10000:
            recommendations.append(
                RecommendationModel(
                    title=f"CTE '{cte_name}' 인라이닝(Inlining) 실패 및 구체화 오버헤드",
                    description=f"WITH 절('{cte_name}')이 인라인 서브쿼리로 병합되지 못하고 Materialized 임시 테이블로 구체화되어 {actual_rows:,}건을 처리했습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="PostgreSQL 옵티마이저가 CTE 최적화 펜스(Fence)로 인해 조건절 푸시다운(Pushdown) 최적화를 수행하지 못했습니다.",
                    recommendation="WITH 절 선언부에 'AS NOT MATERIALIZED' 키워드를 명시하거나 Subquery(Inline View) 형태로 리팩토링하십시오.",
                    recommended_sql=f"WITH {cte_name} AS NOT MATERIALIZED (\n    ... \n)",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

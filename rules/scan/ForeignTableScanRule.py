from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class ForeignTableScanRule(BaseRule):
    RULE_ID = "RULE_STR_002"
    NAME = "ForeignTableScanRule"
    DESCRIPTION = "FDW(Foreign Data Wrapper) 원격 테이블 스캔 시 푸시다운(Pushdown) 실패로 대량 데이터가 전송되는지 진단합니다."
    CATEGORY = "STRUCTURAL"
    TARGET_NODE_TYPES = ["Foreign Scan"]
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Foreign Scan"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type")
        table_name = node.get("Relation Name", "Foreign Table")
        actual_rows = node.get("Actual Rows", 0)

        # FDW를 통해 로컬 네트워크로 5만건 이상이 전송된 경우
        if actual_rows > 50000:
            recommendations.append(
                RecommendationModel(
                    title=f"원격 테이블 '{table_name}' 대량 데이터 네트워크 전송",
                    description=f"Foreign Scan 연산으로 원격 DB로부터 총 {actual_rows:,}건의 대량 데이터를 로컬 세션으로 네트워크 수신했습니다.",
                    severity="HIGH",
                    priority=1,
                    reason="WHERE 조건절이나 JOIN 연산이 원격 서버로 Pushdown되지 못해 전체 데이터를 로컬로 전송받아 처리하고 있습니다.",
                    recommendation="원격 서버 측 함수/연산자 매핑 상태를 확인하거나, fetch_size 옵션을 조정하고 조건식을 Pushdown 가능하도록 변경하십시오.",
                    recommended_sql=f"-- 원격 서버 통계 정보 갱신: ANALYZE {table_name};",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Medium",
                )
            )

        return recommendations

from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class HighFilterRemovalRatioRule(BaseRule):
    RULE_ID = "RULE_SCAN_005"
    NAME = "HighFilterRemovalRatioRule"
    DESCRIPTION = "스캔 노드 이후 Filter 과정에서 버려지는 행(Rows Removed by Filter)의 비율이 높아 I/O 낭비가 심한지 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Seq Scan", "Index Scan", "Bitmap Heap Scan"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Scan")
        table_name = node.get("Relation Name")

        rows_removed = node.get("Rows Removed by Filter", 0)
        actual_rows = node.get("Actual Rows", 0)

        if not table_name or rows_removed == 0:
            return recommendations

        total_scanned = actual_rows + rows_removed

        # 10,000건 이상 읽었는데 90% 이상이 Filter로 버려진 경우
        if total_scanned >= 10000 and (rows_removed / total_scanned) >= 0.9:
            removal_pct = (rows_removed / total_scanned) * 100
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' 과도한 필터 탈락(Filter Removal) 발생",
                    description=f"'{table_name}' 테이블에서 읽어들인 데이터 중 {removal_pct:.1f}%({rows_removed:,}건)가 Filter 조건에 의해 후속 폐기되었습니다.",
                    severity="HIGH",
                    priority=1,
                    reason="스캔 단계에서 필요한 행만 걸러내지 못해 디스크 I/O 및 CPU 리소스를 읽고 버리는 데 낭비하고 있습니다.",
                    recommendation="Filter에 사용된 조건 컬럼을 인덱스 조건(Index Cond)으로 수용할 수 있도록 복합 인덱스를 재구성하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 외부에서 수행되어야 합니다.",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

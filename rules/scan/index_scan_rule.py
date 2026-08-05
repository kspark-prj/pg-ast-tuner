from typing import List, Union
from rules.base_rule import BaseRule, RuleContext
from models.recommendation import RecommendationModel

class IndexScanRule(BaseRule):
    RULE_ID = "RULE_SCAN_002"
    NAME = "IndexScanRule"
    DESCRIPTION = "인덱스 스캔(Index Scan) 수행 시 너무 많은 양의 행을 반환하여 랜덤 I/O 병목을 유발하는지 검증합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Index Scan"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Index Scan"

    def analyze(self, context: RuleContext, node: dict) -> List[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Index Scan")
        table_name = node.get("Relation Name")
        actual_rows = node.get("Actual Rows", 0)

        if not table_name:
            return recommendations

        meta = context.metadata_provider.get_table_metadata(table_name)
        if meta.total_rows > 50000 and actual_rows > (meta.total_rows * 0.25):
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' 비효율적 인덱스 스캔 감지",
                    description=f"'{table_name}' 테이블에서 인덱스 스캔을 수행 중이나, 반환 데이터량({actual_rows}건)이 전체 행의 25%를 초과하여 대규모 랜덤 I/O 병목을 유발하고 있습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="인덱스 스캔으로 너무 많은 행을 조회하면 테이블 페이지에 무작위로 접근하게 되어(Random I/O) 풀 스캔보다 느려질 수 있습니다.",
                    recommendation="테이블 페이지 랜덤 접근 오버헤드를 줄이기 위해 Bitmap Index Scan으로 강제 전환되도록 유도하거나, 커버링 인덱스(INCLUDE 절) 구성 또는 클러스터링을 검토하십시오.",
                    recommended_sql=f"ANALYZE VERBOSE {table_name};",
                    plan_node=node_type,
                    estimated_gain="Medium",
                    false_positive_risk="Low"
                )
            )

        return recommendations

from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class BitmapHeapScanLossyRule(BaseRule):
    RULE_ID = "RULE_SCAN_003"
    NAME = "BitmapHeapScanLossyRule"
    DESCRIPTION = "Bitmap Heap Scan 중 work_mem 부족으로 인한 Lossy 전환 및 과도한 Recheck 힙 페이지 접근이 발생하는지 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Bitmap Heap Scan"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Bitmap Heap Scan"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Bitmap Heap Scan")
        table_name = node.get("Relation Name")

        if not table_name:
            return recommendations

        # PostgreSQL Execution Plan의 Exact / Lossy Heap Blocks 지표 확인
        exact_blocks = node.get("Exact Heap Blocks", 0)
        lossy_blocks = node.get("Lossy Heap Blocks", 0)
        recheck_cond = node.get("Recheck Cond")

        if lossy_blocks > 0:
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' 비트맵 스캔 Lossy 전환 감지",
                    description=f"'{table_name}' 테이블 스캔 중 비트맵 메모리가 부족하여 {lossy_blocks}개의 블록이 Lossy 상태로 전환되었고 Recheck 오버헤드가 발생하고 있습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="작업 메모리(work_mem) 한계로 인해 정밀한 튜플 단위 비트맵이 블록 단위(Lossy)로 손실 전환되어, 불필요한 Heap 페이지 Recheck 검증 비용이 급증했습니다.",
                    recommendation="비트맵 맵 크기를 메모리에 유지할 수 있도록 `work_mem` 설정을 상향하거나, 인덱스를 다중 컬럼 복합 인덱스로 재구성하십시오.",
                    recommended_sql="SET work_mem = '64MB';",
                    plan_node=node_type,
                    estimated_gain="Medium to High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

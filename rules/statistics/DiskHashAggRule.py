from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class DiskHashAggRule(BaseRule):
    RULE_ID = "RULE_STAT_004"
    NAME = "DiskHashAggRule"
    DESCRIPTION = "HashAggregate 집계 중 work_mem 초과로 인해 디스크 기반 해시 집계(Disk-based HashAgg)가 발생했는지 감지합니다."
    CATEGORY = "AGGREGATION"
    TARGET_NODE_TYPES = ["Aggregate"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        # Aggregate 노드 중 Disk-based HashAgg가 선택된 경우 감지
        return (
            node.get("Node Type") == "Aggregate"
            and node.get("Strategy") == "Hashed"
            and node.get("Disk Used", 0) > 0
        )

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Aggregate")
        disk_kb = node.get("Disk Used", 0)

        recommendations.append(
            RecommendationModel(
                title="디스크 기반 해시 집계(Disk HashAgg) 감지",
                description=f"GROUP BY/집계 연산 처리 중 메모리가 부족하여 디스크 공간({disk_kb} KB)을 사용하여 해시 테이블을 처리 중입니다.",
                severity="WARNING",
                priority=2,
                reason="해시 집계를 수행하기 위한 그룹화 대상 데이터셋이 작업 메모리(work_mem) 한도를 초과하여 디스크 임시 공간으로 강제 스필(Spill)되었습니다.",
                recommendation="집계용 작업 메모리를 넓히기 위해 `work_mem` 수치를 상향 조정하거나, 인덱스를 활용한 정렬 집계(GroupAggregate)로 유도하십시오.",
                recommended_sql="SET work_mem = '64MB';",
                plan_node=node_type,
                estimated_gain="High",
                false_positive_risk="Low",
            )
        )

        return recommendations

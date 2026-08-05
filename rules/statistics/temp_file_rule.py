from typing import List, Union
from rules.base_rule import BaseRule, RuleContext
from models.recommendation import RecommendationModel

class TempFileRule(BaseRule):
    RULE_ID = "RULE_STAT_001"
    NAME = "TempFileRule"
    DESCRIPTION = "노드 실행 중 메모리 공간 부족으로 임시 파일 쓰기(Temp Written Blocks)가 발생하여 디스크 I/O 병목이 발생했는지 감지합니다."
    CATEGORY = "STATISTICS"
    TARGET_NODE_TYPES = ["*"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Temp Written Blocks", 0) > 0

    def analyze(self, context: RuleContext, node: dict) -> List[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Unknown Node")
        temp_write = node.get("Temp Written Blocks", 0)

        recommendations.append(
            RecommendationModel(
                title="디스크 임시 파일 쓰기 발생",
                description=f"임시 파일 쓰기가 발생했습니다 ({temp_write * 8} KB). 메모리 공간 부족으로 디스크 I/O 병목이 발생하고 있습니다.",
                severity="WARNING",
                priority=2,
                reason="정렬(Sort), 해시(Hash), 또는 그룹화(Group)를 위해 할당된 작업 메모리(work_mem)가 부족하여, 엔진이 데이터를 로컬 디스크에 임시 파일로 작성하며 연산을 지속하고 있습니다.",
                recommendation="작업 메모리(work_mem) 설정을 상향 조정하거나 대용량 조인/정렬 범위를 최적화하십시오.",
                recommended_sql="SET work_mem = '64MB';",
                plan_node=node_type,
                estimated_gain="High (메모리 정렬로 전환되어 극적인 I/O 절감)",
                false_positive_risk="Low"
            )
        )

        return recommendations

from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class HashJoinBatchInflationRule(BaseRule):
    RULE_ID = "RULE_JOIN_009"
    NAME = "HashJoinBatchInflationRule"
    DESCRIPTION = "Hash Join 중 최초 예상과 달리 동적으로 배치(Batch) 수가 수십~수백 배 폭증했는지 진단합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Hash Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Hash Join"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Hash Join")

        # Original Hash Batches와 Original Hash Buckets 지표 활용
        orig_batches = node.get("Original Hash Batches", 1)
        actual_batches = node.get("Hash Batches", 1)

        if orig_batches == 1 and actual_batches >= 8:
            recommendations.append(
                RecommendationModel(
                    title="해시 조인 동적 배치 확장(Dynamic Batch Expansion) 발생",
                    description=f"최초 1개 배치로 예상되었으나 메모리 부족으로 인해 {actual_batches}개 배치로 동적 확장되었습니다.",
                    severity="HIGH",
                    priority=2,
                    reason="빌드 대상 데이터 크기 예측 오류로 인해 실행 중 배치 수가 폭증하면서 디스크 스필 및 I/O 병목이 중복 발생했습니다.",
                    recommendation="`work_mem` 수치를 높여 해시 테이블 분할을 방지하거나, 대상 테이블의 통계 정보(ANALYZE)를 갱신하십시오.",
                    recommended_sql="SET work_mem = '64MB';",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

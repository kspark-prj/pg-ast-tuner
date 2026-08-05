from typing import List, Union
from rules.base_rule import BaseRule, RuleContext
from models.recommendation import RecommendationModel

class HashJoinRule(BaseRule):
    RULE_ID = "RULE_JOIN_001"
    NAME = "HashJoinRule"
    DESCRIPTION = "해시 조인(Hash Join) 시 해시 테이블 빌드 크기가 work_mem을 초과하여 디스크로 임시 스필(Spill)되었는지 감지합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Hash Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "CRITICAL"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Hash Join"

    def analyze(self, context: RuleContext, node: dict) -> List[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Hash Join")
        hash_batches = node.get("Hash Batches", 1)

        if hash_batches > 1:
            recommendations.append(
                RecommendationModel(
                    title="해시 조인 디스크 스필 감지",
                    description=f"해시 조인 처리 중 빌드 테이블 크기가 work_mem을 초과하여 {hash_batches}개의 배치 블록으로 쪼개져 디스크 임시 공간으로 강제 스필(Spill)되었습니다.",
                    severity="CRITICAL",
                    priority=1,
                    reason="조인 대상 해시 테이블이 메모리에 다 올라가지 않아 디스크를 사용하여 조인 연산을 쪼개서 수행하고 있습니다. 이로 인해 심각한 디스크 I/O가 발생합니다.",
                    recommendation="해시 테이블이 한 번에 메모리에 로드될 수 있도록 세션 혹은 글로벌 단위의 작업 메모리 영역(work_mem)을 대폭 증가시켜 주십시오.",
                    recommended_sql="SET work_mem = '128MB';",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low"
                )
            )

        return recommendations

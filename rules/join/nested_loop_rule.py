from typing import List, Union
from rules.base_rule import BaseRule, RuleContext
from models.recommendation import RecommendationModel

class NestedLoopRule(BaseRule):
    RULE_ID = "RULE_JOIN_002"
    NAME = "NestedLoopRule"
    DESCRIPTION = "Nested Loop 조인 중 내부 드라이븐 테이블(Driven Table)에 조인 키 인덱스가 없어 반복적인 풀 스캔이 수행되는지 진단합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Nested Loop"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "CRITICAL"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Nested Loop"

    def analyze(self, context: RuleContext, node: dict) -> List[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Nested Loop")
        plans = node.get("Plans", [])

        if len(plans) >= 2:
            inner_node = plans[1]
            inner_node_type = inner_node.get("Node Type", "")

            if "Seq Scan" in inner_node_type:
                inner_table = inner_node.get("Relation Name", "알 수 없는 테이블")
                recommendations.append(
                    RecommendationModel(
                        title="Nested Loop 내부 테이블 조인 인덱스 부재",
                        description=f"Nested Loop 조인 중 내부 드라이븐 테이블('{inner_table}')을 반복 검색하기 위한 조인 키 인덱스가 없습니다.",
                        severity="CRITICAL",
                        priority=1,
                        reason=f"외부 테이블에서 추출된 매 행마다 내부 테이블 전체를 풀 스캔({inner_node_type})하고 있습니다. O(N * M)의 무서운 성능 저하가 초래됩니다.",
                        recommendation="조인 ON 절의 매핑 키 컬럼을 기준으로 내부 테이블에 인덱스를 생성하여 Random Access 효율을 극대화하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 외부에서 수행되어야 합니다.",
                        plan_node=node_type,
                        estimated_gain="Extreme (데이터량에 비례하여 기하급수적으로 성능 상승)",
                        false_positive_risk="Low"
                    )
                )

        return recommendations

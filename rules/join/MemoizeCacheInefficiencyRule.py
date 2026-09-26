from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class MemoizeCacheInefficiencyRule(BaseRule):
    RULE_ID = "RULE_JOIN_010"
    NAME = "MemoizeCacheInefficiencyRule"
    DESCRIPTION = "Nested Loop 조인 하위 Memoize 노드의 캐시 미스(Cache Misses) 비율이 높거나 메모리 초과로 재평가가 비효율적으로 발생하는지 진단합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Memoize"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Memoize"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        hits = node.get("Cache Hits", 0)
        misses = node.get("Cache Misses", 0)
        overflows = node.get("Cache Overflows", 0)
        total_evals = hits + misses

        if total_evals > 0:
            miss_ratio = misses / total_evals
            if miss_ratio >= 0.3 or overflows > 0:
                recommendations.append(
                    RecommendationModel(
                        title="Memoize 노드 캐시 미스 및 비효율 감지",
                        description=(
                            f"Memoize 노드에서 캐시 미스 비율이 {miss_ratio * 100:.1f}% "
                            f"(Hits: {hits}, Misses: {misses}, Overflows: {overflows})로 과도하게 높습니다."
                        ),
                        severity="HIGH",
                        priority=2,
                        reason="Nested Loop 실행 중 파라미터 유일값 분포가 넓거나 메모리 제한(work_mem)으로 인해 Memoize 캐시가 빈번히 플러시되고 재평가되었습니다.",
                        recommendation="Nested Loop 대신 Hash Join/Merge Join으로 튜닝하거나, inner table의 조인 컬럼 인덱스 구성을 강화하고 work_mem을 상향 조정하십시오.",
                        recommended_sql="SET enable_memoize = off; -- 또는 work_mem 상향 조정",
                        plan_node="Memoize",
                        estimated_gain="High",
                        false_positive_risk="Low",
                    )
                )

        return recommendations

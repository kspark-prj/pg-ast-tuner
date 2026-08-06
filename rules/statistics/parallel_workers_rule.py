from rules.base_rule import BaseRule, RuleContext
from models.recommendation import RecommendationModel

class ParallelWorkersRule(BaseRule):
    RULE_ID = "RULE_STAT_002"
    NAME = "ParallelWorkersRule"
    DESCRIPTION = "병렬 처리 및 Gather 노드 수행 시 너무 많은 병렬 워커가 계획되어 리소스 과부하가 생길 수 있는지 검증합니다."
    CATEGORY = "STATISTICS"
    TARGET_NODE_TYPES = ["*"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        node_type = str(node.get("Node Type", ""))
        return ("Parallel" in node_type or "Gather" in node_type) and node.get("Workers Planned", 0) >= 4

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Gather")
        workers = node.get("Workers Planned", 0)

        recommendations.append(
            RecommendationModel(
                title="과도한 병렬 워커 계획",
                description=f"많은 수의 병렬 워커({workers}명)가 할당되었습니다. 세션당 중복 메모리가 할당되어 전체 시스템 OOM 위험이 가중됩니다.",
                severity="WARNING",
                priority=2,
                reason="각 병렬 워커는 독립적인 프로세스로 실행되며 세션 작업 메모리(work_mem)를 개별적으로 할당받을 수 있어 CPU 및 메모리 가용 자원을 빠르게 소모합니다.",
                recommendation="복잡한 집계 분석이 아니라면 병렬 워커 개수를 기본값(2개)으로 조율해 리소스 소모를 제어하십시오.",
                recommended_sql="SET max_parallel_workers_per_gather = 2;",
                plan_node=node_type,
                estimated_gain="Resource Stability",
                false_positive_risk="Low"
            )
        )

        return recommendations

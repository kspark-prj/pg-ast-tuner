
from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class ParallelJoinWorkerLossRule(BaseRule):
    RULE_ID = "RULE_JOIN_008"
    NAME = "ParallelJoinWorkerLossRule"
    DESCRIPTION = (
        "병렬 조인(Parallel Join) 수행 시 계획된 워커 프로세스를 모두 활용하지 못했는지 진단합니다."
    )
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Gather", "Gather Merge"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Gather")

        workers_planned = node.get("Workers Planned", 0)
        workers_launched = node.get("Workers Launched", 0)

        if workers_planned > 0 and workers_launched < workers_planned:
            recommendations.append(
                RecommendationModel(
                    title="병렬 조인 워커 프로세스 부족",
                    description=f"계획된 병렬 워커는 {workers_planned}개였으나, 실제 실행 시 {workers_launched}개만 할당되어 작동했습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="시스템 전체의 `max_worker_processes` 또는 `max_parallel_workers` 한계에 도달하여 동시 병렬 처리 능력이 저하되었습니다.",
                    recommendation="PostgreSQL 서버 설정의 max_parallel_workers 및 max_worker_processes 값을 점검하여 동시성 여유를 확보하십시오.",
                    recommended_sql="SHOW max_parallel_workers;",
                    plan_node=node_type,
                    estimated_gain="Medium",
                    false_positive_risk="Low",
                )
            )

        return recommendations

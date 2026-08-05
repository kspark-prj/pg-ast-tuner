from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class ParallelWorkerSkewRule(BaseRule):
    RULE_ID = "RULE_STAT_005"
    NAME = "ParallelWorkerSkewRule"
    DESCRIPTION = (
        "병렬 처리 시 워커(Worker) 프로세스 간 데이터 처리 불균형(Skew)이 심각한지 감지합니다."
    )
    CATEGORY = "STATISTICS"
    TARGET_NODE_TYPES = ["Gather", "Gather Merge"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 3
    DEFAULT_SEVERITY = "INFO"

    def match(self, context: RuleContext, node: dict) -> bool:
        node_type = str(node.get("Node Type", ""))
        return node_type in self.TARGET_NODE_TYPES and node.get("Workers Launched", 0) > 1

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Gather")
        workers_launched = node.get("Workers Launched", 0)

        # 하위 노드의 Workers 처리 실적 분석 (JSON 실행계획 내 Workers 배열 체크)
        plans = node.get("Plans", [])
        if plans:
            child_workers = plans[0].get("Workers", [])
            if len(child_workers) > 1:
                rows_per_worker = [w.get("Actual Rows", 0) for w in child_workers]
                max_rows = max(rows_per_worker)
                min_rows = min(rows_per_worker)

                # 특정 워커가 5배 이상 일을 더 수행한 경우 (데이터 쏠림)
                if min_rows > 0 and (max_rows / min_rows) >= 5.0 and max_rows > 10000:
                    recommendations.append(
                        RecommendationModel(
                            title="병렬 워커 간 데이터 처리 편중(Worker Skew) 감지",
                            description=f"할당된 {workers_launched}개의 병렬 워커 간 처리 데이터량 차이가 5배 이상으로 편중되었습니다. (최대: {max_rows:,}건 / 최소: {min_rows:,}건)",
                            severity="INFO",
                            priority=3,
                            reason="테이블 블록 분배가 균등하지 않거나 특정 데이터 값의 카디널리티가 쏠려 있어 일부 병렬 워커가 Bottleneck이 되고 있습니다.",
                            recommendation="테이블의 통계 수집 레벨(SET STATISTICS)을 높여 ANALYZE를 재실행하거나, 데이터 파티셔닝 전략을 재검토하십시오.",
                            plan_node=node_type,
                            estimated_gain="Medium",
                            false_positive_risk="Medium",
                        )
                    )

        return recommendations

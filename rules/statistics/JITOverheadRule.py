from typing import List, Union

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class JITOverheadRule(BaseRule):
    RULE_ID = "RULE_STAT_006"
    NAME = "JITOverheadRule"
    DESCRIPTION = "JIT(Just-In-Time) 컴파일 연산 시간이 과도하게 소비되었는지 진단합니다."
    CATEGORY = "STATISTICS"
    TARGET_NODE_TYPES = ["*"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        # Node 자체에 JIT 정보가 들어있는지 확인
        return "JIT" in node

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        jit_info = node.get("JIT", {})

        timing = jit_info.get("Timing", {})
        generation_time = timing.get("Generation", 0.0)
        inlining_time = timing.get("Inlining", 0.0)
        optimization_time = timing.get("Optimization", 0.0)
        emission_time = timing.get("Emission", 0.0)

        total_jit_time = generation_time + inlining_time + optimization_time + emission_time

        # JIT 컴파일 소요 시간이 100ms 이상으로 과도하게 긴 경우
        if total_jit_time >= 100.0:
            recommendations.append(
                RecommendationModel(
                    title="JIT(Just-In-Time) 컴파일 오버헤드 감지",
                    description=f"JIT 컴파일 작업에 총 {total_jit_time:.1f}ms의 지연이 발생했습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="쿼리의 복잡성으로 인해 옵티마이저가 JIT를 활성화했으나, JIT 코드를 생성 및 최적화하는 비용이 최적화 이득보다 길어졌습니다.",
                    recommendation="해당 세션 또는 OLTP 성격의 SQL 실행 시 JIT 옵션을 끄거나 jit_above_cost 임계값을 높여 지연을 방지하십시오.",
                    recommended_sql="SET jit = off;",
                    plan_node="JIT",
                    estimated_gain="High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

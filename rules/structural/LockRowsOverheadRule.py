from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class LockRowsOverheadRule(BaseRule):
    RULE_ID = "RULE_STR_005"
    NAME = "LockRowsOverheadRule"
    DESCRIPTION = "SELECT FOR UPDATE / FOR SHARE (LockRows) 연산 시 대량의 테이블 스캔 또는 비인덱스 접근으로 인해 락 대기 및 병목 오버헤드가 유발되는지 진단합니다."
    CATEGORY = "STRUCTURAL"
    TARGET_NODE_TYPES = ["LockRows"]
    SUPPORTED_PG_VERSION = "all"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "LockRows"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        sub_plans = node.get("Plans", [])
        has_seq_scan = any(sub.get("Node Type") == "Seq Scan" for sub in sub_plans)

        severity = "CRITICAL" if has_seq_scan else "HIGH"

        recommendations.append(
            RecommendationModel(
                title="행 잠금(LockRows / FOR UPDATE) 동시성 병목 위험 감지",
                description="동시성 행 잠금(LockRows) 스캔 구간에서 탐색 범위가 크거나 테이블 풀 스캔(Seq Scan)이 감지되었습니다.",
                severity=severity,
                priority=1,
                reason="FOR UPDATE / FOR SHARE 절 구문 실행 시 유일성 인덱스 미비로 필요 이상의 행에 락을 점유하면서 트랜잭션 동시성이 급격히 저하되고 락 블로킹이 유발될 위험이 큽니다.",
                recommendation="PK 또는 명확한 인덱스 조건으로 대상을 최소화하고, 동시성 처리를 위해 `NOWAIT` 또는 `SKIP LOCKED` 옵션 적용을 검토하십시오.",
                recommended_sql="SELECT * FROM table_name WHERE id = 1 FOR UPDATE NOWAIT;",
                plan_node="LockRows",
                estimated_gain="High",
                false_positive_risk="Low",
            )
        )

        return recommendations

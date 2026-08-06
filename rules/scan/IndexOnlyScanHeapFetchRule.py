
from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class IndexOnlyScanHeapFetchRule(BaseRule):
    RULE_ID = "RULE_SCAN_004"
    NAME = "IndexOnlyScanHeapFetchRule"
    DESCRIPTION = "Index Only Scan 수행 중 Visibility Map 미갱신으로 인해 과도한 테이블 힙 접근(Heap Fetches)이 발생하는지 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Index Only Scan"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Index Only Scan"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Index Only Scan")
        table_name = node.get("Relation Name")
        heap_fetches = node.get("Heap Fetches", 0)
        actual_rows = node.get("Actual Rows", 0)

        if not table_name:
            return recommendations

        # Index Only Scan임에도 조회 건수의 20% 이상 Heap Fetch가 발생한 경우
        if actual_rows > 0 and heap_fetches > (actual_rows * 0.2):
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' Index Only Scan 힙 재접근 과다",
                    description=f"'{table_name}' 테이블에서 Index Only Scan이 동작했으나, 총 {heap_fetches:,}회의 불필요한 테이블 힙 접근(Heap Fetch)이 일어났습니다.",
                    severity="WARNING",
                    priority=2,
                    reason="데드 튜플(Dead Tuple) 정리 및 Visibility Map 갱신이 늦어져, 인덱스만으로 결과를 반환하지 못하고 테이블 힙 블록을 직접 재확인했습니다.",
                    recommendation="대상 테이블에 VACUUM(또는 ANALYZE)을 수행하여 Visibility Map을 최신 상태로 정비하고 Autovacuum주기를 점검하십시오.",
                    recommended_sql=f"VACUUM ANALYZE {table_name};",
                    plan_node=node_type,
                    estimated_gain="Medium",
                    false_positive_risk="Low",
                )
            )

        return recommendations

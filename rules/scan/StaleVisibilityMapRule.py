from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class StaleVisibilityMapRule(BaseRule):
    RULE_ID = "RULE_SCAN_008"
    NAME = "StaleVisibilityMapRule"
    DESCRIPTION = "데드 튜플(Dead Tuples) 및 테이블 블로트(Bloat)로 인해 스캔 시 불필요한 I/O가 대량 발생하는지 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Seq Scan", "Bitmap Heap Scan"]
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type")
        table_name = node.get("Relation Name")

        shared_hit = node.get("Shared Hit Blocks", 0)
        shared_read = node.get("Shared Read Blocks", 0)
        total_blocks = shared_hit + shared_read
        actual_rows = node.get("Actual Rows", 0)

        if not table_name or total_blocks < 1000:
            return recommendations

        # Filter 혹은 Index Recheck 과정에서 필터링되어 버려진 행들을 포함하여
        # 실제 디스크에서 접근한 전체 유효한(Live) 행들의 밀도를 측정합니다.
        # 이를 통해 선택도(Selectivity)가 높은 필터 쿼리에서의 오진을 방지합니다.
        rows_removed_filter = node.get("Rows Removed by Filter", 0)
        rows_removed_recheck = node.get("Rows Removed by Index Recheck", 0)
        total_live_rows = actual_rows + rows_removed_filter + rows_removed_recheck

        # 읽은 블록 수 대비 전체 유효 행 수가 지나치게 적은 경우 (블록당 0.1개 미만 행 추출)
        if (total_live_rows / total_blocks) < 0.1:
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' 테이블 블로팅(Bloat) 및 Dead Tuple 누적 의심",
                    description=f"'{table_name}' 스캔 중 총 {total_blocks:,}개의 페이지 블록을 읽었으나 스캔 대상 유효 행은 {total_live_rows:,}건에 불과합니다.",
                    severity="WARNING",
                    priority=2,
                    reason="잦은 UPDATE/DELETE 작업 후 Autovacuum이 제때 수행되지 않아 데드 튜플이 쌓이고 페이지 블로팅이 심화되었습니다.",
                    recommendation="대상 테이블에 VACUUM FULL 또는 pg_repack을 실행하여 빈 페이지 공간을 축소하고 튜플 공간을 정비하십시오.",
                    recommended_sql=f"VACUUM (VERBOSE, ANALYZE) {table_name};",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Medium",
                )
            )

        return recommendations

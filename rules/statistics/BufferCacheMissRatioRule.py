from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class BufferCacheMissRatioRule(BaseRule):
    RULE_ID = "RULE_MEM_002"
    NAME = "BufferCacheMissRatioRule"
    DESCRIPTION = "Shared Buffers 메모리 히트율이 낮아 실제 디스크 Read I/O 병목이 발생하는지 진단합니다."
    CATEGORY = "MEMORY"
    TARGET_NODE_TYPES = ["*"]  # 전체 노드 대상
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "CRITICAL"

    def match(self, context: RuleContext, node: dict) -> bool:
        return True

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Unknown")

        shared_hit = node.get("Shared Hit Blocks", 0)
        shared_read = node.get("Shared Read Blocks", 0)
        total_blocks = shared_hit + shared_read

        # 최소 5,000 블록 이상 읽은 대형 연산이면서, Disk Read 비율이 40% 이상인 경우
        if total_blocks >= 5000 and (shared_read / total_blocks) > 0.4:
            read_pct = (shared_read / total_blocks) * 100
            recommendations.append(
                RecommendationModel(
                    title=f"{node_type} 노드 심각한 디스크 I/O 병목 (낮은 버퍼 히트율)",
                    description=f"요청한 블록 중 {read_pct:.1f}%({shared_read:,} Blocks)를 Shared Buffers 메모리가 아닌 실제 디스크에서 읽어왔습니다.",
                    severity="CRITICAL",
                    priority=1,
                    reason="shared_buffers 설정 크기가 부족하거나 캐시가 웜업(Warm-up)되지 않아 디스크 읽기 병목이 발생했습니다.",
                    recommendation="postgresql.conf의 shared_buffers 값을 늘리거나, pg_prewarm 확장 모듈을 활용해 주요 테이블을 메모리에 상주시키십시오.",
                    recommended_sql="-- shared_buffers 설정값 점검 필요 (권장: 전체 RAM의 25%)",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low",
                )
            )

        return recommendations

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class BufferCacheMissRatioRule(BaseRule):
    RULE_ID = "RULE_MEM_002"
    NAME = "BufferCacheMissRatioRule"
    DESCRIPTION = (
        "Shared Buffers 메모리 히트율이 낮아 실제 디스크 Read I/O 병목이 발생하는지 진단합니다."
    )
    CATEGORY = "MEMORY"
    TARGET_NODE_TYPES = [
        "Seq Scan",
        "Index Scan",
        "Index Only Scan",
        "Bitmap Heap Scan",
        "CTE Scan",
        "Subquery Scan",
        "Foreign Scan",
        "ModifyTable",
        "Update",
        "Insert",
        "Delete",
    ]
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
                    title=f"{node_type} 노드 버퍼 히트율 저하로 인한 디스크 I/O 병목",
                    description=(
                        f"해당 노드에서 요청한 블록 중 {read_pct:.1f}%({shared_read:,} Blocks / "
                        f"전체 {total_blocks:,} Blocks)를 메모리(Shared Buffers)가 아닌 실제 디스크에서 직접 읽어왔습니다."
                    ),
                    severity="CRITICAL",
                    priority=1,
                    reason=(
                        "1. [주요 원인] 데이터베이스 버퍼 캐시(Shared Buffers)가 아직 웜업(Warm-up)되지 않았거나, 대용량 데이터를 처리하는 과정에서 캐시 미스(Cache Miss)가 발생하여 물리 디스크 I/O가 급증했습니다.\n"
                        "2. [구조적 요인] 전체 집계(COUNT/SUM 등) 또는 대규모 스캔 쿼리의 특성상 메모리 적재 범위를 초과하는 데이터를 읽고 있습니다."
                    ),
                    recommendation=(
                        "1. [최우선 조치] 본 쿼리가 인덱스가 불필요한 대규모 집계/분석 쿼리인지 확인하십시오. 집계 쿼리라면 인덱스 추가보다 시스템 캐시 최적화에 집중해야 합니다.\n"
                        "2. [시스템 조치] 자주 조회되는 주요 테이블이나 인덱스가 메모리에 상주할 수 있도록 postgresql.conf의 shared_buffers 값을 증설하거나, pg_prewarm 확장 모듈을 활용한 캐시 사전 적재(Warm-up)를 검토하십시오."
                    ),
                    recommended_sql=(
                        "-- 1단계: 시스템 공유 버퍼 크기 확인\n"
                        "SHOW shared_buffers;\n\n"
                        "-- 2단계: 주요 테이블 캐시 사전 적재 (pg_prewarm 확장 모듈 활용 예시)\n"
                        "-- CREATE EXTENSION IF NOT EXISTS pg_prewarm;\n"
                        "-- SELECT pg_prewarm('테이블명');"
                    ),
                    plan_node=node_type,
                    estimated_gain="High (캐시 적재 시 I/O 지연 대폭 단축)",
                    false_positive_risk="Low",
                )
            )

        return recommendations

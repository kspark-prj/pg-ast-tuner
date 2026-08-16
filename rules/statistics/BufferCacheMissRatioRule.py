from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class BufferCacheMissRatioRule(BaseRule):
    RULE_ID = "RULE_MEM_002"
    NAME = "BufferCacheMissRatioRule"
    DESCRIPTION = "Shared Buffers 메모리 히트율이 낮아 실제 디스크 Read I/O 병목이 발생하는지 진단합니다."
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
                        "1. [주요 원인] 인덱스 부재 또는 인덱스 가공(Suppression)으로 인해 대량의 데이터 블록을 물리 디스크에서 풀 스캔(Full Scan)하고 있습니다.\n"
                        "2. [시스템 요인] shared_buffers 설정 크기가 부족하거나, 데이터베이스 재기동 등으로 인해 캐시가 아직 웜업(Warm-up)되지 않았습니다."
                    ),
                    recommendation=(
                        "1. [최우선 조치] 본 리포트에 함께 제시된 인덱스 누락 및 컬럼 가공(Seq Scan/Index Scan) 튜닝 가이드를 먼저 적용하십시오. 인덱스가 활성화되면 디스크 I/O 요청량 자체가 급감하여 본 메모리 병목 현상도 자연스럽게 해결됩니다.\n"
                        "2. [보조 조치] 인덱스 최적화 이후에도 디스크 읽기 비중이 높다면, postgresql.conf의 shared_buffers 값을 늘리거나 pg_prewarm 확장 모듈을 사용하여 자주 조회되는 주요 테이블을 메모리에 강제 상주시키십시오."
                    ),
                    recommended_sql=(
                        "-- 1단계: 함께 감지된 인덱스/스캔 튜닝 가이드 조치를 먼저 실행하여 I/O 발생량 줄이기\n\n"
                        "-- 2단계: 시스템 공유 버퍼 크기 확인 및 증설 검토 (권장: 물리 RAM의 25% 내외)\n"
                        "-- SHOW shared_buffers;"
                    ),
                    plan_node=node_type,
                    estimated_gain="High (인덱스 튜닝 선행 시 극적인 I/O 절감)",
                    false_positive_risk="Low",
                )
            )

        return recommendations

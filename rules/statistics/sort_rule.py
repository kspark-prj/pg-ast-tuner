import re
from rules.base_rule import BaseRule, RuleContext
from models.recommendation import RecommendationModel

class SortRule(BaseRule):
    RULE_ID = "RULE_STAT_003"
    NAME = "SortRule"
    DESCRIPTION = "정렬(Sort) 연산 시 디스크 정렬(External Sort)이 유발되거나 LIMIT 조건 하에서 정렬 인덱스 미적용으로 대규모 정렬(Quicksort)이 감지되는지 분석합니다."
    CATEGORY = "STATISTICS"
    TARGET_NODE_TYPES = ["Sort"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Sort"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Sort")
        sort_method = node.get("Sort Method", "")

        if "external" in sort_method.lower():
            actual_kb = node.get("Sort Space Used", 0)
            recommendations.append(
                RecommendationModel(
                    title="디스크 정렬(External Sort) 감지",
                    description=f"정렬 버퍼 용량 한계로 디스크 정렬(External Sort) 진행 중. (사용 공간: {actual_kb} KB)",
                    severity="WARNING",
                    priority=2,
                    reason="데이터 정렬 요구량이 작업 메모리(work_mem) 한도를 넘어 임시 디스크 쓰기를 수행하므로 현저한 성능 병목이 발생합니다.",
                    recommendation="정렬 작업 영역 메모리(work_mem)를 세션 단위로 임시 상향하십시오.",
                    recommended_sql="SET work_mem = '64MB';",
                    plan_node=node_type,
                    estimated_gain="High",
                    false_positive_risk="Low"
                )
            )
        elif "top-n" not in sort_method.lower() and "quicksort" in sort_method.lower():
            if re.search(r"\bLIMIT\b", context.raw_query, re.IGNORECASE):
                recommendations.append(
                    RecommendationModel(
                        title="LIMIT 조건 하 퀵정렬(Quicksort) 비효율 감지",
                        description="LIMIT 조건이 존재하나 인덱스 순서 스캔 정렬이 지원되지 않아 대형 정렬 연산(Quicksort)이 과도하게 유발되고 있습니다.",
                        severity="WARNING",
                        priority=2,
                        reason="출력 개수 제한(LIMIT)이 있지만 B-Tree 인덱스의 정렬된 구조를 활용하지 못해 테이블 전체를 퀵정렬하고 있습니다.",
                        recommendation="ORDER BY 대상 정렬 조건 컬럼에 순서 정합성을 살린 최적의 인덱스를 추가하여 정렬 비용 자체를 회피(Index Scan Ordering)시키십시오.",
                        plan_node=node_type,
                        estimated_gain="High",
                        false_positive_risk="Low"
                    )
                )

        return recommendations

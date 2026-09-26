from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class MaterializeSpillRule(BaseRule):
    RULE_ID = "RULE_MEM_003"
    NAME = "MaterializeSpillRule"
    DESCRIPTION = "Materialize 노드가 중간 처리 결과를 디스크에 작성(Storage: disk)하거나 반복 덤프하여 메모리 병목을 일으키는지 진단합니다."
    CATEGORY = "MEMORY"
    TARGET_NODE_TYPES = ["Materialize"]
    SUPPORTED_PG_VERSION = "all"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "HIGH"

    def match(self, context: RuleContext, node: dict) -> bool:
        if node.get("Node Type") != "Materialize":
            return False
        storage = str(node.get("Storage", "")).lower()
        peak_mem = node.get("Peak Memory Usage", 0)
        return storage == "disk" or peak_mem > 1024

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        storage = node.get("Storage", "Memory/Disk")
        peak_mem = node.get("Peak Memory Usage", 0)

        recommendations.append(
            RecommendationModel(
                title="Materialize 노드 디스크 덤프 및 메모리 병목 감지",
                description=f"Materialize 노드의 중간 저장이 디스크/과도한 메모리(Storage: {storage}, Peak: {peak_mem}KB)에 도달했습니다.",
                severity="HIGH",
                priority=2,
                reason="Nested Loop inner side 또는 서브쿼리 결과를 반복 참조하기 위해 구직된 Materialize 노드의 데이터 양이 work_mem을 초과하여 디스크로 덤프되었습니다.",
                recommendation="Nested Loop를 Hash/Merge Join으로 대체하거나, `work_mem` 크기를 상향 조절하여 디스크 덤프를 방지하십시오.",
                recommended_sql="SET work_mem = '64MB';",
                plan_node="Materialize",
                estimated_gain="High",
                false_positive_risk="Low",
            )
        )

        return recommendations

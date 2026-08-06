from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class ExcessiveWorkMemRule(BaseRule):
    RULE_ID = "RULE_MEM_001"
    NAME = "ExcessiveWorkMemRule"
    DESCRIPTION = "단일 연산 노드에서 지나치게 높은 메모리(work_mem)를 할당하여 사용 중인지 진단합니다."
    CATEGORY = "MEMORY"
    TARGET_NODE_TYPES = ["Sort", "Hash", "Aggregate"]
    SUPPORTED_PG_VERSION = ">=12"
    DEFAULT_PRIORITY = 3
    DEFAULT_SEVERITY = "INFO"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type")

        # Memory Used가 KB 단위로 출력된다고 가정 (예: 1048576 KB = 1GB)
        peak_memory_kb = node.get("Peak Memory Usage", 0) or node.get("Memory Used", 0)

        # 단일 노드 메모리 사용량이 512MB(524,288 KB) 이상인 경우
        if peak_memory_kb > 524288:
            mb_used = peak_memory_kb / 1024
            recommendations.append(
                RecommendationModel(
                    title=f"{node_type} 연산의 과도한 메모리(work_mem) 사용 감지",
                    description=f"{node_type} 노드 수행 시 약 {mb_used:.1f} MB의 메모리를 사용했습니다.",
                    severity="INFO",
                    priority=3,
                    reason="단일 쿼리 세션의 메모리 사용량이 높아 동시 접속자가 몰릴 경우 OS Out-Of-Memory(OOM) 킬러가 작동할 위험이 있습니다.",
                    recommendation="세션 수준의 work_mem을 전역적으로 너무 높게 잡았는지 확인하고, 필요 시 해당 대용량 쿼리 세션에서만 지정하도록 수정하십시오.",
                    recommended_sql="-- 세션 내 일시 설정: SET LOCAL work_mem = '128MB';",
                    plan_node=node_type,
                    estimated_gain="Low",
                    false_positive_risk="Low",
                )
            )

        return recommendations

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class BitmapMultiIndexInefficiencyRule(BaseRule):
    RULE_ID = "RULE_SCAN_009"
    NAME = "BitmapMultiIndexInefficiencyRule"
    DESCRIPTION = "여러 개의 단일 컬럼 인덱스를 BitmapAnd/BitmapOr 연산으로 결합 스캔하여 발생하는 비트맵 생성 및 CPU 병목을 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["BitmapAnd", "BitmapOr"]
    SUPPORTED_PG_VERSION = "all"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "BitmapAnd/BitmapOr")
        sub_plans = node.get("Plans", [])

        recommendations.append(
            RecommendationModel(
                title=f"다중 비트맵 인덱스 결합 연산({node_type}) 감지",
                description=f"{len(sub_plans)}개의 독립된 인덱스 스캔 결과를 비트맵 {node_type} 연산으로 합성하고 있습니다.",
                severity="WARNING",
                priority=2,
                reason="단일 컬럼 인덱스들을 실행 시점에 메모리 비트맵으로 연합(AND/OR)함에 따라 CPU 연산 오버헤드와 비트맵 메모리 빌드 비용이 중복 발생하고 있습니다.",
                recommendation="자주 함께 조건절로 사용되는 컬럼들을 묶은 복합 인덱스(Composite Index)를 생성하여 단일 Index Scan/Bitmap Scan으로 유도하십시오.",
                recommended_sql="CREATE INDEX idx_composite ON table_name (col1, col2);",
                plan_node=node_type,
                estimated_gain="Medium to High",
                false_positive_risk="Low",
            )
        )

        return recommendations

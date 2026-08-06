
from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class HashJoinLargeBuildTableRule(BaseRule):
    RULE_ID = "RULE_JOIN_005"
    NAME = "HashJoinLargeBuildTableRule"
    DESCRIPTION = "Hash Join 시 더 작은 집합이 빌드(Build) 테이블로 선택되지 않고 대량 데이터가 해시 테이블로 구축되는지 진단합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Hash Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 3
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Hash Join"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Hash Join")
        plans = node.get("Plans", [])

        # Hash Join의 plans[1]은 Hash 노드(Build Side)
        if len(plans) >= 2:
            outer_node = plans[0]
            hash_build_node = plans[1]

            outer_rows = outer_node.get("Actual Rows", outer_node.get("Plan Rows", 0))
            build_rows = hash_build_node.get("Actual Rows", hash_build_node.get("Plan Rows", 0))

            # Build 측 rows가 Outer 측 rows보다 10배 이상 큰 경향이 있을 때 (통계정보 오류 가능성)
            if build_rows > 100000 and build_rows > (outer_rows * 10):
                recommendations.append(
                    RecommendationModel(
                        title="Hash Join 드라이빙/드리븐 테이블 선택 부적절 가능성",
                        description=f"Hash Build(내부) 테이블의 데이터 수({build_rows:,}건)가 Outer(외부) 스캔 대상({outer_rows:,}건)보다 훨씬 큽니다.",
                        severity="WARNING",
                        priority=3,
                        reason="해시 조인은 가급적 작은 테이블을 빌드(Inner) 테이블로 선정하여 메모리에 해시 테이블을 생성해야 효율적입니다. 통계 정보 불일치 등으로 인해 거대 테이블이 Build Side로 지정되었을 수 있습니다.",
                        recommendation="관련 테이블의 통계 정보(ANALYZE)를 최신화하여 옵티마이저가 올바른 해시 빌드 대상을 선택하도록 조치하십시오.",
                        recommended_sql="ANALYZE VERBOSE;",
                        plan_node=node_type,
                        estimated_gain="Medium",
                        false_positive_risk="Medium",
                    )
                )

        return recommendations

import importlib
import pkgutil
import sys
from typing import Any, Dict, List

import rules
from core.catalog import PGMetadataProvider
from rules.base_rule import BaseRule, RuleContext


class RuleEngine:
    def __init__(self, metadata_provider: PGMetadataProvider):
        self.metadata_provider = metadata_provider
        self.rules: list[BaseRule] = self._discover_rules()

    def _discover_rules(self) -> list[BaseRule]:
        """
        rules/ 패키지 하위의 모든 모듈을 동적으로 import하여
        BaseRule의 서브클래스들을 자동으로 검색(Auto Discovery) 및 인스턴스화합니다.
        """
        discovered = []

        # rules 패키지가 로드되어 있는지 확인
        pkg = sys.modules.get("rules") or importlib.import_module("rules")

        # 패키지 하위의 모든 모듈 탐색 및 로드
        for _, module_name, is_pkg in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            if not is_pkg:
                try:
                    importlib.import_module(module_name)
                except Exception as e:
                    print(
                        f"[Warning] Failed to import rule module {module_name}: {e}",
                        file=sys.stderr,
                    )

        # BaseRule의 모든 하위 클래스(서브클래스의 서브클래스 포함) 수집
        def get_all_subclasses(cls):
            subclasses = set(cls.__subclasses__())
            for sub in list(subclasses):
                subclasses.update(get_all_subclasses(sub))
            return subclasses

        rule_classes = get_all_subclasses(BaseRule)
        sorted_rule_classes = sorted(
            rule_classes,
            key=lambda cls: getattr(cls, "RULE_ID", cls.__name__) or cls.__name__,
        )
        for rule_cls in sorted_rule_classes:
            # RULE_ID가 지정되어 있고 abstract가 아닌 클래스만 인스턴스화
            if getattr(rule_cls, "RULE_ID", None) and not getattr(
                rule_cls, "__abstractmethods__", None
            ):
                try:
                    discovered.append(rule_cls())
                except Exception as e:
                    print(
                        f"[Error] Failed to instantiate rule class {rule_cls.__name__}: {e}",
                        file=sys.stderr,
                    )

        return discovered

    def analyze_node(self, context: RuleContext, node: dict[str, Any]) -> list[Any]:
        """
        주어진 실행 계획 노드에 대해 적용 가능한 모든 룰을 대조하여 추천 가이드 목록을 생성합니다.
        """
        node_recommendations = []
        node_type = node.get("Node Type")

        for rule in self.rules:
            # target node type 매칭 검증
            if "*" in rule.TARGET_NODE_TYPES or (node_type and node_type in rule.TARGET_NODE_TYPES):
                try:
                    if rule.match(context, node):
                        res = rule.analyze(context, node)

                        # 리턴된 RecommendationModel에 rule_id가 누락되어 있다면 자동 할당
                        if isinstance(res, list):
                            for r in res:
                                if hasattr(r, "rule_id") and not r.rule_id:
                                    r.rule_id = rule.RULE_ID
                            node_recommendations.extend(res)
                        elif res is not None:
                            if hasattr(res, "rule_id") and not res.rule_id:
                                res.rule_id = rule.RULE_ID
                            node_recommendations.append(res)

                except Exception as e:
                    print(
                        f"[Error] Exception raised in rule {rule.RULE_ID} ({rule.NAME}): {e}",
                        file=sys.stderr,
                    )

        return node_recommendations

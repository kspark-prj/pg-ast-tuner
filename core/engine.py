import importlib
import pkgutil
import re
import sys
from typing import Any

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

        node_line = node.get("_line_number")
        for rule in self.rules:
            # target node type 매칭 검증
            if "*" in rule.TARGET_NODE_TYPES or (node_type and node_type in rule.TARGET_NODE_TYPES):
                try:
                    if rule.match(context, node):
                        res = rule.analyze(context, node)

                        # 리턴된 RecommendationModel에 rule_id 및 line_number 매핑
                        added_recs = []
                        if isinstance(res, list):
                            added_recs = res
                        elif res is not None:
                            added_recs = [res]

                        for r in added_recs:
                            if hasattr(r, "rule_id") and not r.rule_id:
                                r.rule_id = rule.RULE_ID
                            if node_line:
                                if hasattr(r, "plan_line") and r.plan_line is None:
                                    r.plan_line = node_line
                                if hasattr(r, "plan_lines") and node_line not in r.plan_lines:
                                    r.plan_lines.append(node_line)

                        node_recommendations.extend(added_recs)

                except Exception as e:
                    print(
                        f"[Error] Exception raised in rule {rule.RULE_ID} ({rule.NAME}): {e}",
                        file=sys.stderr,
                    )

        return node_recommendations

    @staticmethod
    def deduplicate_recommendations(recs: list[Any]) -> list[Any]:
        """
        실행 계획의 상위/하위 노드 탐색 및 반복 풀 스캔 탐색으로 발생할 수 있는 동일한 룰 ID, 사유, 가이드 항목을 병합하고
        중복된 추천 메시지 및 SQL 구문을 통합 정제합니다.
        """
        seen = set()
        first_pass = []
        for r in recs:
            rule_id = getattr(r, "rule_id", "") or ""
            title = getattr(r, "title", "") or ""
            recommendation = getattr(r, "recommendation", "") or ""
            recommended_sql = getattr(r, "recommended_sql", "") or ""

            key = (rule_id, title, recommendation, recommended_sql)
            if key not in seen:
                seen.add(key)
                first_pass.append(r)
            else:
                # 이미 동일한 key가 존재하면 plan_lines만 기존 객체에 병합
                for existing_item in first_pass:
                    e_key = (
                        getattr(existing_item, "rule_id", "") or "",
                        getattr(existing_item, "title", "") or "",
                        getattr(existing_item, "recommendation", "") or "",
                        getattr(existing_item, "recommended_sql", "") or "",
                    )
                    if e_key == key:
                        if hasattr(r, "plan_lines") and hasattr(existing_item, "plan_lines"):
                            for line in r.plan_lines:
                                if line not in existing_item.plan_lines:
                                    existing_item.plan_lines.append(line)
                            existing_item.plan_lines.sort()
                            if existing_item.plan_lines:
                                existing_item.plan_line = existing_item.plan_lines[0]
                        break

        grouped = {}
        result = []
        for r in first_pass:
            rule_id = getattr(r, "rule_id", "") or ""
            severity = getattr(r, "severity", "") or ""
            reason = getattr(r, "reason", "") or ""
            recommendation = getattr(r, "recommendation", "") or ""

            group_key = (rule_id, severity, reason, recommendation)
            if group_key not in grouped:
                grouped[group_key] = r
                result.append(r)
            else:
                existing = grouped[group_key]
                if hasattr(r, "plan_lines") and hasattr(existing, "plan_lines"):
                    for line in r.plan_lines:
                        if line not in existing.plan_lines:
                            existing.plan_lines.append(line)
                    existing.plan_lines.sort()
                    if existing.plan_lines:
                        existing.plan_line = existing.plan_lines[0]
                elif getattr(r, "plan_line", None) is not None:
                    if getattr(existing, "plan_line", None) is None:
                        existing.plan_line = r.plan_line

                m_exist = re.search(r"^'([^']+)'\s+(.*)", existing.title)
                m_new = re.search(r"^'([^']+)'\s+(.*)", r.title)

                if m_exist and m_new:
                    tables = [t.strip() for t in m_exist.group(1).split(",")]
                    new_table = m_new.group(1).strip()
                    if new_table not in tables:
                        tables.append(new_table)
                    table_str = ", ".join([f"'{t}'" for t in tables])
                    existing.title = f"{table_str} {m_exist.group(2)}"

                if r.recommended_sql and existing.recommended_sql:
                    existing_sqls = [
                        s.strip()
                        for s in existing.recommended_sql.splitlines()
                        if s.strip()
                    ]
                    new_sqls = [
                        s.strip()
                        for s in r.recommended_sql.splitlines()
                        if s.strip()
                    ]
                    for s in new_sqls:
                        if s not in existing_sqls:
                            existing_sqls.append(s)
                    existing.recommended_sql = "\n".join(existing_sqls)

        return result

import re
from datetime import datetime, timedelta

import sqlglot
from sqlglot import exp

from core.parser import PGPlanAnalyzer
from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class SeqScanRule(BaseRule):
    RULE_ID = "RULE_SCAN_001"
    NAME = "SeqScanRule"
    DESCRIPTION = "테이블 풀 스캔(Seq Scan) 발생 시 인덱스 누락, 소형 테이블 여부, OR 조건, LIKE 와일드카드 및 함수 가공 여부를 종합 진단합니다."
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Seq Scan"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "CRITICAL"

    @staticmethod
    def _reorder_index_columns(query: str, columns: list[str]) -> list[str]:
        """
        인덱스 생성 시 성능 최적화를 위해 컬럼 순서를 재정렬합니다.
        1순위: 등가(=, IS NULL) 조건 컬럼 (선두 배치)
        2순위: 범위/부등호/IN/LIKE 등의 조건 컬럼 (후순위 배치)
        """
        if not columns:
            return columns

        eq_cols = []
        range_cols = []

        try:
            parsed_tree = sqlglot.parse_one(query, read="postgres")

            for col in columns:
                is_equality = False
                col_lower = col.lower().strip()

                # exp.Column 노드만 순회하도록 명시적 필터링
                for col_node in parsed_tree.find_all(exp.Column):
                    # 리터럴/값 형태가 아닌 순수 컬럼 명칭만 정밀 매칭
                    node_col_name = (
                        col_node.name.lower().strip()
                        if hasattr(col_node, "name") and col_node.name
                        else (
                            col_node.this.name.lower().strip()
                            if hasattr(col_node.this, "name")
                            else str(col_node.this).lower().strip()
                        )
                    )

                    if node_col_name == col_lower:
                        parent = col_node.parent
                        # EQ(=) 연산자이거나 IS NULL 조건인지 탐색
                        while parent and not isinstance(parent, (exp.Where, exp.Join)):
                            if isinstance(parent, exp.EQ) or (
                                isinstance(parent, exp.Is)
                                and isinstance(parent.expression, exp.Null)
                            ):
                                is_equality = True
                                break
                            parent = parent.parent
                        if is_equality:
                            break

                if is_equality:
                    eq_cols.append(col)
                else:
                    range_cols.append(col)
        except Exception:
            # AST 파싱 실패 시 정규식 기반 Fallback 처리 (리터럴값 오매칭 방지를 위한 \b 경계 강화)
            for col in columns:
                pattern = r"\b" + re.escape(col) + r"\b\s*=\s*"
                if re.search(pattern, query, re.IGNORECASE):
                    eq_cols.append(col)
                else:
                    range_cols.append(col)

        # 등가 조건 컬럼 우선 배치 후 범위/기타 조건 배치 (중복 제거 및 순서 보장)
        ordered = eq_cols + range_cols
        return list(dict.fromkeys(ordered))

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Seq Scan"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Seq Scan")
        table_name = node.get("Relation Name")
        actual_rows = node.get("Actual Rows", 0)

        if not table_name:
            return recommendations

        meta = context.metadata_provider.get_table_metadata(table_name)
        where_cols, join_group_cols, parse_success, has_or = (
            PGPlanAnalyzer.extract_columns_via_ast_ordered(context.raw_query, table_name)
        )

        # [보완] where_cols 리스트에서 리터럴 값/잘못 추출된 컬럼 식별자 2차 필터링
        if where_cols:
            where_cols = [
                c
                for c in where_cols
                if isinstance(c, str) and not c.startswith("'") and not c.startswith('"')
            ]

        if not parse_success:
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' 테이블 Seq Scan 분석 한계",
                    description=f"'{table_name}' 테이블에 Seq Scan이 감지되었으나, 정적 AST 파싱 분석에 한계가 발생했습니다.",
                    severity="INFO",
                    priority=4,
                    reason=f"정적 SQL 파서가 '{table_name}'의 조건절을 완벽하게 분석하지 못했습니다.",
                    recommendation="조건절 컬럼의 인덱스 상태를 수동 검증해주십시오.",
                    plan_node=node_type,
                    estimated_gain="N/A",
                    false_positive_risk="High (파싱 한계로 인한 오진 가능성)",
                )
            )
            return recommendations

        if not meta:
            return recommendations

        # 소규모 테이블 필터 최우선 적용
        if meta.total_rows < 1000:
            recommendations.append(
                RecommendationModel(
                    title=f"'{table_name}' 소형 테이블 풀 스캔",
                    description=f"'{table_name}' 테이블은 통계 데이터 건수({meta.total_rows}건)가 적은 소형 테이블입니다.",
                    severity="INFO",
                    priority=5,
                    reason="테이블의 크기가 너무 작아서 인덱스를 타는 것보다 풀 스캔하는 것이 오버헤드가 적습니다.",
                    recommendation="PostgreSQL 옵티마이저는 오버헤드를 막기 위해 의도적으로 풀 스캔을 선택한 것이므로 정상적인 상태입니다.",
                    plan_node=node_type,
                    estimated_gain="None",
                    false_positive_risk="Low",
                )
            )
            return recommendations

        # --- 2-A. WHERE 필터 조건절 컬럼이 존재하는 경우 ---
        if where_cols:
            if has_or:
                recommendations.append(
                    RecommendationModel(
                        title=f"'{table_name}' 조건절 내 OR 연산자 감지",
                        description=f"'{table_name}' 테이블의 조건절 내부에서 'OR' 연산자가 감지되어 Seq Scan이 유발되었습니다.",
                        severity="WARNING",
                        priority=2,
                        reason="OR 연산자를 사용하면 단일 B-Tree 인덱스가 작동하지 않을 수 있습니다.",
                        recommendation="OR 조건 양측의 각 필터 컬럼에 개별 단일 인덱스를 구축하여 옵티마이저가 Bitmap Or Scan을 타도록 유도하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 블록 외부(autocommit=True 상태)에서 수행되어야 합니다.",
                        recommended_sql="\n".join(
                            [
                                f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col} ON {table_name} ({col});"
                                for col in where_cols[:3]
                            ]
                        ),
                        plan_node=node_type,
                        estimated_gain="Medium to High",
                        false_positive_risk="Low",
                    )
                )

            # [룰 B] LIKE 전방 와일드카드 전용 매칭 진단
            front_wildcard_detected = False
            for col in where_cols:
                if PGPlanAnalyzer.check_front_wildcard_like(context.raw_query, col):
                    front_wildcard_detected = True
                    recommendations.append(
                        RecommendationModel(
                            title=f"'{table_name}' 전방 와일드카드 LIKE 사용",
                            description=f"'{table_name}' 테이블의 조건절 컬럼({col})에 전방 와일드카드(예: LIKE '%키워드') 매칭이 선언되었습니다.",
                            severity="CRITICAL",
                            priority=1,
                            reason="일반 B-Tree 인덱스는 오른쪽(후방) 매칭만 지원하므로 전방 와일드카드 사용 시 완전히 무력화됩니다.",
                            recommendation="전방/부분 일치 검색 성능 향상을 위해 'pg_trgm' 확장 모듈을 활성화하고 GIN 트라이그램 인덱스를 생성하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 블록 외부에서 수행해야 합니다.",
                            recommended_sql=f"CREATE EXTENSION IF NOT EXISTS pg_trgm;\nCREATE INDEX CONCURRENTLY idx_{table_name}_{col}_trgm ON {table_name} USING gin ({col} gin_trgm_ops);",
                            plan_node=node_type,
                            estimated_gain="High",
                            false_positive_risk="Medium (GIN 인덱스 업데이트 비용 및 디스크 공간 추가 발생)",
                        )
                    )
                    break

            suppressed_detected = False
            if not front_wildcard_detected:
                for col in where_cols:
                    ast_match = False
                    func_name = None
                    expr = f"{col}"

                    # 1. AST 기반 정밀 매칭 (exp.Column만 추적하도록 보완)
                    try:
                        parsed_tree = sqlglot.parse_one(context.clean_query, read="postgres")
                        for col_node in parsed_tree.find_all(exp.Column):
                            node_col_name = (
                                col_node.name.lower().strip()
                                if hasattr(col_node, "name") and col_node.name
                                else (
                                    col_node.this.name.lower().strip()
                                    if hasattr(col_node.this, "name")
                                    else str(col_node.this).lower().strip()
                                )
                            )
                            if node_col_name == col.lower().strip():
                                parent = col_node.parent
                                outermost_wrapper = None
                                # WHERE/JOIN/비교연산자 최상위 단계 이전의 가공 표현식(함수, 형변환) 탐색
                                while parent and not isinstance(
                                    parent,
                                    (
                                        exp.Where,
                                        exp.Join,
                                        exp.EQ,
                                        exp.GT,
                                        exp.LT,
                                        exp.GTE,
                                        exp.LTE,
                                        exp.NEQ,
                                    ),
                                ):
                                    if isinstance(parent, (exp.Func, exp.Anonymous, exp.Cast)):
                                        outermost_wrapper = parent
                                    parent = parent.parent

                                if outermost_wrapper:
                                    ast_match = True
                                    if isinstance(outermost_wrapper, exp.Cast):
                                        func_name = "CAST"
                                        expr = f"CAST({col} AS {outermost_wrapper.to.sql()})"
                                    elif isinstance(outermost_wrapper, exp.Anonymous):
                                        func_name = outermost_wrapper.name.upper()
                                        expr = f"{func_name}({col})"
                                    else:
                                        func_name = (
                                            outermost_wrapper.sql_names()[0].upper()
                                            if hasattr(outermost_wrapper, "sql_names")
                                            and outermost_wrapper.sql_names()
                                            else outermost_wrapper.__class__.__name__.upper()
                                        )
                                        expr = f"{func_name}({col})"
                                    break
                    except Exception:
                        pass

                    # 2. 정적 정규식 Fallback (AST 파싱 실패 시 대비)
                    if not ast_match:
                        pattern = r"\b(\w+)\(\s*(?:\w+\.)?" + re.escape(col) + r"\s*(?:,\s*.*?)?\)"
                        match = re.search(pattern, context.raw_query, re.IGNORECASE)
                        if match:
                            func_name = match.group(1).upper()
                            expr = f"{func_name}({col})"

                    if func_name:
                        suppressed_detected = True
                        rec_sql = ""
                        if func_name in ["UPPER", "LOWER"]:
                            real_val = PGPlanAnalyzer.extract_right_value_from_ast(
                                context.raw_query, col
                            )
                            raw_val_stripped = real_val.strip("'\"")
                            processed_val = real_val
                            if func_name == "UPPER":
                                processed_val = f"'{raw_val_stripped.upper()}'"
                            elif func_name == "LOWER":
                                processed_val = f"'{raw_val_stripped.lower()}'"

                            rec_sql = (
                                f"-- 방법 1: 조건식 우변 가공을 통한 인덱스 활용 (가장 권장)\n"
                                f"-- WHERE {col} = {processed_val};\n\n"
                                f"-- 방법 2: 함수 기반 인덱스(Functional Index) 생성 (트랜잭션 외부 수행 권장)\n"
                                f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col}_{func_name.lower()} "
                                f"ON {table_name} ({expr});"
                            )
                        elif func_name in ["DATE", "TRUNC"]:
                            extracted_date = PGPlanAnalyzer.extract_date_literal_from_ast(
                                context.raw_query, col
                            )
                            try:
                                start_dt = datetime.strptime(extracted_date, "%Y-%m-%d")
                                end_dt = start_dt + timedelta(days=1)
                                start_str = start_dt.strftime("%Y-%m-%d 00:00:00")
                                end_str = end_dt.strftime("%Y-%m-%d 00:00:00")
                            except Exception:
                                today = datetime.now()
                                tomorrow = today + timedelta(days=1)
                                start_str = today.strftime("%Y-%m-%d 00:00:00")
                                end_str = tomorrow.strftime("%Y-%m-%d 00:00:00")

                            rec_sql = (
                                f"-- 방법 1: 범위 조건식(Between)으로 우변 변경 (가장 권장)\n"
                                f"-- WHERE {col} >= '{start_str}' AND {col} < '{end_str}'\n\n"
                                f"-- 방법 2: 함수 기반 인덱스(Functional Index) 생성 (트랜잭션 외부 수행 권장)\n"
                                f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col}_date "
                                f"ON {table_name} (CAST({col} AS date));"
                            )
                        elif func_name == "CAST":
                            rec_sql = (
                                f"-- 방법 1: 좌변 형변환(Cast) 제거 또는 우변 형변환으로 변경 (가장 권장)\n"
                                f"-- WHERE {col} = ...;\n\n"
                                f"-- 방법 2: 함수 기반 인덱스(Functional Index) 생성 (트랜잭션 외부 수행 권장)\n"
                                f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col}_cast "
                                f"ON {table_name} ({expr});"
                            )
                        else:
                            rec_sql = (
                                f"-- 방법 1: 좌변 가공 회피하도록 쿼리 수정\n\n"
                                f"-- 방법 2: 함수 기반 인덱스(Functional Index) 생성 (트랜잭션 외부 수행 권장)\n"
                                f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col}_func "
                                f"ON {table_name} ({expr});"
                            )

                        recommendations.append(
                            RecommendationModel(
                                title=f"'{table_name}' 인덱스 컬럼 가공 감지",
                                description=f"'{table_name}' 테이블의 인덱스 컬럼({col})이 WHERE 조건절 내부에서 {func_name}() 가공식으로 감싸져 인덱스가 무력화(Index Suppression)되었습니다.",
                                severity="CRITICAL",
                                priority=1,
                                reason="인덱스 컬럼을 함수나 형변환으로 감싸면 옵티마이저가 인덱스 내부 엔트리를 매핑할 수 없어 전체 테이블을 다 읽어야 합니다.",
                                recommendation=f"인덱스 컬럼 원본이 가공 없이 노출되도록 조건식 우변을 변경하거나, 해당 가공식({expr})이 그대로 들어간 '함수 기반 인덱스(Functional Index)'를 설계하십시오.",
                                recommended_sql=rec_sql,
                                plan_node=node_type,
                                estimated_gain="High",
                                false_positive_risk="Low",
                            )
                        )
                        break

            if not suppressed_detected and not front_wildcard_detected:
                unindexed_cols = [col for col in where_cols if col not in meta.indexed_columns]
                best_idx, best_eval = meta.find_best_index_with_score(where_cols)

                if unindexed_cols:
                    # 등가(=) 조건 컬럼 선두 배치 재정렬
                    ordered_unindexed_cols = self._reorder_index_columns(
                        context.raw_query, unindexed_cols
                    )
                    recommendations.append(
                        RecommendationModel(
                            title=f"'{table_name}' 미인덱스 필터 컬럼 감지",
                            description=f"'{table_name}' 테이블에 전체 스캔 발생. 조건절 필수 필터 컬럼 {ordered_unindexed_cols}에 인덱스가 전혀 구성되어 있지 않습니다.",
                            severity="CRITICAL",
                            priority=1,
                            reason="WHERE 조건에 맞는 행을 찾기 위해 매번 디스크에서 모든 테이블 블록을 읽고 있습니다.",
                            recommendation="테이블 스캔 비용을 줄이기 위해 등가(=) 조건 컬럼을 선두로 구성한 복합 인덱스를 무중단(CONCURRENTLY) 방식으로 생성하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 블록 외부에서 수행해야 합니다.",
                            recommended_sql=f"CREATE INDEX CONCURRENTLY idx_{table_name}_{'_'.join(ordered_unindexed_cols)} ON {table_name} ({', '.join(ordered_unindexed_cols)});\n",
                            plan_node=node_type,
                            estimated_gain="High",
                            false_positive_risk="Low",
                        )
                    )
                elif not best_idx:
                    # 등가(=) 조건 컬럼 선두 배치 재정렬
                    ordered_where_cols = self._reorder_index_columns(context.raw_query, where_cols)
                    recommendations.append(
                        RecommendationModel(
                            title=f"'{table_name}' 인덱스 선두 컬럼 누락",
                            description=f"'{table_name}' 테이블의 조건절 컬럼 {ordered_where_cols}은 기존 복합 인덱스에 존재하지만, 복합 인덱스의 선두(첫 번째) 컬럼이 조건절에 빠져있어 인덱스 스캔을 활용하지 못하고 있습니다.",
                            severity="CRITICAL",
                            priority=1,
                            reason="B-Tree 복합 인덱스는 선행 컬럼이 조건절에 제공되지 않으면 인덱스 범위 검색이 불가능하여 풀 스캔을 수행합니다.",
                            recommendation="현재 쿼리의 필터 조건 컬럼을 맨 앞 순서로 배치하는 최적화된 신규 인덱스를 설계하여 인덱스 풀 스캔 비용을 상쇄하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 블록 외부에서 수행해야 합니다.",
                            recommended_sql=f"CREATE INDEX CONCURRENTLY idx_{table_name}_{'_'.join(ordered_where_cols)} ON {table_name} ({', '.join(ordered_where_cols)});\n",
                            plan_node=node_type,
                            estimated_gain="High",
                            false_positive_risk="Low",
                        )
                    )
                elif best_eval.get("has_skipped_prefix"):
                    # 등가(=) 조건 컬럼 선두 배치 재정렬
                    ordered_where_cols = self._reorder_index_columns(context.raw_query, where_cols)
                    recommendations.append(
                        RecommendationModel(
                            title=f"'{table_name}' 인덱스 중간 컬럼 누락(Skipped Prefix) 감지",
                            description=f"'{table_name}' 테이블에 인덱스 '{best_idx.index_name}'가 구성되어 있으나, WHERE 조건절에서 인덱스의 중간 컬럼이 누락되어 인덱스 검색 비효율이 극대화되고 결과적으로 전체 테이블 스캔이 선택되었습니다.",
                            severity="CRITICAL",
                            priority=1,
                            reason=f"인덱스 '{best_idx.index_name}'({', '.join(best_idx.columns)})의 중간 컬럼이 조건절에 제공되지 않아, 선두 컬럼 이후의 인덱스 검색 범위 제한(Index Cond) 혜택을 받지 못하고 성능 저하가 발생합니다.",
                            recommendation="누락된 인덱스 중간 컬럼을 WHERE 조건절에 포함하여 검색 효율을 보장하거나, 현재 쿼리 조건의 순서에 완전히 최적화된 신규 복합 인덱스를 생성하십시오.",
                            recommended_sql=f"CREATE INDEX CONCURRENTLY idx_{table_name}_{'_'.join(ordered_where_cols)} ON {table_name} ({', '.join(ordered_where_cols)});\n",
                            plan_node=node_type,
                            estimated_gain="High",
                            false_positive_risk="Low",
                        )
                    )
                elif meta.total_rows > 10000:
                    selectivity = (actual_rows / meta.total_rows) if meta.total_rows > 0 else 1.0
                    if selectivity < 0.1:
                        recommendations.append(
                            RecommendationModel(
                                title=f"'{table_name}' 통계 정보 왜곡 경고",
                                description=f"대용량 테이블에서 낮은 반환율({selectivity:.1%})임에도 풀 스캔이 선택되었습니다.",
                                severity="WARNING",
                                priority=2,
                                reason=f"인덱스 '{best_idx.index_name}'가 존재하고 선행 조건도 일치하나 옵티마이저가 수집 정보를 잘못 파악해 회피 중입니다.",
                                recommendation="테이블 통계 수집 데이터(Statistics)를 갱신하십시오.",
                                recommended_sql=f"ANALYZE VERBOSE {table_name};",
                                plan_node=node_type,
                                estimated_gain="Medium",
                                false_positive_risk="Low",
                            )
                        )

        # --- 2-B. WHERE 절은 없으나 JOIN ON / GROUP BY 등으로 인해 풀 스캔된 경우 ---
        elif join_group_cols:
            usable_index = meta.find_usable_index_for_cols(join_group_cols)

            if usable_index:
                recommendations.append(
                    RecommendationModel(
                        title=f"'{table_name}' 조인/그룹화 정상 풀 스캔",
                        description=f"'{table_name}' 테이블의 그룹화/조인 연산 대상 컬럼 중 선행 키가 포함된 인덱스 '{usable_index.index_name}'(구성: {usable_index.columns})가 이미 테이블에 존재합니다.",
                        severity="INFO",
                        priority=5,
                        reason="해시 조인(Hash Join) 처리를 위해 전체 메모리에 테이블 데이터를 올리거나, 소규모 그룹화(GROUP BY)를 위해 옵티마이저가 비용 기반 모델에 따라 의도적으로 풀 스캔을 선택했습니다.",
                        recommendation="의도된 최적화 상태이므로 정상적인 상태입니다. 통계 정보 왜곡 가능성을 배제하기 위해 ANALYZE를 적용해볼 수 있습니다.",
                        recommended_sql=f"ANALYZE VERBOSE {table_name};",
                        plan_node=node_type,
                        estimated_gain="None",
                        false_positive_risk="Low",
                    )
                )
            else:
                recommendations.append(
                    RecommendationModel(
                        title=f"'{table_name}' 조인/그룹화 키 인덱스 부재",
                        description=f"'{table_name}' 테이블이 조인 결합(JOIN) 또는 정렬 집계(GROUP BY) 연산을 위해 풀 스캔되었습니다. 현재 매핑 컬럼을 지원하는 적절한 인덱스가 감지되지 않습니다.",
                        severity="WARNING",
                        priority=3,
                        reason="대량의 데이터를 해시 결합하거나 정렬/중복 제거할 때 조인/그룹화 선두 키에 인덱스가 있으면 옵티마이저가 더 다양한 결합 방식(예: Merge Join)을 택할 수 있습니다.",
                        recommendation="조인 키 또는 그룹화 선두 컬럼에 인덱스 생성을 고려하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 외부에서 수행해야 합니다.",
                        recommended_sql=f"CREATE INDEX CONCURRENTLY idx_{table_name}_{join_group_cols[0]} ON {table_name} ({join_group_cols[0]});\n",
                        plan_node=node_type,
                        estimated_gain="Medium",
                        false_positive_risk="Low",
                    )
                )

        return recommendations

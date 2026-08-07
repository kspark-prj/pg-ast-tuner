import json
from typing import Optional

from pydantic import BaseModel, Field
import psycopg
from psycopg.rows import dict_row


class IndexMetadata(BaseModel):
    index_name: str
    columns: list[str]
    is_unique: bool

    def evaluate_match(self, query_cols: list[str]) -> dict:
        """
        주어진 쿼리 컬럼들에 대해 인덱스가 얼마나 효율적으로 작동하는지
        PostgreSQL B-Tree 선행 매칭(Prefix Matching) 원리를 바탕으로 평가합니다.
        """
        if not self.columns or not query_cols:
            return {
                "consecutive_prefix_matches": 0,
                "total_matched_columns": 0,
                "is_full_cover": False,
                "has_skipped_prefix": False,
            }

        query_cols_set = {c.lower().strip() for c in query_cols}
        consecutive_prefix_matches = 0
        total_matched_columns = 0

        # 1. 선행 컬럼부터 연속으로 일치하는 개수 측정 (Index Cond로 액세스 가능한 범위)
        for col in self.columns:
            if col.lower().strip() in query_cols_set:
                consecutive_prefix_matches += 1
            else:
                break

        # 2. 인덱스 전체 컬럼 중 일치하는 총 개수 측정
        for col in self.columns:
            if col.lower().strip() in query_cols_set:
                total_matched_columns += 1

        # 연속 매칭 수보다 총 매칭 수가 많다면 중간 컬럼이 건너뛰어진 것 (Index Filter 발생 위험)
        has_skipped_prefix = total_matched_columns > consecutive_prefix_matches
        is_full_cover = consecutive_prefix_matches == len(query_cols_set)

        return {
            "consecutive_prefix_matches": consecutive_prefix_matches,
            "total_matched_columns": total_matched_columns,
            "is_full_cover": is_full_cover,
            "has_skipped_prefix": has_skipped_prefix,
        }


class TableMetadata(BaseModel):
    table_name: str
    total_rows: int
    indices: list[IndexMetadata] = Field(default_factory=list)

    @property
    def indexed_columns(self) -> list[str]:
        cols: set[str] = set()
        for idx in self.indices:
            cols.update(c.lower().strip() for c in idx.columns)
        return list(cols)

    def find_usable_index_for_cols(self, query_cols: list[str]) -> IndexMetadata | None:
        """
        [기존 하위 호환성 유지]
        선행 컬럼(Prefix) 매칭이 1개 이상 유효한 인덱스 중,
        가장 연속 매칭 점수가 높은 최적의 인덱스를 반환합니다.
        """
        best_idx, _ = self.find_best_index_with_score(query_cols)
        return best_idx

    def find_best_index_with_score(self, query_cols: list[str]) -> tuple[IndexMetadata | None, dict]:
        """
        PostgreSQL B-Tree 스캔 효율성을 기준으로 모든 인덱스를 평가하여
        최적 인덱스와 해당 인덱스의 분석 스코어를 함께 반환합니다.
        """
        if not query_cols or not self.indices:
            return None, {}

        best_index: IndexMetadata | None = None
        best_eval: dict = {
            "consecutive_prefix_matches": 0,
            "total_matched_columns": 0,
            "is_full_cover": False,
            "has_skipped_prefix": False,
        }

        for idx in self.indices:
            eval_result = idx.evaluate_match(query_cols)

            # 선행 컬럼(Prefix)이 전혀 매칭되지 않으면 B-Tree 인덱스 사용 불가능
            if eval_result["consecutive_prefix_matches"] == 0:
                continue

            # 우선순위 평가 (1: 연속 매칭 수 -> 2: Unique 여부 -> 3: 전체 매칭 수)
            is_better = False
            if eval_result["consecutive_prefix_matches"] > best_eval["consecutive_prefix_matches"]:
                is_better = True
            elif eval_result["consecutive_prefix_matches"] == best_eval["consecutive_prefix_matches"]:
                if (
                    idx.is_unique
                    and not (best_index and best_index.is_unique)
                    or (eval_result["total_matched_columns"] > best_eval["total_matched_columns"])
                ):
                    is_better = True

            if is_better:
                best_index = idx
                best_eval = eval_result

        return best_index, best_eval


class PGMetadataProvider:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def get_table_metadata(self, table_name: str) -> TableMetadata:
        row_count_query = """
            SELECT c.reltuples::bigint AS row_count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s
              AND c.relkind = 'r'
              AND n.nspname = ANY(current_schemas(false));
        """
        index_query = """
            SELECT
                i.relname AS index_name,
                ix.indisunique AS is_unique,
                array_to_json(array_agg(a.attname ORDER BY k.n)) AS index_keys
            FROM pg_index ix
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, n)
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
            WHERE t.relname = %s
              AND n.nspname = ANY(current_schemas(false))
            GROUP BY i.relname, ix.indisunique;
        """
        total_rows = 0
        indices: list[IndexMetadata] = []
        target_table = table_name.lower().strip()

        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(row_count_query, (target_table,))
            row_res = cur.fetchone()
            if row_res:
                total_rows = max(0, row_res["row_count"])

            cur.execute(index_query, (target_table,))
            for row in cur.fetchall():
                raw_keys = row["index_keys"]
                columns = json.loads(raw_keys) if isinstance(raw_keys, str) else raw_keys
                columns = [c.lower() for c in columns if c] if columns else []
                indices.append(
                    IndexMetadata(
                        index_name=row["index_name"], columns=columns, is_unique=row["is_unique"]
                    )
                )

        return TableMetadata(table_name=target_table, total_rows=total_rows, indices=indices)


import json
from typing import List, Optional
from pydantic import BaseModel
import psycopg
from psycopg.rows import dict_row

class IndexMetadata(BaseModel):
    index_name: str
    columns: List[str]
    is_unique: bool

class TableMetadata(BaseModel):
    table_name: str
    total_rows: int
    indices: List[IndexMetadata]

    @property
    def indexed_columns(self) -> List[str]:
        cols = []
        for idx in self.indices:
            cols.extend(idx.columns)
        return list(set(cols))

    def find_usable_index_for_cols(self, query_cols: List[str]) -> Optional[IndexMetadata]:
        """
        제공된 질의 컬럼 중 하나라도 복합 인덱스의 첫 번째 선행 컬럼(Prefix)에
        매칭되는지 검사하여 복합/단일 인덱스 활용 가능 여부를 엄격하게 판별합니다.
        """
        if not query_cols:
            return None
        query_cols_set = {c.lower().strip() for c in query_cols}
        for idx in self.indices:
            if idx.columns:
                first_col = idx.columns[0].lower().strip()
                if first_col in query_cols_set:
                    return idx
        return None

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
        indices: List[IndexMetadata] = []
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

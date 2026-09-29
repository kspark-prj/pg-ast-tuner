import pytest
from unittest.mock import MagicMock
from core.engine import RuleEngine
from rules.base_rule import RuleContext
from rules.scan.seq_scan_rule import SeqScanRule
from rules.join.nested_loop_rule import NestedLoopRule
from core.catalog import TableMetadata, IndexMetadata

def test_rule_discovery():
    mock_provider = MagicMock()
    engine = RuleEngine(mock_provider)
    assert len(engine.rules) > 0
    rule_ids = {r.RULE_ID for r in engine.rules}
    assert "RULE_SCAN_001" in rule_ids  # SeqScanRule
    assert "RULE_SCAN_002" in rule_ids  # IndexScanRule
    assert "RULE_SCAN_003" in rule_ids  # BitmapHeapScanLossyRule
    assert "RULE_SCAN_004" in rule_ids  # IndexOnlyScanHeapFetchRule
    assert "RULE_SCAN_005" in rule_ids  # HighFilterRemovalRatioRule
    assert "RULE_SCAN_006" in rule_ids  # SubqueryScanRepetitionRule
    assert "RULE_JOIN_001" in rule_ids  # HashJoinRule
    assert "RULE_JOIN_002" in rule_ids  # NestedLoopRule
    assert "RULE_JOIN_003" in rule_ids  # MergeJoinSortRule
    assert "RULE_JOIN_004" in rule_ids  # NestedLoopHighLoopsRule
    assert "RULE_JOIN_005" in rule_ids  # HashJoinLargeBuildTableRule
    assert "RULE_JOIN_006" in rule_ids  # JoinCardinalityMisestimationRule
    assert "RULE_JOIN_007" in rule_ids  # CrossJoinRule
    assert "RULE_JOIN_008" in rule_ids  # ParallelJoinWorkerLossRule
    assert "RULE_JOIN_009" in rule_ids  # HashJoinBatchInflationRule
    assert "RULE_JOIN_010" in rule_ids  # MemoizeCacheInefficiencyRule
    assert "RULE_SCAN_009" in rule_ids  # BitmapMultiIndexInefficiencyRule
    assert "RULE_STAT_001" in rule_ids  # TempFileRule
    assert "RULE_STAT_002" in rule_ids  # ParallelWorkersRule
    assert "RULE_STAT_003" in rule_ids  # SortRule
    assert "RULE_STAT_004" in rule_ids  # DiskHashAggRule
    assert "RULE_STAT_005" in rule_ids  # ParallelWorkerSkewRule
    assert "RULE_STAT_006" in rule_ids  # JITOverheadRule
    assert "RULE_STAT_007" in rule_ids  # IncrementalSortSpillRule
    assert "RULE_STAT_008" in rule_ids  # WindowAggSortOverheadRule
    assert "RULE_SCAN_007" in rule_ids  # IndexFilterInefficiencyRule
    assert "RULE_SCAN_008" in rule_ids  # StaleVisibilityMapRule
    assert "RULE_MEM_001" in rule_ids   # ExcessiveWorkMemRule
    assert "RULE_MEM_002" in rule_ids   # BufferCacheMissRatioRule
    assert "RULE_MEM_003" in rule_ids   # MaterializeSpillRule
    assert "RULE_STR_001" in rule_ids   # CTEInliningFailureRule
    assert "RULE_STR_002" in rule_ids   # ForeignTableScanRule
    assert "RULE_STR_003" in rule_ids   # ConstraintTriggerOverheadRule
    assert "RULE_STR_004" in rule_ids   # HotUpdateFailureRule
    assert "RULE_STR_005" in rule_ids   # LockRowsOverheadRule

def test_seq_scan_small_table():
    mock_provider = MagicMock()
    mock_provider.get_table_metadata.return_value = TableMetadata(
        table_name="small_table",
        total_rows=500,
        indices=[]
    )
    
    context = RuleContext(
        raw_query="SELECT * FROM small_table WHERE id = 1",
        clean_query="SELECT * FROM small_table WHERE id = 1",
        metadata_provider=mock_provider
    )
    
    rule = SeqScanRule()
    node = {
        "Node Type": "Seq Scan",
        "Relation Name": "small_table",
        "Actual Rows": 1
    }
    
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 1
    assert recs[0].severity == "INFO"
    assert "소형 테이블" in recs[0].title

def test_nested_loop_missing_index():
    context = RuleContext(
        raw_query="SELECT * FROM t1 JOIN t2 ON t1.id = t2.t1_id",
        clean_query="SELECT * FROM t1 JOIN t2 ON t1.id = t2.t1_id",
        metadata_provider=MagicMock()
    )
    
    rule = NestedLoopRule()
    node = {
        "Node Type": "Nested Loop",
        "Plans": [
            {"Node Type": "Index Scan", "Relation Name": "t1"},
            {"Node Type": "Seq Scan", "Relation Name": "t2"}
        ]
    }
    
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 1
    assert recs[0].severity == "CRITICAL"
    assert "조인 인덱스 부재" in recs[0].title


from rules.scan.IndexFilterInefficiencyRule import IndexFilterInefficiencyRule
from rules.join.MergeJoinSortRule import MergeJoinSortRule
from rules.scan.HighFilterRemovalRatioRule import HighFilterRemovalRatioRule
from rules.join.NestedLoopHighLoopsRule import NestedLoopHighLoopsRule

def test_index_filter_inefficiency_with_skipped_prefix():
    mock_provider = MagicMock()
    mock_provider.get_table_metadata.return_value = TableMetadata(
        table_name="t1",
        total_rows=100000,
        indices=[
            IndexMetadata(index_name="idx_t1_abc", columns=["a", "b", "c"], is_unique=False)
        ]
    )
    context = RuleContext(
        raw_query="SELECT * FROM t1 WHERE a = 1 AND c = 3",
        clean_query="SELECT * FROM t1 WHERE a = 1 AND c = 3",
        metadata_provider=mock_provider
    )
    rule = IndexFilterInefficiencyRule()
    node = {
        "Node Type": "Index Scan",
        "Relation Name": "t1",
        "Index Name": "idx_t1_abc",
        "Actual Rows": 100,
        "Rows Removed by Filter": 5000
    }
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 1
    assert recs[0].severity == "WARNING"
    assert "Skipped Prefix" in recs[0].reason
    assert "idx_t1_abc" in recs[0].reason

def test_merge_join_sort_with_full_cover():
    mock_provider = MagicMock()
    mock_provider.get_table_metadata.return_value = TableMetadata(
        table_name="t1",
        total_rows=50000,
        indices=[
            IndexMetadata(index_name="idx_t1_join", columns=["join_col"], is_unique=False)
        ]
    )
    context = RuleContext(
        raw_query="SELECT * FROM t1 JOIN t2 ON t1.join_col = t2.join_col",
        clean_query="SELECT * FROM t1 JOIN t2 ON t1.join_col = t2.join_col",
        metadata_provider=mock_provider
    )
    rule = MergeJoinSortRule()
    node = {
        "Node Type": "Merge Join",
        "Plans": [
            {
                "Node Type": "Sort",
                "Plans": [
                    {"Node Type": "Seq Scan", "Relation Name": "t1"}
                ]
            },
            {"Node Type": "Index Scan", "Relation Name": "t2"}
        ]
    }
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 1
    assert "idx_t1_join" in recs[0].reason
    assert "정렬 방식" in recs[0].reason

def test_high_filter_removal_ratio_alternative_index():
    mock_provider = MagicMock()
    mock_provider.get_table_metadata.return_value = TableMetadata(
        table_name="t1",
        total_rows=100000,
        indices=[
            IndexMetadata(index_name="idx_t1_current", columns=["other_col"], is_unique=False),
            IndexMetadata(index_name="idx_t1_better", columns=["filter_col"], is_unique=False)
        ]
    )
    context = RuleContext(
        raw_query="SELECT * FROM t1 WHERE filter_col = 5",
        clean_query="SELECT * FROM t1 WHERE filter_col = 5",
        metadata_provider=mock_provider
    )
    rule = HighFilterRemovalRatioRule()
    node = {
        "Node Type": "Index Scan",
        "Relation Name": "t1",
        "Index Name": "idx_t1_current",
        "Actual Rows": 100,
        "Rows Removed by Filter": 9900
    }
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 1
    assert "idx_t1_better" in recs[0].reason
    assert "회피하여" in recs[0].reason

def test_seq_scan_skipped_prefix():
    mock_provider = MagicMock()
    mock_provider.get_table_metadata.return_value = TableMetadata(
        table_name="t1",
        total_rows=100000,
        indices=[
            IndexMetadata(index_name="idx_t1_abc", columns=["a", "b", "c"], is_unique=False)
        ]
    )
    context = RuleContext(
        raw_query="SELECT * FROM t1 WHERE a = 1 AND c = 3",
        clean_query="SELECT * FROM t1 WHERE a = 1 AND c = 3",
        metadata_provider=mock_provider
    )
    rule = SeqScanRule()
    node = {
        "Node Type": "Seq Scan",
        "Relation Name": "t1",
        "Actual Rows": 100
    }
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 1
    assert recs[0].severity == "CRITICAL"
    assert "중간 컬럼 누락" in recs[0].title
    assert "idx_t1_abc" in recs[0].reason

def test_query_history_saving_and_loading(tmp_path):
    import os
    from config import HistoryManager

    # Override the history file path to a temp path for testing
    orig_file = HistoryManager.HISTORY_FILE
    temp_file = os.path.join(tmp_path, "temp_query_history.json")
    HistoryManager.HISTORY_FILE = temp_file

    try:
        # Load initially - should be empty
        history = HistoryManager.load_history()
        assert len(history) == 0

        # Save a query
        test_query = "SELECT * FROM test_table WHERE id = 1"
        HistoryManager.save_query(test_query)

        # Load again - should contain 1 item
        history = HistoryManager.load_history()
        assert len(history) == 1
        assert history[0]["query"] == test_query

        # Save a duplicate - should move to the top and keep length 1
        HistoryManager.save_query(test_query)
        history = HistoryManager.load_history()
        assert len(history) == 1

        # Save another query
        another_query = "SELECT name FROM users"
        HistoryManager.save_query(another_query)
        history = HistoryManager.load_history()
        assert len(history) == 2
        assert history[0]["query"] == another_query
        assert history[1]["query"] == test_query
    finally:
        # Restore original path
        HistoryManager.HISTORY_FILE = orig_file


def test_cross_join_rule_detected():
    from rules.join.CrossJoinRule import CrossJoinRule
    context = RuleContext(
        raw_query="SELECT * FROM t1 CROSS JOIN t2",
        clean_query="SELECT * FROM t1 CROSS JOIN t2",
        metadata_provider=MagicMock()
    )
    rule = CrossJoinRule()
    node = {
        "Node Type": "Nested Loop",
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "t1", "Alias": "t1"},
            {"Node Type": "Seq Scan", "Relation Name": "t2", "Alias": "t2"}
        ]
    }
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 1
    assert "카티시안 곱" in recs[0].title


def test_cross_join_rule_parameterized_nested_loop():
    from rules.join.CrossJoinRule import CrossJoinRule
    context = RuleContext(
        raw_query="SELECT * FROM routines r JOIN routine_schedules s ON r.routine_id = s.routine_id",
        clean_query="SELECT * FROM routines r JOIN routine_schedules s ON r.routine_id = s.routine_id",
        metadata_provider=MagicMock()
    )
    rule = CrossJoinRule()
    node = {
        "Node Type": "Nested Loop",
        "Plans": [
            {
                "Node Type": "Index Scan",
                "Relation Name": "routines",
                "Alias": "r",
                "Index Cond": "(user_id = 1)"
            },
            {
                "Node Type": "Bitmap Heap Scan",
                "Relation Name": "routine_schedules",
                "Alias": "s",
                "Recheck Cond": "(r.routine_id = routine_id)",
                "Filter": "((day_of_week)::numeric = EXTRACT(dow FROM CURRENT_DATE))",
                "Plans": [
                    {
                        "Node Type": "Bitmap Index Scan",
                        "Index Name": "uq_routine_day",
                        "Index Cond": "(routine_id = r.routine_id)"
                    }
                ]
            }
        ]
    }
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 0


def test_cross_join_rule_index_recheck_cond_exception():
    from rules.join.CrossJoinRule import CrossJoinRule
    context = RuleContext(
        raw_query="SELECT * FROM target_categories tc LEFT JOIN products p ON tc.category_id = p.category_id",
        clean_query="SELECT * FROM target_categories tc LEFT JOIN products p ON tc.category_id = p.category_id",
        metadata_provider=MagicMock()
    )
    rule = CrossJoinRule()
    node = {
        "Node Type": "Nested Loop",
        "Plans": [
            {"Node Type": "Values Scan", "Alias": "*VALUES*"},
            {
                "Node Type": "Index Scan",
                "Relation Name": "products",
                "Alias": "p",
                "Index Cond": "(category_id = \"*VALUES*\".column1)"
            }
        ]
    }
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 0  # Should NOT flag Cartesian product


def test_cross_join_rule_constrained_row_count_exception():
    from rules.join.CrossJoinRule import CrossJoinRule
    context = RuleContext(
        raw_query="SELECT * FROM t1 JOIN t2 ON t1.id = t2.t1_id",
        clean_query="SELECT * FROM t1 JOIN t2 ON t1.id = t2.t1_id",
        metadata_provider=MagicMock()
    )
    rule = CrossJoinRule()
    node = {
        "Node Type": "Nested Loop",
        "Actual Rows": 10,
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "t1", "Alias": "t1", "Actual Rows": 10},
            {"Node Type": "Index Scan", "Relation Name": "t2", "Alias": "t2", "Actual Rows": 1, "Actual Loops": 10, "Index Cond": "(t1_id = t1.id)"}
        ]
    }
    assert rule.match(context, node) is True
    recs = rule.analyze(context, node)
    assert len(recs) == 0  # Constrained row count (1 row per loop) -> No false positive


def test_index_scan_and_seq_scan_rule_no_meta_safe():
    from rules.scan.index_scan_rule import IndexScanRule
    from rules.scan.seq_scan_rule import SeqScanRule

    mock_provider = MagicMock()
    mock_provider.get_table_metadata.return_value = None

    context = RuleContext(
        raw_query="SELECT * FROM missing_table WHERE id = 1",
        clean_query="SELECT * FROM missing_table WHERE id = 1",
        metadata_provider=mock_provider
    )

    # 1. IndexScanRule
    idx_rule = IndexScanRule()
    idx_node = {
        "Node Type": "Index Scan",
        "Relation Name": "missing_table",
        "Actual Rows": 1000
    }
    assert idx_rule.match(context, idx_node) is True
    assert idx_rule.analyze(context, idx_node) == []

    # 2. SeqScanRule
    seq_rule = SeqScanRule()
    seq_node = {
        "Node Type": "Seq Scan",
        "Relation Name": "missing_table",
        "Actual Rows": 1000
    }
    assert seq_rule.match(context, seq_node) is True
    # Should not raise AttributeError when meta is None
    assert seq_rule.analyze(context, seq_node) == []


def test_stale_visibility_map_rule_selective_filter():
    from rules.scan.StaleVisibilityMapRule import StaleVisibilityMapRule

    rule = StaleVisibilityMapRule()

    # Case A: Selective filter on dense table. Total blocks is 2000, actual rows is 50.
    # But Rows Removed by Filter is 199,950. Total live rows is 200,000.
    # Live row density = 200,000 / 2000 = 100 rows/block >= 0.1. Should NOT trigger.
    node_dense = {
        "Node Type": "Seq Scan",
        "Relation Name": "dense_table",
        "Shared Hit Blocks": 1500,
        "Shared Read Blocks": 500,
        "Actual Rows": 50,
        "Rows Removed by Filter": 199950
    }
    context = RuleContext(
        raw_query="SELECT * FROM dense_table WHERE status = 'SPECIAL'",
        clean_query="SELECT * FROM dense_table WHERE status = 'SPECIAL'",
        metadata_provider=MagicMock()
    )
    assert rule.match(context, node_dense) is True
    assert rule.analyze(context, node_dense) == []

    # Case B: True bloated table / dead tuples. Total blocks is 2000, actual rows is 50,
    # Rows Removed by Filter is only 50 (i.e. only 100 live rows total in 2000 blocks).
    # Live row density = 100 / 2000 = 0.05 < 0.1. Should trigger!
    node_bloated = {
        "Node Type": "Seq Scan",
        "Relation Name": "bloated_table",
        "Shared Hit Blocks": 1500,
        "Shared Read Blocks": 500,
        "Actual Rows": 50,
        "Rows Removed by Filter": 50
    }
    recs = rule.analyze(context, node_bloated)
    assert len(recs) == 1
    assert "블로팅(Bloat)" in recs[0].title


def test_new_rules_execution():
    from rules.join.MemoizeCacheInefficiencyRule import MemoizeCacheInefficiencyRule
    from rules.scan.BitmapMultiIndexInefficiencyRule import BitmapMultiIndexInefficiencyRule
    from rules.memory.MaterializeSpillRule import MaterializeSpillRule
    from rules.statistics.WindowAggSortOverheadRule import WindowAggSortOverheadRule
    from rules.structural.LockRowsOverheadRule import LockRowsOverheadRule

    ctx = RuleContext("SELECT 1", "SELECT 1", MagicMock())

    # 1. Memoize
    memoize_rule = MemoizeCacheInefficiencyRule()
    m_node = {"Node Type": "Memoize", "Cache Hits": 10, "Cache Misses": 50, "Cache Overflows": 2}
    assert memoize_rule.match(ctx, m_node) is True
    recs = memoize_rule.analyze(ctx, m_node)
    assert len(recs) == 1
    assert recs[0].rule_id == "RULE_JOIN_010" or "Memoize" in recs[0].title

    # 2. BitmapMultiIndex
    bitmap_rule = BitmapMultiIndexInefficiencyRule()
    b_node = {"Node Type": "BitmapAnd", "Plans": [{"Node Type": "Bitmap Index Scan"}, {"Node Type": "Bitmap Index Scan"}]}
    assert bitmap_rule.match(ctx, b_node) is True
    recs = bitmap_rule.analyze(ctx, b_node)
    assert len(recs) == 1
    assert "BitmapAnd" in recs[0].title

    # 3. Materialize
    mat_rule = MaterializeSpillRule()
    mat_node = {"Node Type": "Materialize", "Storage": "disk", "Peak Memory Usage": 2048}
    assert mat_rule.match(ctx, mat_node) is True
    recs = mat_rule.analyze(ctx, mat_node)
    assert len(recs) == 1
    assert "Materialize" in recs[0].title

    # 4. WindowAgg
    w_rule = WindowAggSortOverheadRule()
    w_node = {"Node Type": "WindowAgg", "Plans": [{"Node Type": "Sort"}]}
    assert w_rule.match(ctx, w_node) is True
    recs = w_rule.analyze(ctx, w_node)
    assert len(recs) == 1
    assert "WindowAgg" in recs[0].title

    # 5. LockRows
    lock_rule = LockRowsOverheadRule()
    lock_node = {"Node Type": "LockRows", "Plans": [{"Node Type": "Seq Scan"}]}
    assert lock_rule.match(ctx, lock_node) is True
    recs = lock_rule.analyze(ctx, lock_node)
    assert len(recs) == 1
    assert recs[0].severity == "CRITICAL"


def test_recommendation_deduplication():
    from models.recommendation import RecommendationModel

    rec1 = RecommendationModel(
        rule_id="RULE_STAT_001",
        title="디스크 임시 파일 쓰기 발생",
        description="임시 파일 쓰기가 발생했습니다 (15176 KB).",
        severity="WARNING",
        priority=2,
        reason="work_mem 부족으로 임시 파일 생성",
        recommendation="work_mem 상향 조정",
        recommended_sql="SET work_mem = '64MB';",
        plan_node="Hash Join",
    )
    rec2 = RecommendationModel(
        rule_id="RULE_STAT_001",
        title="디스크 임시 파일 쓰기 발생",
        description="임시 파일 쓰기가 발생했습니다 (5728 KB).",
        severity="WARNING",
        priority=2,
        reason="work_mem 부족으로 임시 파일 생성",
        recommendation="work_mem 상향 조정",
        recommended_sql="SET work_mem = '64MB';",
        plan_node="Hash",
    )

    recs = [rec1, rec2]
    deduped = RuleEngine.deduplicate_recommendations(recs)
    assert len(deduped) == 1
    assert deduped[0].rule_id == "RULE_STAT_001"


def test_assign_line_numbers_and_formatting():
    from core.parser import PGPlanAnalyzer

    raw_explain = (
        "Hash Join  (cost=1.23..4.56 rows=100 width=32)\n"
        "  Hash Cond: (t1.id = t2.t1_id)\n"
        "  ->  Seq Scan on t1  (cost=0.00..2.20 rows=100 width=16)\n"
        "  ->  Hash  (cost=1.00..1.00 rows=10 width=16)\n"
        "        ->  Seq Scan on t2  (cost=0.00..1.00 rows=10 width=16)"
    )

    root_plan = {
        "Node Type": "Hash Join",
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "t1"},
            {
                "Node Type": "Hash",
                "Plans": [
                    {"Node Type": "Seq Scan", "Relation Name": "t2"}
                ]
            }
        ]
    }

    PGPlanAnalyzer.assign_line_numbers(root_plan, raw_explain)

    assert root_plan["Plans"][0]["_line_number"] == 3
    assert root_plan["Plans"][1]["Plans"][0]["_line_number"] == 1
    assert root_plan["Plans"][1]["_line_number"] == 2
    assert root_plan["_line_number"] == 4

    formatted = PGPlanAnalyzer.format_explain_with_line_numbers(raw_explain, root_plan)
    assert "SEQ  4 | Hash Join" in formatted
    assert "SEQ  3 |   ->  Seq Scan on t1" in formatted
    assert "SEQ  2 |   ->  Hash" in formatted
    assert "SEQ  1 |         ->  Seq Scan on t2" in formatted


def test_recommendation_line_mapping_integration():
    from core.parser import PGPlanAnalyzer
    from core.engine import RuleEngine

    mock_provider = MagicMock()
    mock_provider.get_table_metadata.return_value = TableMetadata(
        table_name="t1",
        total_rows=100000,
        indices=[]
    )

    raw_explain = (
        "Hash Join  (cost=1.23..4.56 rows=100 width=32)\n"
        "  Hash Cond: (t1.id = t2.t1_id)\n"
        "  ->  Seq Scan on t1  (cost=0.00..2.20 rows=100 width=16)\n"
        "  ->  Hash  (cost=1.00..1.00 rows=10 width=16)\n"
        "        ->  Seq Scan on t2  (cost=0.00..1.00 rows=10 width=16)"
    )

    root_plan = {
        "Node Type": "Hash Join",
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "t1", "Actual Rows": 100},
            {
                "Node Type": "Hash",
                "Plans": [
                    {"Node Type": "Seq Scan", "Relation Name": "t2", "Actual Rows": 100}
                ]
            }
        ]
    }

    PGPlanAnalyzer.assign_line_numbers(root_plan, raw_explain)

    context = RuleContext(
        raw_query="SELECT * FROM t1 JOIN t2 ON t1.id = t2.t1_id",
        clean_query="SELECT * FROM t1 JOIN t2 ON t1.id = t2.t1_id",
        metadata_provider=mock_provider
    )

    engine = RuleEngine(mock_provider)
    seq_node = root_plan["Plans"][0]
    recs = engine.analyze_node(context, seq_node)

    assert len(recs) > 0
    assert recs[0].plan_line == 3
    assert 3 in recs[0].plan_lines


def test_generate_execution_process_report():
    from core.parser import PGPlanAnalyzer

    raw_explain = (
        "Hash Join  (cost=1.23..4.56 rows=100 width=32)\n"
        "  Hash Cond: (t1.id = t2.t1_id)\n"
        "  ->  Seq Scan on t1  (cost=0.00..2.20 rows=100 width=16)\n"
        "  ->  Hash  (cost=1.00..1.00 rows=10 width=16)\n"
        "        ->  Seq Scan on t2  (cost=0.00..1.00 rows=10 width=16)"
    )

    root_plan = {
        "Node Type": "Hash Join",
        "Actual Startup Time": 0.035,
        "Actual Total Time": 0.050,
        "Actual Rows": 100,
        "Actual Loops": 1,
        "Plans": [
            {
                "Node Type": "Seq Scan",
                "Relation Name": "t1",
                "Actual Startup Time": 0.015,
                "Actual Total Time": 0.025,
                "Actual Rows": 100,
                "Actual Loops": 1,
            },
            {
                "Node Type": "Hash",
                "Actual Startup Time": 0.020,
                "Actual Total Time": 0.020,
                "Actual Rows": 10,
                "Actual Loops": 1,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Relation Name": "t2",
                        "Actual Startup Time": 0.010,
                        "Actual Total Time": 0.018,
                        "Actual Rows": 10,
                        "Actual Loops": 1,
                    }
                ],
            },
        ],
    }

    PGPlanAnalyzer.assign_line_numbers(root_plan, raw_explain)
    report = PGPlanAnalyzer.generate_execution_process_report(root_plan)

    assert "📊 [단계별 실행 과정 분석 리포트 (실행 순서 기준)]" in report
    assert "• [SEQ 1] Seq Scan (테이블: t2)" in report
    assert "• [SEQ 2] Hash" in report
    assert "• [SEQ 3] Seq Scan (테이블: t1)" in report
    assert "• [SEQ 4] Hash Join" in report
    assert "처리 실적:" in report








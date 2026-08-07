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
    assert "RULE_STAT_001" in rule_ids  # TempFileRule
    assert "RULE_STAT_002" in rule_ids  # ParallelWorkersRule
    assert "RULE_STAT_003" in rule_ids  # SortRule
    assert "RULE_STAT_004" in rule_ids  # DiskHashAggRule
    assert "RULE_STAT_005" in rule_ids  # ParallelWorkerSkewRule
    assert "RULE_STAT_006" in rule_ids  # JITOverheadRule
    assert "RULE_STAT_007" in rule_ids  # IncrementalSortSpillRule

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


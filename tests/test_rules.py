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

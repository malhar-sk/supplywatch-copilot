"""Eval round 2 helpers: agent golden set shape, tool precision/recall maths, judge parsing,
retrieval-ablation rank metrics, variance stats. Pure functions; no LLM calls."""
from evals.agent_eval import aggregate_tools, load_golden, parse_judgement, tool_scores
from evals.retrieval_ablation import rank_metrics
from evals.variance_report import spread

TOOLS = {"get_material_signal", "get_material_history", "supplier_exposure", "search_documents", "write_risk_report"}


def test_agent_golden_has_20_labelled_synthetic_items_with_required_mix():
    items = load_golden()
    assert len(items) == 20
    assert len({i["id"] for i in items}) == 20
    types = {t: sum(1 for i in items if i["type"] == t) for t in {i["type"] for i in items}}
    assert set(types) == {"single_tool", "multi_tool", "needs_documents", "unanswerable"}
    assert all(n >= 3 for n in types.values())
    for i in items:
        assert i["synthetic"] is True and i["label"].startswith("SYNTHETIC")
        assert set(i["expected_tools"]) <= TOOLS
        assert i["reference"].strip()
        if i["type"] == "multi_tool":
            assert len(set(i["expected_tools"])) >= 2


def test_tool_scores_precision_recall():
    s = tool_scores(["get_material_signal", "search_documents", "search_documents"],
                    ["get_material_signal", "supplier_exposure"])
    assert (s["tp"], s["n_called"], s["n_expected"]) == (1, 2, 2)
    assert s["precision"] == 0.5 and s["recall"] == 0.5 and not s["exact"]
    # nothing expected, nothing called: perfect precision, recall undefined
    assert tool_scores([], []) == {"tp": 0, "n_called": 0, "n_expected": 0, "precision": 1.0, "recall": None,
                                   "exact": True}
    assert tool_scores(["search_documents"], [])["precision"] == 0.0


def test_aggregate_tools_micro_vs_macro():
    rows = [{"tool_scores": tool_scores(["a"], ["a", "b"])},     # p=1, r=.5
            {"tool_scores": tool_scores(["a", "c"], ["a"])},     # p=.5, r=1
            {"tool_scores": tool_scores([], [])}]                # p=1, r=None
    agg = aggregate_tools(rows)
    assert agg["micro_precision"] == round(2 / 3, 4)
    assert agg["micro_recall"] == round(2 / 3, 4)
    assert agg["macro_precision"] == round((1 + 0.5 + 1) / 3, 4)
    assert agg["macro_recall"] == 0.75
    assert agg["exact_set_match"] == round(1 / 3, 4)


def test_parse_judgement():
    assert parse_judgement('{"correct": 1, "reason": "ok"}') == {"correct": 1, "reason": "ok"}
    assert parse_judgement('```json\n{"correct": 0, "reason": "wrong score"}\n```')["correct"] == 0
    assert parse_judgement("I think it's fine")["correct"] is None
    assert parse_judgement('{"correct": 7}')["correct"] is None


def test_rank_metrics():
    docs = ["x", "y", "x", "z", "w", "e", "q"]
    assert rank_metrics(docs, ["e"]) == {"hit_at_5": 0.0, "hit_at_10": 1.0, "rr_at_10": 1 / 6}
    assert rank_metrics(docs, ["y", "e"]) == {"hit_at_5": 1.0, "hit_at_10": 1.0, "rr_at_10": 0.5}
    assert rank_metrics(docs, ["nope"]) == {"hit_at_5": 0.0, "hit_at_10": 0.0, "rr_at_10": 0.0}


def test_spread():
    s = spread([0.89, 0.87, 0.91])
    assert s == {"n": 3, "mean": 0.89, "min": 0.87, "max": 0.91, "range": 0.04, "half_range": 0.02}
    assert spread([None, 0.5])["n"] == 1

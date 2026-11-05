"""Summarize analysis_perf JSONL (nested stage timings are inclusive)."""
import argparse
from collections import Counter, defaultdict
import json
import math
import statistics

def summarize(lines):
    grouped = defaultdict(list)
    for line in lines:
        try:
            event = json.loads(line[line.index("{"):])
            if event.get("event") == "analysis_perf":
                grouped[event["market"]].append(event)
        except (ValueError, KeyError, TypeError):
            continue
    result = {}
    for market, events in grouped.items():
        stages = defaultdict(list)
        for event in events:
            stages[event["stage"]].append(event)
        by_stage = {}
        for stage, entries in stages.items():
            values = sorted(e["elapsed_ms"] for e in entries)
            by_stage[stage] = {"count": len(values), "median_ms": round(statistics.median(values), 3), "p95_ms": round(values[math.ceil(len(values)*.95)-1], 3), "cumulative_ms": round(sum(values), 3)}
        totals = stages.get("backend_total", [])
        result[market] = {"runs_by_trigger": dict(Counter(e["trigger"] for e in totals)), "cache_lookups": dict(Counter(e["cache_state"] for e in stages.get("cache_lookup", []))), "queries": sum(e.get("query_count", 0) for e in events), "rows": sum(e.get("row_count", 0) for e in events), "stages": by_stage, "top_cumulative_inclusive": sorted(by_stage, key=lambda k: by_stage[k]["cumulative_ms"], reverse=True)[:5], "top_individual": [{"stage": e["stage"], "elapsed_ms": round(e["elapsed_ms"],3)} for e in sorted(events, key=lambda x:x["elapsed_ms"], reverse=True)[:5]]}
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logfile", help="JSONL or captured stderr/journal text")
    args = parser.parse_args()
    with open(args.logfile, encoding="utf-8") as source:
        print(json.dumps(summarize(source), indent=2, sort_keys=True))

if __name__ == "__main__":
    main()

import json
from contextlib import contextmanager
from src.invoice_app.services import analysis_perf as perf
from scripts.analyze_analysis_perf import summarize

class Fake:
    def __init__(self): self.calls = 0
    def execute(self, query):
        self.calls += 1
        return self
    def mappings(self): return iter([1, 2])

def test_opt_in_and_counts(monkeypatch, capsys):
    monkeypatch.delenv("INVOICEGATHER_PERF_LOG", raising=False)
    with perf.run("MY"):
        assert perf.read_rows(Fake(), object(), lambda x:x, "test") == (1,2)
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("INVOICEGATHER_PERF_LOG", "1")
    engine = Fake()
    with perf.run("SG"):
        perf.update(cache_state="MISS", trigger="APPLY")
        assert perf.read_rows(engine, object(), lambda x:x, "test") == (1,2)
    events = [json.loads(x) for x in capsys.readouterr().err.splitlines()]
    assert engine.calls == 1
    assert len({e["run_id"] for e in events}) == 1
    assert all(e["market"] == "SG" and e["trigger"] == "APPLY" for e in events)
    summary = summarize(json.dumps(e) for e in events)["SG"]
    assert summary["queries"] == 1 and summary["rows"] == 2
    assert summary["runs_by_trigger"] == {"APPLY":1}

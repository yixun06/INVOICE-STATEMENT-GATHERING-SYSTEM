from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import pytest
from src.invoice_app.services.financial_fact_poc import OrderFact
from src.invoice_app.services.incremental_financial_fact import calculate_targeted_facts, publish_test_facts
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG


class EmptyResult:
    def mappings(self):
        return ()


class CapturingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return EmptyResult()


def test_empty_scope_never_executes_a_query():
    connection = CapturingConnection()
    assert calculate_targeted_facts(connection, SHOPEE_MY, ()) == ()
    assert connection.statements == []


def test_queries_are_scoped_to_explicit_market_and_order():
    for market in (SHOPEE_MY, SHOPEE_SG):
        connection = CapturingConnection()
        assert calculate_targeted_facts(connection, market, ('synthetic-order',)) == ()
        assert len(connection.statements) == 4
        for statement in connection.statements:
            compiled = statement.compile()
            assert ['synthetic-order'] in compiled.params.values()
            assert market.key.value in compiled.params.values() or market.persisted_platform in compiled.params.values()


def test_publication_rejects_foreign_market_and_wrong_database_without_sql():
    connection = CapturingConnection()
    connection.in_transaction = lambda: True
    connection.engine = SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=5433, database='invoicegather_perf_scale'))
    fact = OrderFact('shopee_my', 'sample', date(2026, 1, 1), 'MISSING_STATEMENT', None, None,
                     0, Decimal('0'), Decimal('0'), Decimal('0'), Decimal('0'))
    with pytest.raises(ValueError, match='foreign-market'):
        publish_test_facts(connection, SHOPEE_MY, [replace(fact, market='shopee_sg')], [], revision=1)
    with pytest.raises(RuntimeError, match='non-disposable'):
        publish_test_facts(connection, SHOPEE_MY, [fact], [], revision=1)
    assert not connection.statements


def test_publication_rejects_overlapping_upsert_and_delete():
    connection = CapturingConnection()
    fact = OrderFact('shopee_my', 'sample', date(2026, 1, 1), 'MISSING_STATEMENT', None, None,
                     0, Decimal('0'), Decimal('0'), Decimal('0'), Decimal('0'))
    with pytest.raises(ValueError, match='conflicting'):
        publish_test_facts(connection, SHOPEE_MY, [fact], ['sample'], revision=1)
    assert not connection.statements


def test_server_publisher_rejects_unverified_direct_revision(monkeypatch):
    monkeypatch.setenv('INVOICEGATHER_FACT_SERVER_ENABLED', '1')
    monkeypatch.delenv('INVOICEGATHER_ATOMICITY_TEST_MODE', raising=False)
    for key, value in {'HOST': '127.0.0.1', 'PORT': '55434', 'DATABASE': 'invoicegather_uat2_import'}.items():
        monkeypatch.setenv('INVOICEGATHER_PG_' + key, value)
    connection = CapturingConnection()
    connection.in_transaction = lambda: True
    connection.engine = SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=55434, database='invoicegather_uat2_import'))
    with pytest.raises(RuntimeError, match='verified sync coverage'):
        publish_test_facts(connection, SHOPEE_MY, [], [], revision=1)
    assert not connection.statements


def test_server_publisher_rechecks_locked_database_revision(monkeypatch):
    from src.invoice_app.services.incremental_financial_fact import _VERIFIED_SYNC_PUBLICATION
    monkeypatch.setenv('INVOICEGATHER_FACT_SERVER_ENABLED', '1')
    monkeypatch.delenv('INVOICEGATHER_ATOMICITY_TEST_MODE', raising=False)
    for key, value in {'HOST': '127.0.0.1', 'PORT': '55434', 'DATABASE': 'invoicegather_uat2_import'}.items():
        monkeypatch.setenv('INVOICEGATHER_PG_' + key, value)
    class Connection:
        engine = SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=55434, database='invoicegather_uat2_import'))
        def __init__(self): self.sql = []
        def in_transaction(self): return True
        def execute(self, statement, params=None):
            sql = str(statement); self.sql.append(sql)
            if 'current_database()' in sql:
                return SimpleNamespace(one=lambda: ('invoicegather_uat2_import', 'public'))
            if 'analysis_source_revision' in sql:
                return SimpleNamespace(scalar_one=lambda: 7)
            if 'analysis_financial_fact_revision' in sql:
                return SimpleNamespace(scalar_one=lambda: 6)
            raise AssertionError('unexpected SQL')
    connection = Connection()
    with pytest.raises(RuntimeError, match='does not match locked source'):
        publish_test_facts(connection, SHOPEE_MY, [], [], revision=8, _authorization=_VERIFIED_SYNC_PUBLICATION)
    assert not any(sql.startswith(('INSERT', 'UPDATE', 'DELETE')) for sql in connection.sql)

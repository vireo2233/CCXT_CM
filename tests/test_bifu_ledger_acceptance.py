from examples.inspect_bifu_ledger import inspect_ledger


async def test_ledger_inspection_reports_shape_without_sensitive_values():
    class FakeExchange:
        last_json_response = {"next_cursor": "private-cursor"}

        async def fetch_ledger(self, code, limit):
            assert code == "USDT"
            assert limit == 2
            return [
                {
                    "id": "private-ticket",
                    "timestamp": 1700000000000,
                    "datetime": "2023-11-14T22:13:20.000Z",
                    "direction": "in",
                    "account": "SPOT:0",
                    "referenceId": None,
                    "referenceAccount": None,
                    "type": "deposit",
                    "currency": "USDT",
                    "amount": 100.0,
                    "before": None,
                    "after": None,
                    "status": None,
                    "fee": None,
                    "info": {"ticket": "private-ticket", "amount": "100"},
                }
            ]

    result = await inspect_ledger("USDT", 2, exchange=FakeExchange(), retry_delay=0)

    assert result == {
        "environment": "test",
        "currency_filter": "USDT",
        "sample_count": 1,
        "directions": ["in"],
        "types": ["deposit"],
        "currencies": ["USDT"],
        "standard_fields_present": True,
        "next_page_available": True,
        "identifiers_and_amounts_redacted": True,
    }
    assert "private-ticket" not in repr(result)
    assert "100" not in repr(result)


async def test_ledger_inspection_distinguishes_empty_page_from_failure():
    class FakeExchange:
        last_json_response = {"flows": [], "next_cursor": ""}

        async def fetch_ledger(self, code, limit):
            return []

    result = await inspect_ledger(exchange=FakeExchange(), retry_delay=0)

    assert result["sample_count"] == 0
    assert result["standard_fields_present"] is None
    assert result["next_page_available"] is False

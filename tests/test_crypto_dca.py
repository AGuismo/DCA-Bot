import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import crypto_dca
import portfolio_logger


class DcaGhostfolioBoundaryTests(unittest.TestCase):
    def test_successful_exchange_fill_is_persisted_with_its_pair(self):
        execution_timestamp = int(
            datetime(2026, 7, 13, 17, 0, 39, tzinfo=timezone.utc).timestamp()
        )
        exchange_responses = [
            {"error": 0, "result": {"id": "exchange-order-id"}},
            {
                "error": 0,
                "result": {
                    "filled": 698.24,
                    "history": [{"amount": 698.24, "rate": 2080750.83}],
                    "ts": execution_timestamp,
                },
            },
        ]

        with (
            patch.dict(
                os.environ,
                {"PORTFOLIO_ACCOUNT_MAP": json.dumps({"BTC": "btc-account"})},
                clear=False,
            ),
            patch.object(
                crypto_dca, "bitkub_request", side_effect=exchange_responses
            ),
            patch.object(crypto_dca, "get_thb_usd_rate", return_value=0.03),
            patch.object(crypto_dca.time, "sleep"),
            patch.object(crypto_dca, "update_gist_log") as update_gist_log,
            patch.object(crypto_dca, "send_discord_alert") as send_discord_alert,
            patch.object(portfolio_logger, "log_to_ghostfolio", return_value=True) as log,
        ):
            crypto_dca.execute_trade("BTC_THB", 698.24)

        log.assert_called_once()
        trade_data, symbol, account_id = log.call_args.args[:3]
        self.assertEqual(symbol, "BTC")
        self.assertEqual(account_id, "btc-account")
        self.assertEqual(log.call_args.kwargs["exchange_pair"], "BTC_THB")
        self.assertEqual(trade_data["order_id"], "exchange-order-id")
        self.assertGreater(trade_data["amount_crypto"], 0)
        self.assertGreater(trade_data["usd_price_per_unit"], 0)
        self.assertTrue(update_gist_log.call_args.kwargs["saved_to_ghostfolio"])
        self.assertIn("✅ Saved", send_discord_alert.call_args.args[0])

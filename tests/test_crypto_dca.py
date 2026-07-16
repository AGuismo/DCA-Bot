import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import crypto_dca
import portfolio_logger


class DcaGhostfolioBoundaryTests(unittest.TestCase):
    def test_roi_at_threshold_uses_half_configured_amount(self):
        with (
            patch.dict(
                os.environ,
                {"PORTFOLIO_ACCOUNT_MAP": json.dumps({"BTC": "btc-account"})},
                clear=False,
            ),
            patch.object(
                portfolio_logger, "get_asset_roi_percent", return_value=-2.0
            ),
        ):
            decision = crypto_dca.determine_dynamic_dca_decision(
                "BTC_THB",
                800,
                {
                    "ENABLED": True,
                    "THRESHOLD_PERCENT": -2,
                    "REDUCED_MULTIPLIER": 0.5,
                },
            )

        self.assertEqual(decision["amount_thb"], 400)
        self.assertEqual(decision["multiplier"], 0.5)
        self.assertEqual(decision["roi_percent"], -2.0)
        self.assertIn("Half buy", decision["reason"])

    def test_ghostfolio_doge_roi_below_threshold_uses_full_configured_amount(self):
        with (
            patch.dict(
                os.environ,
                {"PORTFOLIO_ACCOUNT_MAP": json.dumps({"BTC": "btc-account"})},
                clear=False,
            ),
            patch.object(portfolio_logger, "get_asset_roi_percent", return_value=-15.43),
        ):
            decision = crypto_dca.determine_dynamic_dca_decision(
                "BTC_THB", 800, {"ENABLED": True}
            )

        self.assertEqual(decision["amount_thb"], 800)
        self.assertEqual(decision["multiplier"], 1.0)
        self.assertEqual(decision["roi_percent"], -15.43)
        self.assertIn("below -2.00%", decision["reason"])

    def test_unavailable_roi_uses_full_configured_amount(self):
        with (
            patch.dict(
                os.environ,
                {"PORTFOLIO_ACCOUNT_MAP": json.dumps({"BTC": "btc-account"})},
                clear=False,
            ),
            patch.object(
                portfolio_logger, "get_asset_roi_percent", return_value=None
            ),
        ):
            decision = crypto_dca.determine_dynamic_dca_decision(
                "BTC_THB", 800, {"ENABLED": True}
            )

        self.assertEqual(decision["amount_thb"], 800)
        self.assertEqual(decision["multiplier"], 1.0)
        self.assertIsNone(decision["roi_percent"])
        self.assertIn("ROI is unavailable", decision["reason"])

    def test_main_executes_the_dynamic_dca_decision(self):
        decision = {
            "amount_thb": 400,
            "multiplier": 0.5,
            "roi_percent": 1.25,
            "reason": (
                "Half buy (x0.5): asset ROI +1.25% is at or above -2.00%."
            ),
        }
        target_map = {
            "BTC_THB": {
                "TIME": "07:00",
                "AMOUNT": 800,
                "BUY_ENABLED": True,
                "LAST_BUY_DATE": "",
                "DYNAMIC_DCA": {"ENABLED": True},
            }
        }

        with (
            patch.object(crypto_dca, "DCA_TARGET_MAP_JSON", json.dumps(target_map)),
            patch.object(crypto_dca, "is_time_to_trade", return_value=True),
            patch.object(
                crypto_dca, "determine_dynamic_dca_decision", return_value=decision
            ) as determine_decision,
            patch.object(crypto_dca, "execute_trade") as execute_trade,
        ):
            crypto_dca.main()

        determine_decision.assert_called_once_with(
            "BTC_THB", 800.0, {"ENABLED": True}
        )
        execute_trade.assert_called_once_with(
            "BTC_THB",
            400,
            map_key="BTC_THB",
            target_map=target_map,
            dca_decision=decision,
        )

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
            crypto_dca.execute_trade(
                "BTC_THB",
                698.24,
                dca_decision={
                    "amount_thb": 698.24,
                    "multiplier": 0.5,
                    "roi_percent": -1.75,
                    "reason": (
                        "Half buy (x0.5): asset ROI -1.75% is at or above -2.00%."
                    ),
                },
            )

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
        self.assertIn("**Asset ROI:** -1.75%", send_discord_alert.call_args.args[0])
        self.assertIn("Half buy (x0.5)", send_discord_alert.call_args.args[0])

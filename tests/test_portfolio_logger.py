import unittest
from unittest.mock import patch

import portfolio_logger


class GhostfolioAssetResolutionTests(unittest.TestCase):
    def setUp(self):
        self.trade = {
            "ts": 1783830882,
            "amount_crypto": 0.08915444,
            "amount_thb": 199.50,
            "amount_usd": 5.99,
            "order_id": "6a53195c989d70eb16eeb540zmvsyf",
            "usd_price_per_unit": 67.1755,
        }

    def test_hype_thb_resolves_to_hyperliquid_coingecko_asset(self):
        resolved = portfolio_logger.resolve_ghostfolio_asset("HYPE", "HYPE_THB")

        self.assertEqual(resolved["dataSource"], "COINGECKO")
        self.assertEqual(resolved["symbol"], "hyperliquid")
        self.assertTrue(resolved["usedExplicitMapping"])

    def test_hype_payload_uses_provider_identifier_and_preserves_trade(self):
        activity = portfolio_logger.build_ghostfolio_activity(
            self.trade, "HYPE", "account-id", exchange_pair="HYPE_THB"
        )

        self.assertEqual(activity["dataSource"], "COINGECKO")
        self.assertEqual(activity["symbol"], "hyperliquid")
        self.assertEqual(activity["currency"], "USD")
        self.assertEqual(activity["quantity"], 0.08915444)
        self.assertEqual(activity["unitPrice"], 67.1755)
        self.assertIn(self.trade["order_id"], activity["comment"])

    def test_ambiguous_ticker_without_mapping_fails_closed(self):
        with patch.dict(portfolio_logger.SYMBOL_DATASOURCE_OVERRIDES, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Ambiguous Ghostfolio asset ticker HYPE"):
                portfolio_logger.resolve_ghostfolio_asset("HYPE", "HYPE_THB")

    def test_existing_non_ambiguous_asset_keeps_yahoo_fallback(self):
        resolved = portfolio_logger.resolve_ghostfolio_asset("BTC", "BTC_THB")

        self.assertEqual(resolved["dataSource"], "YAHOO")
        self.assertEqual(resolved["symbol"], "BTCUSD")
        self.assertFalse(resolved["usedExplicitMapping"])

    def test_existing_sui_override_is_unchanged(self):
        resolved = portfolio_logger.resolve_ghostfolio_asset("SUI", "SUI_THB")

        self.assertEqual(resolved["dataSource"], "COINGECKO")
        self.assertEqual(resolved["symbol"], "sui")

    def test_dry_run_accepts_exact_hyperliquid_provider_identity(self):
        activity = {"dataSource": "COINGECKO", "symbol": "hyperliquid"}
        response = {
            "activities": [{
                "error": None,
                "SymbolProfile": {
                    "dataSource": "COINGECKO",
                    "symbol": "hyperliquid",
                    "name": "Hyperliquid",
                },
            }]
        }

        portfolio_logger.validate_ghostfolio_resolution(activity, response)

    def test_dry_run_rejects_provider_resolution_mismatch(self):
        activity = {"dataSource": "COINGECKO", "symbol": "hyperliquid"}
        response = {
            "activities": [{
                "error": None,
                "SymbolProfile": {
                    "dataSource": "YAHOO",
                    "symbol": "HYPEUSD",
                    "name": "Supreme Finance USD",
                },
            }]
        }

        with self.assertRaisesRegex(ValueError, "asset resolution mismatch"):
            portfolio_logger.validate_ghostfolio_resolution(activity, response)


if __name__ == "__main__":
    unittest.main()

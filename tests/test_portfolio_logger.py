import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import portfolio_logger


class MockResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


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

    def test_all_dca_pairs_build_the_expected_activity(self):
        expected_activities = {
            "BTC_THB": {
                "account_id": "btc-account",
                "amount_crypto": 0.00033557,
                "data_source": "YAHOO",
                "date": "2026-07-13T17:00:39.000Z",
                "symbol": "BTC",
                "provider_symbol": "BTCUSD",
                "unit_price": 62318.4874,
            },
            "DOGE_THB": {
                "account_id": "alts-account",
                "amount_crypto": 145.23670854,
                "data_source": "YAHOO",
                "date": "2026-07-13T13:15:36.000Z",
                "symbol": "DOGE",
                "provider_symbol": "DOGEUSD",
                "unit_price": 0.0722,
            },
            "LINK_THB": {
                "account_id": "alts-account",
                "amount_crypto": 1.31813033,
                "data_source": "YAHOO",
                "date": "2026-07-14T00:15:47.000Z",
                "symbol": "LINK",
                "provider_symbol": "LINKUSD",
                "unit_price": 7.9326,
            },
            "SUI_THB": {
                "account_id": "alts-account",
                "amount_crypto": 6.15720165,
                "data_source": "COINGECKO",
                "date": "2026-07-14T02:00:43.000Z",
                "symbol": "SUI",
                "provider_symbol": "sui",
                "unit_price": 0.7278,
            },
            "HYPE_THB": {
                "account_id": "alts-account",
                "amount_crypto": 0.08915444,
                "data_source": "COINGECKO",
                "date": "2026-07-14T02:00:43.000Z",
                "symbol": "HYPE",
                "provider_symbol": "hyperliquid",
                "unit_price": 67.1755,
            },
        }

        for exchange_pair, expected in expected_activities.items():
            with self.subTest(exchange_pair=exchange_pair):
                timestamp = datetime.fromisoformat(
                    expected["date"].replace("Z", "+00:00")
                ).timestamp()
                trade = {
                    **self.trade,
                    "amount_crypto": expected["amount_crypto"],
                    "order_id": f"order-{expected['symbol'].lower()}",
                    "ts": timestamp,
                    "usd_price_per_unit": expected["unit_price"],
                }
                activity = portfolio_logger.build_ghostfolio_activity(
                    trade,
                    expected["symbol"],
                    expected["account_id"],
                    exchange_pair=exchange_pair,
                )

                self.assertEqual(activity["accountId"], expected["account_id"])
                self.assertEqual(activity["dataSource"], expected["data_source"])
                self.assertEqual(activity["date"], expected["date"])
                self.assertEqual(activity["quantity"], expected["amount_crypto"])
                self.assertEqual(activity["symbol"], expected["provider_symbol"])
                self.assertEqual(activity["unitPrice"], expected["unit_price"])
                self.assertIn(trade["order_id"], activity["comment"])

    def test_hype_resolution_does_not_change_other_pair_resolutions(self):
        pairs = {
            "BTC": "BTC_THB",
            "DOGE": "DOGE_THB",
            "LINK": "LINK_THB",
            "SUI": "SUI_THB",
        }
        before_hype = {
            symbol: portfolio_logger.resolve_ghostfolio_asset(symbol, exchange_pair)
            for symbol, exchange_pair in pairs.items()
        }

        portfolio_logger.resolve_ghostfolio_asset("HYPE", "HYPE_THB")

        after_hype = {
            symbol: portfolio_logger.resolve_ghostfolio_asset(symbol, exchange_pair)
            for symbol, exchange_pair in pairs.items()
        }
        self.assertEqual(before_hype, after_hype)

    def test_get_account_id_uses_direct_then_default_mapping(self):
        account_map = {"BTC": "btc-account", "DEFAULT": "alts-account"}

        self.assertEqual(
            portfolio_logger.get_account_id("BTC", account_map), "btc-account"
        )
        self.assertEqual(
            portfolio_logger.get_account_id("DOGE", account_map), "alts-account"
        )
        self.assertIsNone(portfolio_logger.get_account_id("BTC", {}))

    def test_asset_roi_uses_mapped_account_and_currency_effect_return(self):
        response = MockResponse(
            200,
            {
                "holdings": [
                    {
                        "dataSource": "YAHOO",
                        "symbol": "BTCUSD",
                        "netPerformancePercentWithCurrencyEffect": -1.75,
                    }
                ]
            },
        )

        with (
            patch.object(portfolio_logger, "GHOSTFOLIO_TOKEN", "test-token"),
            patch.object(
                portfolio_logger, "authenticate_ghostfolio", return_value="jwt"
            ) as authenticate,
            patch.object(portfolio_logger.requests, "get", return_value=response) as get,
        ):
            roi_percent = portfolio_logger.get_asset_roi_percent(
                "BTC", "btc-account", exchange_pair="BTC_THB"
            )

        self.assertEqual(roi_percent, -1.75)
        authenticate.assert_called_once_with(
            portfolio_logger.GHOSTFOLIO_URL,
            "test-token",
            timeout=portfolio_logger.ROI_LOOKUP_TIMEOUT_SECONDS,
            retries=1,
        )
        self.assertEqual(
            get.call_args.kwargs["params"],
            {
                "accounts": "btc-account",
                "dataSource": "YAHOO",
                "range": "max",
                "symbol": "BTCUSD",
            },
        )
        self.assertEqual(
            get.call_args.kwargs["timeout"],
            portfolio_logger.ROI_LOOKUP_TIMEOUT_SECONDS,
        )

    def test_asset_roi_returns_none_for_missing_matching_holding(self):
        response = MockResponse(
            200,
            {
                "holdings": [
                    {
                        "dataSource": "YAHOO",
                        "symbol": "ETHUSD",
                        "netPerformancePercentWithCurrencyEffect": -10.0,
                    }
                ]
            },
        )

        with (
            patch.object(portfolio_logger, "GHOSTFOLIO_TOKEN", "test-token"),
            patch.object(
                portfolio_logger, "authenticate_ghostfolio", return_value="jwt"
            ),
            patch.object(portfolio_logger.requests, "get", return_value=response),
        ):
            roi_percent = portfolio_logger.get_asset_roi_percent(
                "BTC", "btc-account", exchange_pair="BTC_THB"
            )

        self.assertIsNone(roi_percent)

    def test_asset_roi_returns_none_for_failed_response(self):
        response = MockResponse(503, {"message": "service unavailable"})

        with (
            patch.object(portfolio_logger, "GHOSTFOLIO_TOKEN", "test-token"),
            patch.object(
                portfolio_logger, "authenticate_ghostfolio", return_value="jwt"
            ),
            patch.object(portfolio_logger.requests, "get", return_value=response),
        ):
            roi_percent = portfolio_logger.get_asset_roi_percent(
                "BTC", "btc-account", exchange_pair="BTC_THB"
            )

        self.assertIsNone(roi_percent)

    def test_transient_validation_rejection_retries_then_saves(self):
        validation_failure = MockResponse(
            400,
            {
                "message": [
                    'activities.0.symbol ("BTCUSD") is not valid for the '
                    'specified data source ("YAHOO")'
                ]
            },
        )
        valid_dry_run = MockResponse(
            201,
            {
                "activities": [
                    {
                        "error": None,
                        "SymbolProfile": {
                            "dataSource": "YAHOO",
                            "symbol": "BTCUSD",
                            "name": "Bitcoin USD",
                        },
                    }
                ]
            },
        )
        successful_import = MockResponse(
            201,
            {
                "activities": [
                    {
                        "error": None,
                        "SymbolProfile": {
                            "dataSource": "YAHOO",
                            "symbol": "BTCUSD",
                            "name": "Bitcoin USD",
                        },
                    }
                ]
            },
        )

        with (
            patch.object(portfolio_logger, "GHOSTFOLIO_TOKEN", "test-token"),
            patch.object(
                portfolio_logger, "authenticate_ghostfolio", return_value="jwt"
            ),
            patch.object(
                portfolio_logger.requests,
                "post",
                side_effect=[validation_failure, valid_dry_run, successful_import],
            ) as post,
            patch.object(portfolio_logger.time, "sleep") as sleep,
        ):
            saved = portfolio_logger.log_to_ghostfolio(
                self.trade, "BTC", "btc-account", exchange_pair="BTC_THB"
            )

        self.assertTrue(saved)
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 1)
        self.assertIn("?dryRun=true", post.call_args_list[0].args[0])
        self.assertNotIn("?dryRun=true", post.call_args_list[-1].args[0])

    def test_http_success_without_a_resolved_activity_is_not_reported_saved(self):
        valid_dry_run = MockResponse(
            201,
            {
                "activities": [
                    {
                        "error": None,
                        "SymbolProfile": {
                            "dataSource": "YAHOO",
                            "symbol": "BTCUSD",
                            "name": "Bitcoin USD",
                        },
                    }
                ]
            },
        )
        malformed_import = MockResponse(201, {"activities": []})

        with (
            patch.object(portfolio_logger, "GHOSTFOLIO_TOKEN", "test-token"),
            patch.object(
                portfolio_logger, "authenticate_ghostfolio", return_value="jwt"
            ),
            patch.object(
                portfolio_logger.requests,
                "post",
                side_effect=[valid_dry_run, malformed_import],
            ),
        ):
            saved = portfolio_logger.log_to_ghostfolio(
                self.trade, "BTC", "btc-account", exchange_pair="BTC_THB"
            )

        self.assertFalse(saved)

    def test_rejected_ghostfolio_response_returns_false_after_retries(self):
        rejection = MockResponse(
            400,
            {
                "message": [
                    'activities.0.symbol ("BTCUSD") is not valid for the '
                    'specified data source ("YAHOO")'
                ]
            },
        )

        with (
            patch.object(portfolio_logger, "GHOSTFOLIO_TOKEN", "test-token"),
            patch.object(
                portfolio_logger, "authenticate_ghostfolio", return_value="jwt"
            ),
            patch.object(
                portfolio_logger.requests, "post", return_value=rejection
            ) as post,
            patch.object(portfolio_logger.time, "sleep"),
        ):
            saved = portfolio_logger.log_to_ghostfolio(
                self.trade, "BTC", "btc-account", exchange_pair="BTC_THB"
            )

        self.assertFalse(saved)
        self.assertEqual(post.call_count, portfolio_logger.IMPORT_RETRY_ATTEMPTS)

    def test_duplicate_dry_run_is_confirmed_idempotent_success(self):
        duplicate = MockResponse(
            201, {"activities": [{"error": {"code": "IS_DUPLICATE"}}]}
        )

        with (
            patch.object(portfolio_logger, "GHOSTFOLIO_TOKEN", "test-token"),
            patch.object(
                portfolio_logger, "authenticate_ghostfolio", return_value="jwt"
            ),
            patch.object(
                portfolio_logger.requests, "post", return_value=duplicate
            ) as post,
        ):
            saved = portfolio_logger.log_to_ghostfolio(
                self.trade, "BTC", "btc-account", exchange_pair="BTC_THB"
            )

        self.assertTrue(saved)
        self.assertEqual(post.call_count, 1)

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

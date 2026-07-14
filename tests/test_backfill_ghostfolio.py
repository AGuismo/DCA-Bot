import unittest
from pathlib import Path
from unittest.mock import patch

import backfill_ghostfolio


class GhostfolioBackfillTests(unittest.TestCase):
    def setUp(self):
        manifest_path = Path(backfill_ghostfolio.DEFAULT_MANIFEST)
        self.transactions = backfill_ghostfolio.load_transactions(manifest_path)
        self.account_map = {
            "BTC": "3cced5d3-f219-47c8-bb73-878466060d7a",
            "DEFAULT": "9069984b-3c2b-48d8-831d-b7d73b5bafb7",
        }

    def test_confirmed_transactions_build_correct_assets(self):
        expected_assets = {
            "BTC_THB": ("YAHOO", "BTCUSD"),
            "DOGE_THB": ("YAHOO", "DOGEUSD"),
            "LINK_THB": ("YAHOO", "LINKUSD"),
            "SUI_THB": ("COINGECKO", "sui"),
        }

        for transaction in self.transactions:
            with self.subTest(pair=transaction["pair"]):
                activity, _, _ = backfill_ghostfolio.build_reconciliation_activity(
                    transaction, self.account_map
                )
                self.assertEqual(
                    (activity["dataSource"], activity["symbol"]),
                    expected_assets[transaction["pair"]],
                )
                self.assertIn(transaction["order_id"], activity["comment"])

    def test_find_existing_activity_detects_exact_exchange_order_id(self):
        transaction = self.transactions[0]
        expected_activity, _, _ = backfill_ghostfolio.build_reconciliation_activity(
            transaction, self.account_map
        )
        existing_activity = {
            "SymbolProfile": {
                "dataSource": expected_activity["dataSource"],
                "symbol": expected_activity["symbol"],
            },
            "comment": expected_activity["comment"],
        }

        state, reason = backfill_ghostfolio.find_existing_activity(
            [existing_activity], expected_activity, transaction["order_id"]
        )

        self.assertEqual(state, "already_present")
        self.assertIn("order ID", reason)

    def test_find_existing_activity_rejects_wrong_asset_collision(self):
        transaction = self.transactions[0]
        expected_activity, _, _ = backfill_ghostfolio.build_reconciliation_activity(
            transaction, self.account_map
        )
        existing_activity = {
            "SymbolProfile": {"dataSource": "COINGECKO", "symbol": "hyperliquid"},
            "comment": expected_activity["comment"],
        }

        state, reason = backfill_ghostfolio.find_existing_activity(
            [existing_activity], expected_activity, transaction["order_id"]
        )

        self.assertEqual(state, "conflict")
        self.assertIn("hyperliquid", reason)

    def test_dry_run_reports_missing_activity_without_inserting(self):
        with (
            patch.object(
                backfill_ghostfolio.portfolio_logger,
                "GHOSTFOLIO_TOKEN",
                "test-token",
            ),
            patch.object(
                backfill_ghostfolio.portfolio_logger,
                "authenticate_ghostfolio",
                return_value="jwt",
            ),
            patch.object(
                backfill_ghostfolio, "fetch_existing_activities", return_value=[]
            ),
            patch.object(backfill_ghostfolio.portfolio_logger, "log_to_ghostfolio") as log,
        ):
            summary = backfill_ghostfolio.reconcile_transactions(
                [self.transactions[0]], dry_run=True
            )

        self.assertEqual(len(summary["discovered"]), 1)
        self.assertEqual(len(summary["to_insert"]), 1)
        self.assertEqual(summary["inserted"], [])
        self.assertEqual(summary["failed"], [])
        log.assert_not_called()

    def test_reconciliation_inserts_only_missing_activity(self):
        with (
            patch.object(
                backfill_ghostfolio.portfolio_logger,
                "GHOSTFOLIO_TOKEN",
                "test-token",
            ),
            patch.object(
                backfill_ghostfolio.portfolio_logger,
                "authenticate_ghostfolio",
                return_value="jwt",
            ),
            patch.object(
                backfill_ghostfolio, "fetch_existing_activities", return_value=[]
            ),
            patch.object(
                backfill_ghostfolio.portfolio_logger,
                "log_to_ghostfolio",
                return_value=True,
            ) as log,
        ):
            summary = backfill_ghostfolio.reconcile_transactions(
                [self.transactions[0]], dry_run=False
            )

        self.assertEqual(len(summary["inserted"]), 1)
        self.assertEqual(summary["failed"], [])
        log.assert_called_once()

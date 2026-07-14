"""Reconcile completed DCA fills into Ghostfolio without creating duplicates."""

import argparse
import json
import math
import os
from datetime import datetime
from pathlib import Path

import requests

import portfolio_logger


DEFAULT_MANIFEST = Path(__file__).with_name("ghostfolio_backfill_2026_07_13.json")
REQUIRED_TRANSACTION_FIELDS = {
    "amount_crypto",
    "amount_thb",
    "amount_usd",
    "executed_at",
    "order_id",
    "pair",
    "usd_price_per_unit",
}


def load_transactions(manifest_path):
    """Load reconciliation transactions from a JSON manifest."""
    with manifest_path.open(encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    transactions = manifest.get("transactions")
    if not isinstance(transactions, list):
        raise ValueError("Manifest must contain a transactions array")

    return transactions


def _parse_timestamp(timestamp):
    """Convert an ISO-8601 execution time to a Unix timestamp."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


def _load_account_map():
    """Load the optional runtime account map used by normal DCA executions."""
    account_map_json = os.environ.get("PORTFOLIO_ACCOUNT_MAP", "{}")
    try:
        account_map = json.loads(account_map_json)
    except json.JSONDecodeError as error:
        raise ValueError(f"PORTFOLIO_ACCOUNT_MAP is not valid JSON: {error}") from error

    if not isinstance(account_map, dict):
        raise ValueError("PORTFOLIO_ACCOUNT_MAP must be a JSON object")

    return account_map


def build_reconciliation_activity(transaction, account_map):
    """Build a validated Ghostfolio activity from one exchange-fill record."""
    missing_fields = REQUIRED_TRANSACTION_FIELDS - transaction.keys()
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ValueError(f"Transaction is missing required fields: {missing}")

    exchange_pair = transaction["pair"].strip().upper()
    if "_" not in exchange_pair:
        raise ValueError(f"Unsupported exchange pair: {exchange_pair}")

    symbol = exchange_pair.split("_", maxsplit=1)[0]
    account_id = transaction.get("account_id") or portfolio_logger.get_account_id(
        symbol, account_map
    )
    if not account_id:
        raise ValueError(f"No Ghostfolio account configured for {symbol}")

    trade_data = {
        "amount_crypto": float(transaction["amount_crypto"]),
        "amount_thb": float(transaction["amount_thb"]),
        "amount_usd": float(transaction["amount_usd"]),
        "order_id": transaction["order_id"],
        "ts": _parse_timestamp(transaction["executed_at"]),
        "usd_price_per_unit": float(transaction["usd_price_per_unit"]),
    }
    activity = portfolio_logger.build_ghostfolio_activity(
        trade_data, symbol, account_id, exchange_pair=exchange_pair
    )

    return activity, symbol, trade_data


def fetch_existing_activities(base_url, bearer_token, account_id):
    """Fetch all activities for an account so order references can be checked."""
    activities = []
    skip = 0

    while True:
        response = requests.get(
            f"{base_url}/api/v1/order",
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={"accounts": account_id, "skip": skip, "take": 1000},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Ghostfolio activity lookup failed (HTTP {response.status_code}): "
                f"{response.text[:1000]}"
            )

        response_data = response.json()
        page = response_data.get("activities", [])
        if not isinstance(page, list):
            raise RuntimeError(
                "Ghostfolio activity lookup returned an invalid activities list"
            )

        activities.extend(page)
        total = response_data.get("count", len(activities))
        if not page or len(activities) >= total:
            return activities

        skip += len(page)


def authenticate_for_reconciliation():
    """Authenticate with either the production access token or a local security token."""
    if portfolio_logger.GHOSTFOLIO_TOKEN:
        return portfolio_logger.authenticate_ghostfolio(
            portfolio_logger.GHOSTFOLIO_URL, portfolio_logger.GHOSTFOLIO_TOKEN
        )

    security_token = os.environ.get("GHOSTFOLIO_SECURITY_TOKEN")
    if not security_token:
        return None

    try:
        response = requests.get(
            f"{portfolio_logger.GHOSTFOLIO_URL}/api/v1/auth/anonymous/{security_token}",
            timeout=30,
        )
    except requests.exceptions.RequestException:
        print("❌ Ghostfolio local authentication request failed")
        return None

    if response.status_code not in {200, 201}:
        print(f"❌ Ghostfolio local authentication failed (HTTP {response.status_code})")
        return None

    try:
        bearer_token = response.json().get("authToken")
    except ValueError:
        print("❌ Ghostfolio local authentication returned invalid JSON")
        return None

    if not bearer_token:
        print("❌ Ghostfolio local authentication returned no auth token")
        return None

    return bearer_token


def _activity_identity(activity):
    """Return the provider identity regardless of Ghostfolio response shape."""
    profile = activity.get("SymbolProfile") or {}
    return (
        activity.get("dataSource") or profile.get("dataSource"),
        activity.get("symbol") or profile.get("symbol"),
    )


def _same_execution_time(first_date, second_date):
    """Compare activity dates at whole-second precision."""
    first = datetime.fromisoformat(first_date.replace("Z", "+00:00"))
    second = datetime.fromisoformat(second_date.replace("Z", "+00:00"))
    return abs((first - second).total_seconds()) < 1


def find_existing_activity(existing_activities, expected_activity, order_id):
    """Find a matching order ID or a fully equivalent Ghostfolio activity."""
    expected_identity = (expected_activity["dataSource"], expected_activity["symbol"])

    for activity in existing_activities:
        comment = activity.get("comment") or ""
        if order_id not in comment:
            continue

        if _activity_identity(activity) == expected_identity:
            return "already_present", "matching exchange order ID"

        actual_data_source, actual_symbol = _activity_identity(activity)
        return (
            "conflict",
            "exchange order ID exists under "
            f"{actual_data_source}/{actual_symbol}, expected "
            f"{expected_activity['dataSource']}/{expected_activity['symbol']}",
        )

    for activity in existing_activities:
        if activity.get("accountId") != expected_activity["accountId"]:
            continue
        if _activity_identity(activity) != expected_identity:
            continue
        if activity.get("type") != expected_activity["type"]:
            continue
        if not _same_execution_time(activity["date"], expected_activity["date"]):
            continue
        if not math.isclose(
            float(activity.get("quantity", 0)),
            float(expected_activity["quantity"]),
            rel_tol=0,
            abs_tol=0.00000001,
        ):
            continue
        if not math.isclose(
            float(activity.get("unitPrice", 0)),
            float(expected_activity["unitPrice"]),
            rel_tol=0,
            abs_tol=0.0001,
        ):
            continue

        return "already_present", "matching account, asset, time, quantity, and price"

    return "missing", "no equivalent Ghostfolio activity found"


def _record(summary, category, order_id, reason):
    """Add one reconciliation result and print an actionable status line."""
    result = {"order_id": order_id, "reason": reason}
    summary[category].append(result)
    print(f"{category.upper()}: {order_id} - {reason}")


def reconcile_transactions(transactions, dry_run):
    """Check and optionally insert every transaction from a reconciliation manifest."""
    bearer_token = authenticate_for_reconciliation()
    if not bearer_token:
        raise RuntimeError(
            "Set GHOSTFOLIO_TOKEN or GHOSTFOLIO_SECURITY_TOKEN to reconcile Ghostfolio"
        )

    account_map = _load_account_map()
    activities_by_account = {}
    summary = {
        "already_present": [],
        "discovered": [],
        "failed": [],
        "inserted": [],
        "to_insert": [],
    }

    for transaction in transactions:
        order_id = str(transaction.get("order_id", "unknown"))
        _record(summary, "discovered", order_id, transaction.get("pair", "unknown pair"))

        try:
            activity, symbol, trade_data = build_reconciliation_activity(
                transaction, account_map
            )
            account_id = activity["accountId"]
            if account_id not in activities_by_account:
                activities_by_account[account_id] = fetch_existing_activities(
                    portfolio_logger.GHOSTFOLIO_URL, bearer_token, account_id
                )

            state, reason = find_existing_activity(
                activities_by_account[account_id], activity, order_id
            )
            if state == "already_present":
                _record(summary, "already_present", order_id, reason)
                continue
            if state == "conflict":
                _record(summary, "failed", order_id, reason)
                continue

            _record(summary, "to_insert", order_id, reason)
            if dry_run:
                continue

            saved = portfolio_logger.log_to_ghostfolio(
                trade_data,
                symbol,
                account_id,
                exchange_pair=transaction["pair"],
                bearer_token=bearer_token,
            )
            if saved:
                activities_by_account[account_id].append(
                    {
                        "accountId": activity["accountId"],
                        "comment": activity["comment"],
                        "date": activity["date"],
                        "quantity": activity["quantity"],
                        "type": activity["type"],
                        "unitPrice": activity["unitPrice"],
                        "SymbolProfile": {
                            "dataSource": activity["dataSource"],
                            "symbol": activity["symbol"],
                        },
                    }
                )
                _record(summary, "inserted", order_id, "Ghostfolio confirmed import")
            else:
                _record(summary, "failed", order_id, "Ghostfolio did not confirm import")
        except Exception as error:
            _record(summary, "failed", order_id, str(error))

    return summary


def print_summary(summary):
    """Print the reconciliation totals requested by the recovery workflow."""
    print("\nReconciliation summary")
    print(f"Transactions discovered: {len(summary['discovered'])}")
    print(f"Transactions already present: {len(summary['already_present'])}")
    print(f"Transactions to be inserted: {len(summary['to_insert'])}")
    print(f"Transactions successfully inserted: {len(summary['inserted'])}")
    print(f"Transactions that failed: {len(summary['failed'])}")


def main():
    """Run the Ghostfolio reconciliation command."""
    parser = argparse.ArgumentParser(
        description="Reconcile completed DCA fills into Ghostfolio safely"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="JSON manifest of exchange fills to reconcile",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report missing activities without inserting them",
    )
    arguments = parser.parse_args()

    summary = reconcile_transactions(load_transactions(arguments.input), arguments.dry_run)
    print_summary(summary)
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
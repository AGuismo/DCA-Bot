import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone as dt_timezone

# Ghostfolio Configuration
GHOSTFOLIO_URL = os.environ.get("GHOSTFOLIO_URL", "https://ghostfol.io")
GHOSTFOLIO_TOKEN = os.environ.get("GHOSTFOLIO_TOKEN")
PORTFOLIO_ACCOUNT_MAP_JSON = os.environ.get("PORTFOLIO_ACCOUNT_MAP", "{}")

# Overrides for coins where Yahoo Finance's {symbol}USD ticker is wrong or maps to the
# wrong asset. Keys are the base crypto symbol (e.g. "SUI"). Values are the exact
# dataSource + symbol pair that Ghostfolio should use instead.
# COINGECKO symbols are the CoinGecko coin ID (lowercase).
SYMBOL_DATASOURCE_OVERRIDES = {
    "HYPE": {"dataSource": "COINGECKO", "symbol": "hyperliquid"},
    "SUI": {"dataSource": "COINGECKO", "symbol": "sui"},
}

# These tickers are known to identify more than one asset. They must never use
# the conventional Yahoo {ticker}USD fallback without an explicit mapping.
AMBIGUOUS_SYMBOLS = {"HYPE"}

IMPORT_RETRY_ATTEMPTS = 3
IMPORT_RETRY_DELAY_SECONDS = 2
RETRYABLE_IMPORT_STATUS_CODES = {408, 425, 429}

# Timezone Configuration
TIMEZONE_NAME = os.environ.get("TIMEZONE", "Asia/Bangkok")
from zoneinfo import ZoneInfo
SELECTED_TZ = ZoneInfo(TIMEZONE_NAME)

def get_account_id(symbol, portfolio_map):
    """
    Get Ghostfolio account ID for a given crypto symbol.
    Falls back to DEFAULT if symbol not found.
    
    Args:
        symbol: Base crypto symbol (e.g., "BTC", "LINK")
        portfolio_map: Dict mapping symbols to account IDs
    
    Returns:
        Account UUID string or None if no mapping exists
    """
    if not portfolio_map:
        print(f"⚠️ No PORTFOLIO_ACCOUNT_MAP configured.")
        return None
    
    # Try direct symbol match first
    account_id = portfolio_map.get(symbol)
    
    if not account_id:
        # Fall back to DEFAULT
        account_id = portfolio_map.get("DEFAULT")
        if account_id:
            print(f"   Using DEFAULT account for {symbol}")
    
    return account_id

def authenticate_ghostfolio(base_url, access_token, timeout=30, retries=3, delay=2):
    """
    Authenticate to Ghostfolio and get Bearer JWT token.
    Retries on transient network/SSL errors.
    
    Args:
        base_url: Ghostfolio instance URL
        access_token: User's access token
        timeout: Request timeout in seconds (doubled from standard 15s)
        retries: Number of attempts before giving up
        delay: Seconds to wait between retries
    
    Returns:
        Bearer token string or None on failure
    """
    url = f"{base_url}/api/v1/auth/anonymous"
    payload = {"accessToken": access_token}
    
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            
            if r.status_code != 201:
                print(f"❌ Ghostfolio auth failed ({r.status_code}): {r.text}")
                return None
            
            token = r.json().get("authToken")
            if not token:
                print(f"❌ No authToken in Ghostfolio response")
                return None
            
            return token
            
        except requests.exceptions.Timeout:
            print(f"❌ Ghostfolio auth timed out (attempt {attempt}/{retries})")
        except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as e:
            print(f"⚠️ Ghostfolio auth connection error (attempt {attempt}/{retries}): {e}")
        except Exception as e:
            print(f"❌ Ghostfolio authentication error: {e}")
            return None
        
        if attempt < retries:
            print(f"   Retrying in {delay}s...")
            time.sleep(delay)
    
    print(f"❌ Ghostfolio authentication failed after {retries} attempts")
    return None


def resolve_ghostfolio_asset(symbol, exchange_pair=None):
    """Resolve a base ticker to the exact identity expected by Ghostfolio."""
    base_symbol = symbol.strip().upper()
    override = SYMBOL_DATASOURCE_OVERRIDES.get(base_symbol)

    if override:
        resolution = {
            "dataSource": override["dataSource"],
            "symbol": override["symbol"],
            "providerIdentifier": override["symbol"],
            "usedExplicitMapping": True,
        }
    elif base_symbol in AMBIGUOUS_SYMBOLS:
        raise ValueError(
            f"Ambiguous Ghostfolio asset ticker {base_symbol} has no explicit mapping"
        )
    else:
        yahoo_symbol = f"{base_symbol}USD"
        resolution = {
            "dataSource": "YAHOO",
            "symbol": yahoo_symbol,
            "providerIdentifier": yahoo_symbol,
            "usedExplicitMapping": False,
        }

    print(
        "   Ghostfolio asset resolution: "
        f"pair={exchange_pair or 'unknown'}, base={base_symbol}, "
        f"requested_symbol={resolution['symbol']}, "
        f"data_source={resolution['dataSource']}, "
        f"provider_identifier={resolution['providerIdentifier']}, "
        f"method={'explicit_mapping' if resolution['usedExplicitMapping'] else 'fallback'}"
    )
    return resolution


def build_ghostfolio_activity(trade_data, symbol, account_id, exchange_pair=None):
    """Build an import activity after resolving its provider-specific asset."""
    ts = trade_data["ts"]
    dt = datetime.fromtimestamp(ts, tz=SELECTED_TZ)
    date_str = dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    quantity = float(f"{trade_data['amount_crypto']:.8f}")
    resolution = resolve_ghostfolio_asset(symbol, exchange_pair=exchange_pair)

    return {
        "accountId": account_id,
        "comment": (
            f"฿{trade_data['amount_thb']:.2f} - ${trade_data['amount_usd']:.2f} - "
            f"{trade_data['order_id']}"
        ),
        "currency": "USD",
        "dataSource": resolution["dataSource"],
        "date": date_str,
        "fee": 0,
        "quantity": quantity,
        "symbol": resolution["symbol"],
        "type": "BUY",
        "unitPrice": round(trade_data["usd_price_per_unit"], 4),
    }


def _safe_response_body(response, redacted_values=()):
    """Return a bounded API response body without exposing the access token."""
    body = response.text[:1000]
    for value in (GHOSTFOLIO_TOKEN, *redacted_values):
        if value:
            body = body.replace(value, "[redacted]")
    return body


def _response_messages(response):
    """Extract Ghostfolio error messages when the response is JSON."""
    try:
        response_data = response.json()
    except ValueError:
        return []

    messages = response_data.get("message", [])
    if isinstance(messages, str):
        return [messages]
    if isinstance(messages, list):
        return [str(message) for message in messages]
    return []


def _is_retryable_import_response(response):
    """Identify transient Ghostfolio failures, including masked provider outages."""
    if response.status_code in RETRYABLE_IMPORT_STATUS_CODES:
        return True
    if response.status_code >= 500:
        return True
    if response.status_code != 400:
        return False

    return any(
        "is not valid for the specified data source" in message.lower()
        for message in _response_messages(response)
    )


def _post_import(url, headers, payload, stage):
    """Post an import request with bounded retries for transient failures."""
    for attempt in range(1, IMPORT_RETRY_ATTEMPTS + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
        ) as error:
            retryable = True
            failure = f"request error: {error}"
        else:
            if response.status_code == 201:
                return response

            retryable = _is_retryable_import_response(response)
            failure = (
                f"HTTP {response.status_code}: "
                f"{_safe_response_body(response, [headers.get('Authorization')])}"
            )

            if not retryable:
                print(f"❌ Ghostfolio {stage} failed ({failure})")
                return response

        print(
            f"⚠️ Ghostfolio {stage} failed "
            f"(attempt {attempt}/{IMPORT_RETRY_ATTEMPTS}; {failure})"
        )
        if retryable and attempt < IMPORT_RETRY_ATTEMPTS:
            print(f"   Retrying in {IMPORT_RETRY_DELAY_SECONDS}s...")
            time.sleep(IMPORT_RETRY_DELAY_SECONDS)

    print(
        f"❌ Ghostfolio {stage} failed after "
        f"{IMPORT_RETRY_ATTEMPTS} attempts: {failure}"
    )
    return None


def validate_ghostfolio_resolution(activity, dry_run_response):
    """Ensure Ghostfolio resolved the exact provider identity we requested."""
    activities = dry_run_response.get("activities", [])
    if len(activities) != 1:
        raise ValueError(
            f"Ghostfolio dry run returned {len(activities)} activities; expected 1"
        )

    result = activities[0]
    result_error = result.get("error")
    if result_error:
        if (
            isinstance(result_error, dict)
            and result_error.get("code") == "IS_DUPLICATE"
        ):
            return "duplicate"
        raise ValueError(f"Ghostfolio asset resolution failed: {result_error}")

    profile = result.get("SymbolProfile") or {}
    selected_data_source = profile.get("dataSource")
    selected_symbol = profile.get("symbol")
    print(
        "   Ghostfolio dry-run selection: "
        f"data_source={selected_data_source}, "
        f"provider_identifier={selected_symbol}, name={profile.get('name')}"
    )

    if (
        selected_data_source != activity["dataSource"]
        or selected_symbol != activity["symbol"]
    ):
        raise ValueError(
            "Ghostfolio asset resolution mismatch: "
            f"requested {activity['dataSource']}/{activity['symbol']}, "
            f"selected {selected_data_source}/{selected_symbol}"
        )

    return "valid"


def log_to_ghostfolio(
    trade_data, symbol, account_id, exchange_pair=None, bearer_token=None
):
    """
    Log a trade to Ghostfolio portfolio.
    
    Args:
        trade_data: Dict with keys:
            - ts: Unix timestamp
            - amount_crypto: Crypto quantity received
            - amount_thb: THB spent
            - amount_usd: USD spent
            - symbol: Base symbol (for logging)
            - order_id: Trade order ID
            - usd_price_per_unit: Price per 1 full coin in USD
        symbol: Base crypto symbol (e.g., "BTC", "LINK")
        account_id: Ghostfolio account UUID
        exchange_pair: Input exchange pair for resolution logging (e.g., "HYPE_THB")
        bearer_token: Existing Ghostfolio session token for a recovery job
    
    Returns:
        True on success, False on failure
    """
    if not GHOSTFOLIO_TOKEN and not bearer_token:
        print("⚠️ GHOSTFOLIO_TOKEN not set. Skipping Ghostfolio logging.")
        return False
    
    if not account_id:
        print("⚠️ No account ID provided. Skipping Ghostfolio logging.")
        return False
    
    try:
        # 1. Authenticate
        if not bearer_token:
            bearer_token = authenticate_ghostfolio(
                GHOSTFOLIO_URL, GHOSTFOLIO_TOKEN, timeout=30
            )
            if not bearer_token:
                return False
        
        # 2. Resolve the provider-specific asset and build the import payload.
        activity = build_ghostfolio_activity(
            trade_data, symbol, account_id, exchange_pair=exchange_pair
        )
        quantity = activity["quantity"]
        
        # 6. Import to Ghostfolio (with retry for transient errors)
        url = f"{GHOSTFOLIO_URL}/api/v1/import"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }
        payload = {"activities": [activity]}

        # Validate Ghostfolio's own provider resolution before creating anything.
        # A mismatch fails closed instead of saving an activity under a wrong asset.
        dry_run = _post_import(
            f"{url}?dryRun=true", headers, payload, "asset-resolution dry run"
        )
        if dry_run is None or dry_run.status_code != 201:
            return False
        dry_run_state = validate_ghostfolio_resolution(activity, dry_run.json())
        if dry_run_state == "duplicate":
            print(
                f"✅ Ghostfolio activity already exists: "
                f"{quantity:.8f} {symbol} @ ${activity['unitPrice']:.4f}"
            )
            return True

        response = _post_import(url, headers, payload, "import")
        if response is None or response.status_code != 201:
            return False

        import_state = validate_ghostfolio_resolution(activity, response.json())
        if import_state != "valid":
            print(
                "❌ Ghostfolio import returned a duplicate activity after a "
                "valid dry run; reconcile it by exchange order ID"
            )
            return False

        print(
            f"✅ Successfully logged to Ghostfolio: "
            f"{quantity:.8f} {symbol} @ ${activity['unitPrice']:.4f}"
        )
        return True
    
    except Exception as e:
        print(f"❌ Ghostfolio logging error: {e}")
        return False

if __name__ == "__main__":
    # Test execution
    print("Testing Portfolio Logger...")
    
    if not GHOSTFOLIO_TOKEN:
        print("⚠️ Please set GHOSTFOLIO_TOKEN environment variable to test.")
        print("Example: export GHOSTFOLIO_TOKEN='your-token' && python portfolio_logger.py")
    else:
        # Load account map
        try:
            portfolio_map = json.loads(PORTFOLIO_ACCOUNT_MAP_JSON)
        except Exception:
            print("⚠️ Failed to parse PORTFOLIO_ACCOUNT_MAP. Using empty map.")
            portfolio_map = {}
        
        print(f"Portfolio Account Map: {portfolio_map}")
        
        # Test with dummy BTC trade
        test_symbol = "BTC"
        test_account_id = get_account_id(test_symbol, portfolio_map)
        
        if test_account_id:
            dummy_data = {
                "ts": datetime.now().timestamp(),
                "amount_crypto": 0.00012345,
                "amount_thb": 800.0,
                "amount_usd": 25.10,
                "symbol": test_symbol,
                "order_id": "TEST_123",
                "usd_price_per_unit": 95000.00
            }
            print(f"\nTest Payload: {dummy_data}")
            result = log_to_ghostfolio(dummy_data, test_symbol, test_account_id)
            print(f"Result: {'SUCCESS' if result else 'FAILED'}")
        else:
            print(f"❌ No account ID found for {test_symbol}")

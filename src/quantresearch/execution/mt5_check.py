"""Read-only MetaTrader 5 connection check, and the MT5 block of the health endpoint.

    python -m quantresearch.execution.mt5_check [--symbol XAUUSD]

Connects to the installed MT5 terminal, prints the account, a live quote and the last five closed H1 bars,
then disconnects and prints PASS or FAIL. It never calls an order function: order sending is forced off first.
Credentials come from the same place the API reads them (Windows Credential Manager through keyring, then
QR_MT5_LOGIN / QR_MT5_PASSWORD / QR_MT5_SERVER), or from the terminal's own saved login if none are set.
The password is never printed. Exit code 0 means PASS, 1 means FAIL.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Sequence, TextIO

from quantresearch.execution.broker import BrokerError
from quantresearch.execution.mt5_adapter import Mt5Broker
from quantresearch.execution.secrets import load_mt5_credentials

log = logging.getLogger(__name__)

BAR_COUNT = 5
SYMBOL_LIST_LIMIT = 10


def _account_type(mt5: Any, account: Any) -> str:
    return "real" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL else "demo"


def health_snapshot(broker: Any, symbol: str = "XAUUSD") -> dict:
    """The `mt5` block of /api/health. Never raises and never waits on a reconnect.

    status: not_configured (the paper broker is in use, QR_BROKER is not mt5), mock (an MT5 broker running on a
    stand-in terminal module, as in tests), connected, or disconnected. Account and quote fields are filled only
    when connected, by reading the terminal directly rather than through the adapter's reconnecting calls.
    """
    out: dict = {"status": "not_configured", "account_type": None, "balance": None, "symbol": symbol, "bid": None, "ask": None}
    if getattr(broker, "name", "") != "mt5":
        return out
    out["symbol"] = getattr(broker, "symbol_name", symbol)
    mt5 = getattr(broker, "mt5", None)
    stand_in = mt5 is not None and getattr(mt5, "__name__", "") != "MetaTrader5"
    try:
        up = bool(broker.connected())
    except Exception:  # A broken terminal handle must not take the health endpoint down.
        up = False
    out["status"] = "mock" if stand_in else ("connected" if up else "disconnected")
    if not up:
        return out
    try:
        account = mt5.account_info()
        if account is not None:
            out["account_type"], out["balance"] = _account_type(mt5, account), float(account.balance)
        tick = mt5.symbol_info_tick(out["symbol"])
        if tick is not None:
            out["bid"], out["ask"] = float(tick.bid), float(tick.ask)
    except Exception as exc:
        log.warning("MT5 health read failed: %s", exc)
    return out


def run_check(broker: Mt5Broker, symbol: str = "XAUUSD", out: TextIO | None = None) -> int:
    """Run the read-only check against `broker`. Prints a report, returns 0 for PASS and 1 for FAIL."""
    out = out or sys.stdout

    def say(line: str = "") -> None:
        out.write(line + "\n")

    def fail(reason: str, *details: str) -> int:
        say("")
        say(f"FAIL: {reason}")
        for line in details:
            say(f"  {line}")
        return 1

    broker.orders_enabled = False  # This command never sends orders.
    try:
        try:
            connected = broker.connect()
        except BrokerError as exc:
            return fail(str(exc), "Install it on Windows with: pip install MetaTrader5")
        mt5 = broker.mt5
        if not connected:
            return fail("could not attach to the MT5 terminal.", f"MT5 said: {mt5.last_error()}",
                        "Check that the terminal is installed, running and logged in, and that login, password and server are right.")
        terminal = mt5.terminal_info()
        if terminal is None or not getattr(terminal, "connected", False):
            return fail("the terminal is running but not connected to the broker's trade server.")

        account = mt5.account_info()
        if account is None:
            return fail(f"account_info returned nothing: {mt5.last_error()}")
        kind = _account_type(mt5, account)
        say("Account")
        say(f"  login     {account.login}")
        say(f"  server    {account.server}")
        say(f"  type      {kind}")
        say(f"  balance   {account.balance:.2f} {account.currency}")
        say(f"  leverage  1:{account.leverage}")
        if kind == "real":
            say("  WARNING: this is a real-money account. The check is read-only, but test on a demo account first.")

        info = mt5.symbol_info(symbol)
        if info is None:
            names = [s.name for s in (mt5.symbols_get() or ())][:SYMBOL_LIST_LIMIT]
            return fail(f"the terminal has no symbol called {symbol}.",
                        "Brokers often rename gold (XAUUSDm, XAUUSD.a, GOLD). Rerun with --symbol <exact name>.",
                        "First symbols available: " + (", ".join(names) if names else "none listed"))
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return fail(f"no quote for {symbol}: {mt5.last_error()}")
        spread = tick.ask - tick.bid
        points = spread / info.point if getattr(info, "point", 0) else float("nan")
        say("")
        say(f"Quote {symbol}")
        say(f"  bid     {tick.bid}")
        say(f"  ask     {tick.ask}")
        say(f"  spread  {points:.0f} points = ${spread:.3f} per ounce = ${spread * info.trade_contract_size:.2f} per 1.00 lot")
        say(f"  time    {datetime.fromtimestamp(tick.time, timezone.utc).isoformat()}")

        # Position 1 skips the bar that is still forming, so every row shown is final.
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, BAR_COUNT)
        if rates is None or len(rates) == 0:
            return fail(f"no H1 bars for {symbol}: {mt5.last_error()}")
        say("")
        say(f"Last {len(rates)} closed H1 bars (times are the broker's server clock)")
        say("  time              open       high       low        close")
        for r in rates:
            stamp = datetime.fromtimestamp(int(r["time"]), timezone.utc).strftime("%Y-%m-%d %H:%M")
            say(f"  {stamp}  {float(r['open']):<10.3f} {float(r['high']):<10.3f} {float(r['low']):<10.3f} {float(r['close']):<10.3f}")
        say("")
        say("PASS: account, quote and bars all read. No orders were placed.")
        return 0
    finally:
        broker.disconnect()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m quantresearch.execution.mt5_check",
                                     description="Read-only MT5 connection check. Places no orders.")
    parser.add_argument("--symbol", default="XAUUSD", help="Gold symbol as your broker spells it (default XAUUSD)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    credentials = load_mt5_credentials()
    sys.stdout.write("Using " + ("configured credentials" if credentials else "the terminal's saved login") + "\n\n")
    return run_check(Mt5Broker(symbol=args.symbol, credentials=credentials, max_reconnects=1), args.symbol)


if __name__ == "__main__":
    raise SystemExit(main())

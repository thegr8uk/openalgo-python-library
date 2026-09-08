"""
OpenAlgo GTT (Good Till Triggered) Order Examples

A GTT is a price trigger that sits with the broker until LTP crosses the level,
at which point the underlying order is placed automatically. Two shapes:

  SINGLE - one trigger, one order. Exactly one of triggerprice_sl /
           triggerprice_tg carries the level and the other stays 0. The suffix
           is only a directional hint: _sl for a level below LTP, _tg for one
           above. A SINGLE has no stoploss leg to name.

  OCO    - two triggers, one of which fires and cancels the other. Here the
           suffix is a real role: triggerprice_sl is the stoploss leg's trigger
           with stoploss as its limit, triggerprice_tg is the target leg's
           trigger with target as its limit, and all four are required with
           triggerprice_sl < triggerprice_tg.

GTT accepts CNC and NRML only. MIS is refused, because a GTT can sit for days
and MIS is squared off the same session.
"""

from openalgo import api
import json


def print_response(title, response):
    """Helper function to print responses in a readable format"""
    print(f"\n{title}:")
    print(json.dumps(response, indent=2))


client = api(
    api_key="your_api_key_here",
    host="http://127.0.0.1:5000"
)


def place_single_gtt_example():
    """SINGLE GTT: one trigger, one order."""
    # "Buy IDEA if it dips to 9.55, with a LIMIT order at 9.50."
    # LTP is above 9.55, so the trigger sits below it -> triggerprice_sl.
    response = client.placegttorder(
        strategy="My GTT Strategy",
        symbol="IDEA",
        action="BUY",
        exchange="NSE",
        product="CNC",
        quantity=1,
        price_type="LIMIT",
        price=9.50,
        triggerprice_sl=9.55
    )
    print_response("SINGLE GTT - buy the dip", response)

    # "Buy RELIANCE at MARKET if it breaks above 1450."
    # LTP is below 1450, so the trigger sits above it -> triggerprice_tg.
    # price is 0 because pricetype is MARKET.
    response = client.placegttorder(
        strategy="My GTT Strategy",
        symbol="RELIANCE",
        action="BUY",
        exchange="NSE",
        product="CNC",
        quantity=1,
        price_type="MARKET",
        price=0,
        triggerprice_tg=1450
    )
    print_response("SINGLE GTT - breakout buy", response)

    return response.get("trigger_id")


def place_oco_gtt_example():
    """OCO GTT: bracket an existing position with a stop and a target."""
    # "I am short 5 INFY. Stop me out at 1480, take profit at 1620."
    # price is 0 because OCO prices each leg separately: stoploss is the SL
    # leg's limit and target is the target leg's limit.
    response = client.placegttorder(
        strategy="Bracket OCO",
        trigger_type="OCO",
        symbol="INFY",
        action="SELL",
        exchange="NSE",
        product="CNC",
        quantity=5,
        price_type="LIMIT",
        price=0,
        triggerprice_sl=1480,
        stoploss=1478,
        triggerprice_tg=1620,
        target=1622
    )
    print_response("OCO GTT - stop and target", response)
    return response.get("trigger_id")


def modify_gtt_example(trigger_id):
    """Modify is a full replacement, not a patch.

    Every field on the trigger is replaced by what this call sends, so pass
    everything you want to keep rather than only the values that changed.
    """
    response = client.modifygttorder(
        trigger_id=trigger_id,
        strategy="My GTT Strategy",
        symbol="IDEA",
        action="BUY",
        exchange="NSE",
        product="CNC",
        quantity=1,
        price_type="LIMIT",
        price=9.60,          # was 9.50
        triggerprice_sl=9.65  # was 9.55
    )
    print_response("Modify SINGLE GTT", response)

    # trigger_type, symbol, exchange and action cannot be changed. To swap a
    # SINGLE for an OCO, cancel and re-place.


def gtt_orderbook_example():
    """List active GTT triggers.

    Triggered, cancelled, expired and rejected GTTs are filtered out at the
    broker layer, so every row returned is one that can still fire.
    """
    response = client.gttorderbook()
    print_response("GTT Order Book", response)

    for entry in response.get("data", []):
        kind = entry["trigger_type"]          # "single" or "two-leg"
        prices = entry["trigger_prices"]      # ascending; OCO is [sl, tg]
        print(f"  {entry['trigger_id']}  {entry['symbol']:<12} {kind:<9} "
              f"triggers={prices}  ltp={entry['last_price']}")


def cancel_gtt_example(trigger_id):
    """Cancel an active trigger. Cancelling an OCO removes both legs atomically."""
    response = client.cancelgttorder(
        trigger_id=trigger_id,
        strategy="My GTT Strategy"
    )
    print_response("Cancel GTT", response)


def validation_examples():
    """The SDK refuses an impossible trigger spec before anything is sent."""
    # SINGLE with no trigger price at all
    print_response("SINGLE with no trigger", client.placegttorder(
        symbol="IDEA", action="BUY", exchange="NSE",
        product="CNC", quantity=1, price=9.50))

    # SINGLE with both trigger prices set: which one is the trigger?
    print_response("SINGLE with both triggers", client.placegttorder(
        symbol="IDEA", action="BUY", exchange="NSE", product="CNC",
        quantity=1, price=9.50, triggerprice_sl=9.55, triggerprice_tg=20))

    # OCO whose stoploss trigger is not below its target trigger
    print_response("OCO with sl >= tg", client.placegttorder(
        trigger_type="OCO", symbol="INFY", action="SELL", exchange="NSE",
        product="CNC", quantity=5, price=0,
        triggerprice_sl=1700, stoploss=1698,
        triggerprice_tg=1620, target=1622))


def main():
    """Run the GTT examples end to end."""
    try:
        single_id = place_single_gtt_example()
        oco_id = place_oco_gtt_example()
        gtt_orderbook_example()

        if single_id:
            modify_gtt_example(single_id)
            cancel_gtt_example(single_id)
        if oco_id:
            cancel_gtt_example(oco_id)

        validation_examples()
    finally:
        client.close()


if __name__ == "__main__":
    main()

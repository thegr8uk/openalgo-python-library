# -*- coding: utf-8 -*-
"""
OpenAlgo REST API Documentation - GTT (Good Till Triggered) Order Methods
    https://docs.openalgo.in

A GTT is a price trigger that sits with the broker until LTP crosses the level,
at which point the underlying order is placed automatically.

Two trigger types, and picking the wrong one is the usual mistake:

- SINGLE: one trigger, one order. Exactly one of ``triggerprice_sl`` /
  ``triggerprice_tg`` carries the level and the other stays 0. The suffix is
  only a directional hint - ``_sl`` for a level below LTP, ``_tg`` for one
  above - because a SINGLE GTT has no stoploss leg to name.
- OCO: two triggers, one of which fires and cancels the other. Here the suffix
  is a real role: ``triggerprice_sl`` is the stoploss leg's trigger with
  ``stoploss`` as its limit, ``triggerprice_tg`` is the target leg's trigger
  with ``target`` as its limit, and all four are required.

GTT numeric fields go out as JSON numbers rather than the strings the rest of
the order surface sends, because the server validates them as floats.
"""

from .base import BaseAPI

#: Trigger types the API accepts.
GTT_TRIGGER_TYPES = ("SINGLE", "OCO")

#: Products a GTT accepts. MIS is refused server-side: a GTT can sit for days
#: and MIS is squared off the same session.
GTT_PRODUCTS = ("CNC", "NRML")


def _num(value, default=0.0):
    """Coerce a trigger/price field to a float, treating None and "" as unset."""
    if value is None or value == "":
        return default
    return float(value)


class GTTAPI(BaseAPI):
    """
    GTT order management API methods for OpenAlgo.
    Inherits from the BaseAPI class.
    """

    def _gtt_payload(self, *, strategy, trigger_type, symbol, action, exchange,
                     product, quantity, price_type, price,
                     triggerprice_sl, triggerprice_tg, stoploss, target, kwargs):
        """Build and validate the flat GTT body shared by place and modify.

        Validation mirrors the server's own rules rather than adding to them,
        so a caller learns about a bad SINGLE/OCO combination before an order
        leaves the machine. It returns an error dict in the same shape the API
        uses instead of raising, so a trading loop handles it the same way it
        handles every other failure.
        """
        trigger_type = str(trigger_type).upper()
        if trigger_type not in GTT_TRIGGER_TYPES:
            return None, {
                'status': 'error',
                'message': "trigger_type must be 'SINGLE' or 'OCO'.",
                'error_type': 'validation_error'
            }

        sl_trigger = _num(triggerprice_sl)
        tg_trigger = _num(triggerprice_tg)

        if trigger_type == "OCO":
            sl_limit = _num(stoploss, None)
            tg_limit = _num(target, None)
            missing = [
                name for name, value in (
                    ("triggerprice_sl", sl_trigger),
                    ("stoploss", sl_limit),
                    ("triggerprice_tg", tg_trigger),
                    ("target", tg_limit),
                ) if not value
            ]
            if missing:
                return None, {
                    'status': 'error',
                    'message': f"Required for OCO: {', '.join(missing)}.",
                    'error_type': 'validation_error'
                }
            if sl_trigger >= tg_trigger:
                return None, {
                    'status': 'error',
                    'message': 'Stoploss trigger must be less than target trigger '
                               '(triggerprice_sl < triggerprice_tg).',
                    'error_type': 'validation_error'
                }
        else:
            if sl_trigger <= 0 and tg_trigger <= 0:
                return None, {
                    'status': 'error',
                    'message': 'SINGLE GTT requires a positive triggerprice_sl or '
                               'triggerprice_tg.',
                    'error_type': 'validation_error'
                }
            if sl_trigger > 0 and tg_trigger > 0:
                return None, {
                    'status': 'error',
                    'message': 'SINGLE GTT takes exactly one of triggerprice_sl or '
                               'triggerprice_tg; set the other to 0.',
                    'error_type': 'validation_error'
                }
            # SINGLE has no legs, so the per-leg limits are not sent at all.
            sl_limit = None
            tg_limit = None

        payload = {
            "apikey": self.api_key,
            "strategy": strategy,
            "trigger_type": trigger_type,
            "exchange": exchange,
            "symbol": symbol,
            "action": str(action).upper(),
            "product": product,
            "quantity": float(quantity) if float(quantity) % 1 else int(quantity),
            "pricetype": price_type,
            "price": _num(price),
            "triggerprice_sl": sl_trigger,
            "triggerprice_tg": tg_trigger,
            "stoploss": sl_limit,
            "target": tg_limit,
        }
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        return payload, None

    def placegttorder(self, *, strategy="Python", symbol, action, exchange,
                      trigger_type="SINGLE", product="CNC", quantity=1,
                      price_type="LIMIT", price=0,
                      triggerprice_sl=0, triggerprice_tg=0,
                      stoploss=None, target=None, **kwargs):
        """
        Place a GTT (Good Till Triggered) order.

        Parameters:
        - strategy (str, optional): Strategy identifier, used as the broker
          correlation id where supported. Defaults to "Python".
        - symbol (str): Trading symbol in OpenAlgo format. Required.
        - action (str): BUY or SELL. For OCO it applies to both legs. Required.
        - exchange (str): Exchange code. Required.
        - trigger_type (str, optional): "SINGLE" or "OCO". Defaults to "SINGLE".
        - product (str, optional): CNC or NRML. MIS is refused. Defaults to "CNC".
        - quantity (int/float, optional): Order quantity. Integer for equity and
          F&O; a fractional value is only accepted on crypto exchanges. Defaults to 1.
        - price_type (str, optional): LIMIT or MARKET. Defaults to "LIMIT".
        - price (float, optional): SINGLE only - the child order's limit price.
          Send 0 with price_type="MARKET". Ignored for OCO.
        - triggerprice_sl (float, optional): Trigger below LTP. For SINGLE use
          this or triggerprice_tg; for OCO it is the stoploss leg's trigger.
        - triggerprice_tg (float, optional): Trigger above LTP. For SINGLE use
          this or triggerprice_sl; for OCO it is the target leg's trigger.
        - stoploss (float, optional): OCO only - the stoploss leg's limit price.
        - target (float, optional): OCO only - the target leg's limit price.
        - **kwargs: Passed through unchanged for future API extensions.

        Returns:
        dict: {"status": "success", "trigger_id": "..."} on success. Save the
        trigger_id: modify and cancel both need it.

        Notes:
        - last_price is fetched server-side; do not send it.
        - GTT is not available in analyzer (sandbox) mode - the server answers 501.
        """
        payload, error = self._gtt_payload(
            strategy=strategy, trigger_type=trigger_type, symbol=symbol,
            action=action, exchange=exchange, product=product, quantity=quantity,
            price_type=price_type, price=price,
            triggerprice_sl=triggerprice_sl, triggerprice_tg=triggerprice_tg,
            stoploss=stoploss, target=target, kwargs=kwargs,
        )
        if error is not None:
            return error
        return self._post("placegttorder", payload)

    def modifygttorder(self, *, trigger_id, strategy="Python", symbol, action,
                       exchange, trigger_type="SINGLE", product="CNC", quantity=1,
                       price_type="LIMIT", price=0,
                       triggerprice_sl=0, triggerprice_tg=0,
                       stoploss=None, target=None, **kwargs):
        """
        Modify an active GTT trigger.

        Modify is a full replacement, not a patch: every field on the trigger is
        replaced by what this call sends, so pass everything you want to keep
        rather than only the values that changed.

        Parameters:
        - trigger_id (str): The trigger to modify, as returned by placegttorder. Required.
        - Everything else is identical to placegttorder.

        Returns:
        dict: {"status": "success", "trigger_id": "..."} on success.

        What cannot be modified:
        - trigger_type (SINGLE <-> OCO), symbol, exchange and action are fixed.
          Cancel and re-place instead.
        - Only active GTTs are modifiable; triggered, cancelled and expired ones
          are immutable.
        """
        payload, error = self._gtt_payload(
            strategy=strategy, trigger_type=trigger_type, symbol=symbol,
            action=action, exchange=exchange, product=product, quantity=quantity,
            price_type=price_type, price=price,
            triggerprice_sl=triggerprice_sl, triggerprice_tg=triggerprice_tg,
            stoploss=stoploss, target=target, kwargs=kwargs,
        )
        if error is not None:
            return error
        payload["trigger_id"] = str(trigger_id)
        return self._post("modifygttorder", payload)

    def cancelgttorder(self, *, trigger_id, strategy="Python", **kwargs):
        """
        Cancel an active GTT trigger.

        Parameters:
        - trigger_id (str): The trigger to cancel. Required.
        - strategy (str, optional): Strategy identifier, recorded in event logs.
          Defaults to "Python".
        - **kwargs: Passed through unchanged for future API extensions.

        Returns:
        dict: {"status": "success", "trigger_id": "..."} on success.

        Notes:
        - Cancelling an OCO removes both legs atomically; there is no per-leg cancel.
        - Only active triggers can be cancelled. Cancelling one that already fired
          returns whatever the broker says, which may be a success or a
          "trigger not found" error depending on the broker.
        """
        payload = {
            "apikey": self.api_key,
            "strategy": strategy,
            "trigger_id": str(trigger_id),
        }
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        return self._post("cancelgttorder", payload)

    def gttorderbook(self, **kwargs):
        """
        List the active GTT triggers for the authenticated user.

        Triggered, cancelled, expired and rejected GTTs are filtered out at the
        broker layer, so every row returned is one that can still fire.

        Parameters:
        - **kwargs: Passed through unchanged for future API extensions.

        Returns:
        dict: {"status": "success", "data": [...]} where each entry carries
        trigger_id, trigger_type ("single" or "two-leg"), status, symbol,
        exchange, trigger_prices (ascending), last_price, legs, created_at,
        updated_at and expires_at. A SINGLE has one trigger price and one leg;
        an OCO has two of each, stoploss first.
        """
        payload = {"apikey": self.api_key}
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        return self._post("gttorderbook", payload)

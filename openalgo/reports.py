# -*- coding: utf-8 -*-
"""
OpenAlgo Trade Reporting Service API
Sends executed trades asynchronously to the Trade Reporting Service via a background queue.
"""

import atexit
import logging
import os
import queue
import threading
import time
from typing import Any, Dict, Optional, Tuple
import httpx

logger = logging.getLogger("openalgo.reports")


class ReportsAPI:
    """
    Mixin class for sending trade execution reports to the Kunkustox Trade Reporting Service.
    Trades are queued in memory and dispatched by a background worker thread so the strategy's
    trading execution is never blocked.
    """

    def __init__(self, *args, **kwargs):
        # Prevent re-initialization if called multiple times in class hierarchy
        if hasattr(self, "_reports_initialized") and self._reports_initialized:
            return

        self._trade_report_queue: queue.Queue = queue.Queue()
        self._trade_report_worker_thread: Optional[threading.Thread] = None
        self._trade_report_lock = threading.Lock()
        self._trade_report_stop_event = threading.Event()
        self._reports_initialized = True

        # Register atexit handler so pending reports are flushed when the script exits
        atexit.register(self.flush_trade_reports, timeout=5.0)

    def _ensure_worker_started(self):
        """Starts the background worker thread if it is not already running."""
        with self._trade_report_lock:
            if (
                self._trade_report_worker_thread is None
                or not self._trade_report_worker_thread.is_alive()
            ):
                self._trade_report_stop_event.clear()
                self._trade_report_worker_thread = threading.Thread(
                    target=self._process_trade_reports_worker,
                    name="OpenAlgo-TradeReporter",
                    daemon=True,
                )
                self._trade_report_worker_thread.start()

    def _get_trade_reports_config(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Retrieves the domain name and API key from environment variables.
        Normalizes the domain to a full URL (e.g. 'https://reports.kunkustox.shop').
        """
        domain = os.getenv("TRADE_REPORTS_DOMAIN_NAME", "").strip()
        api_key = os.getenv("TRADE_REPORTS_API_KEY", "").strip()

        if not domain:
            logger.warning(
                "TRADE_REPORTS_DOMAIN_NAME environment variable is not set. "
                "Trade report may fail to dispatch."
            )
            return None, api_key

        # Normalize domain: ensure protocol is present
        if not (domain.startswith("http://") or domain.startswith("https://")):
            if domain.startswith("localhost") or domain.startswith("127.0.0.1"):
                domain = f"http://{domain}"
            else:
                domain = f"https://{domain}"

        # Strip trailing slashes
        domain = domain.rstrip("/")
        return domain, api_key

    def _process_trade_reports_worker(self):
        """
        Background worker that continuously pulls trade items from the queue and sends HTTP POST requests.
        """
        client = httpx.Client(timeout=30.0)
        try:
            while not self._trade_report_stop_event.is_set():
                try:
                    # Timeout allows checking the stop event periodically
                    item = self._trade_report_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                if item is None:  # Sentinel value to terminate
                    self._trade_report_queue.task_done()
                    break

                try:
                    self._dispatch_single_report(client, item)
                except Exception as e:
                    logger.error(f"Unexpected error in trade report worker: {e}", exc_info=True)
                finally:
                    self._trade_report_queue.task_done()
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _dispatch_single_report(
        self, client: httpx.Client, payload: Dict[str, Any], max_retries: int = 3
    ):
        """
        Sends the trade report payload to the reports server with automatic retry logic.
        """
        domain, api_key = self._get_trade_reports_config()
        if not domain:
            logger.error(
                f"Cannot send trade report for local_id '{payload.get('local_id')}': "
                "TRADE_REPORTS_DOMAIN_NAME is missing."
            )
            return

        if not api_key:
            logger.warning(
                f"TRADE_REPORTS_API_KEY is not set when sending trade report for local_id '{payload.get('local_id')}'."
            )

        endpoint_url = f"{domain}/api/v1/trades"
        headers = {
            "Content-Type": "application/json"
        }
        if api_key:
            headers["X-API-KEY"] = api_key

        for attempt in range(1, max_retries + 1):
            try:
                response = client.post(endpoint_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    if getattr(self, "verbose", 0):
                        print(f"[TradeReport] Successfully reported trade: {payload.get('local_id')}")
                    return
                elif 400 <= response.status_code < 500:
                    # Client errors (validation, auth) - do not retry
                    logger.error(
                        f"Trade report rejected by server with HTTP {response.status_code}: {response.text} "
                        f"(local_id={payload.get('local_id')})"
                    )
                    return
                else:
                    # 5xx server errors - retry
                    logger.warning(
                        f"Server error HTTP {response.status_code} reporting trade on attempt {attempt}/{max_retries}: {response.text}"
                    )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                logger.warning(
                    f"Network error reporting trade on attempt {attempt}/{max_retries}: {exc}"
                )
            except Exception as exc:
                logger.error(f"Error reporting trade on attempt {attempt}/{max_retries}: {exc}")

            if attempt < max_retries:
                time.sleep(1.0 * attempt)

        logger.error(
            f"Failed to report trade {payload.get('local_id')} after {max_retries} attempts."
        )

    def report_trade(
        self,
        local_id: str,
        exchange_order_id: str,
        strategy: str,
        deployment: Optional[str] = None,
        counter: Optional[int] = None,
        broker: Optional[str] = None,
        deployment_type: Optional[str] = "LIVE",
        symbol: Optional[str] = None,
        exchange: Optional[str] = None,
        txn_type: Optional[str] = None,
        txn_product: Optional[str] = None,
        txn_instrument: Optional[str] = None,
        quantity: Optional[int] = None,
        traded_price: Optional[float] = None,
        created_at: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Enqueues an executed trade to be reported to the Trade Reporting Service asynchronously.
        Does not block the calling strategy thread.

        Parameters:
        - local_id (str): Internal trade identifier (e.g. 'ORD-3169543')
        - exchange_order_id (str): Broker exchange order ID (e.g. '1400000136777523')
        - strategy (str): Strategy identifier (e.g. 'STR0001')
        - deployment (str, optional): Deployment hostname or tag (e.g. 'kotak.kxopenalgo.online')
        - counter (int, optional): Strategy run counter sequence number (e.g. 1, 2)
        - broker (str, optional): Broker name (e.g. 'KOTAK')
        - deployment_type (str, optional): LIVE / PAPER / BACKTEST. Defaults to 'LIVE'.
        - symbol (str, optional): Trading symbol (e.g. 'NIFTY29SEP2622950PE')
        - exchange (str, optional): Exchange name ('NSE', 'NFO', 'BSE', 'MCX')
        - txn_type (str, optional): 'BUY' or 'SELL'
        - txn_product (str, optional): 'MIS', 'NRML', or 'CNC'
        - txn_instrument (str, optional): 'OPTIDX', 'FUTIDX', 'EQ', etc.
        - quantity (int, optional): Executed quantity (e.g. 65)
        - traded_price (float, optional): Executed trade price (e.g. 30.45)
        - created_at (int, optional): Unix timestamp in seconds. If omitted, defaults to current time.
        - **kwargs: Any additional custom fields to include in the JSON payload.

        Returns:
        dict: Immediate confirmation of queuing status (e.g. {'status': 'queued', 'local_id': '...'}).
        """
        # Ensure background queue worker is running
        self._ensure_worker_started()

        if created_at is None:
            created_at = int(time.time())

        payload = {
            "local_id": str(local_id),
            "exchange_order_id": str(exchange_order_id),
            "strategy": str(strategy),
            "deployment": str(deployment) if deployment is not None else "",
            "counter": int(counter) if counter is not None else 1,
            "broker": str(broker) if broker is not None else "",
            "deployment_type": str(deployment_type) if deployment_type is not None else "LIVE",
            "symbol": str(symbol) if symbol is not None else "",
            "exchange": str(exchange) if exchange is not None else "",
            "txn_type": str(txn_type) if txn_type is not None else "",
            "txn_product": str(txn_product) if txn_product is not None else "",
            "txn_instrument": str(txn_instrument) if txn_instrument is not None else "",
            "quantity": int(quantity) if quantity is not None else 0,
            "traded_price": float(traded_price) if traded_price is not None else 0.0,
            "created_at": int(created_at),
        }

        # Include any extra kwargs
        for k, v in kwargs.items():
            if v is not None:
                payload[k] = v

        # Add to non-blocking queue
        self._trade_report_queue.put(payload)

        return {
            "status": "queued",
            "local_id": local_id,
            "queue_size": self._trade_report_queue.qsize(),
        }

    def flush_trade_reports(self, timeout: float = 10.0) -> bool:
        """
        Blocks until all queued trade reports have been processed or timeout occurs.

        Parameters:
        - timeout (float): Maximum seconds to wait.

        Returns:
        bool: True if queue emptied successfully, False if timed out.
        """
        if not hasattr(self, "_trade_report_queue"):
            return True

        if self._trade_report_queue.unfinished_tasks == 0:
            return True

        start_time = time.time()
        while self._trade_report_queue.unfinished_tasks > 0:
            if time.time() - start_time > timeout:
                logger.warning("Timed out waiting for trade reports queue to flush.")
                return False
            time.sleep(0.05)
        return True

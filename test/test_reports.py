# -*- coding: utf-8 -*-
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Allow testing reports module directly without requiring all heavy dependencies of openalgo package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "openalgo")))
import reports
ReportsAPI = reports.ReportsAPI


class TestReportsAPI(unittest.TestCase):
    def setUp(self):
        self.reporter = ReportsAPI()
        # Ensure fresh queue
        while not self.reporter._trade_report_queue.empty():
            self.reporter._trade_report_queue.get_nowait()
            self.reporter._trade_report_queue.task_done()

    def tearDown(self):
        self.reporter.flush_trade_reports(timeout=1.0)
        self.reporter._trade_report_stop_event.set()

    def test_domain_normalization(self):
        with patch.dict(os.environ, {"TRADE_REPORTS_DOMAIN_NAME": "reports.kunkustox.shop", "TRADE_REPORTS_API_KEY": "test-key-123"}):
            domain, api_key = self.reporter._get_trade_reports_config()
            self.assertEqual(domain, "https://reports.kunkustox.shop")
            self.assertEqual(api_key, "test-key-123")

        with patch.dict(os.environ, {"TRADE_REPORTS_DOMAIN_NAME": "https://reports.kunkustox.shop/", "TRADE_REPORTS_API_KEY": "test-key-456"}):
            domain, api_key = self.reporter._get_trade_reports_config()
            self.assertEqual(domain, "https://reports.kunkustox.shop")
            self.assertEqual(api_key, "test-key-456")

        with patch.dict(os.environ, {"TRADE_REPORTS_DOMAIN_NAME": "http://127.0.0.1:8000/", "TRADE_REPORTS_API_KEY": "local-key"}):
            domain, api_key = self.reporter._get_trade_reports_config()
            self.assertEqual(domain, "http://127.0.0.1:8000")
            self.assertEqual(api_key, "local-key")

    def test_report_trade_queues_immediately(self):
        # Measure execution time to verify it doesn't block
        t0 = time.perf_counter()
        res = self.reporter.report_trade(
            local_id="ORD-3169543",
            exchange_order_id="1400000136777523",
            strategy="STR0001",
            deployment="kotak.kxopenalgo.online",
            counter=2,
            broker="KOTAK",
            deployment_type="LIVE",
            symbol="NIFTY29SEP2622950PE",
            exchange="NFO",
            txn_type="BUY",
            txn_product="MIS",
            txn_instrument="OPTIDX",
            quantity=65,
            traded_price=30.45,
            created_at=1790328907
        )
        elapsed = time.perf_counter() - t0

        self.assertEqual(res["status"], "queued")
        self.assertEqual(res["local_id"], "ORD-3169543")
        # Enqueueing should take less than 50 milliseconds (typically < 0.05ms)
        self.assertLess(elapsed, 0.05)

    def test_background_dispatch_invokes_http_post(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.dict(os.environ, {
            "TRADE_REPORTS_DOMAIN_NAME": "https://reports.kunkustox.shop",
            "TRADE_REPORTS_API_KEY": "secret-report-api-key"
        }):
            with patch("httpx.Client.post", return_value=mock_response) as mock_post:
                self.reporter.report_trade(
                    local_id="ORD-9999",
                    exchange_order_id="EX-12345",
                    strategy="STR_TEST",
                    deployment="kotak.kxopenalgo.online",
                    counter=2,
                    broker="KOTAK",
                    deployment_type="LIVE",
                    symbol="NIFTY29SEP2622950PE",
                    exchange="NFO",
                    txn_type="BUY",
                    txn_product="MIS",
                    txn_instrument="OPTIDX",
                    quantity=65,
                    traded_price=30.45,
                    created_at=1790328907
                )

                # Wait for queue to flush
                flushed = self.reporter.flush_trade_reports(timeout=3.0)
                self.assertTrue(flushed)

                mock_post.assert_called_once()
                call_args, call_kwargs = mock_post.call_args
                self.assertEqual(call_args[0], "https://reports.kunkustox.shop/api/v1/trades")
                self.assertEqual(call_kwargs["headers"]["X-API-KEY"], "secret-report-api-key")
                self.assertEqual(call_kwargs["headers"]["Content-Type"], "application/json")
                expected_payload = {
                    "local_id": "ORD-9999",
                    "exchange_order_id": "EX-12345",
                    "strategy": "STR_TEST",
                    "deployment": "kotak.kxopenalgo.online",
                    "counter": 2,
                    "broker": "KOTAK",
                    "deployment_type": "LIVE",
                    "symbol": "NIFTY29SEP2622950PE",
                    "exchange": "NFO",
                    "txn_type": "BUY",
                    "txn_product": "MIS",
                    "txn_instrument": "OPTIDX",
                    "quantity": 65,
                    "traded_price": 30.45,
                    "created_at": 1790328907,
                }
                self.assertEqual(call_kwargs["json"], expected_payload)

    def test_default_created_at(self):
        before = int(time.time())
        res = self.reporter.report_trade(
            local_id="ORD-DEFAULT",
            exchange_order_id="EX-DEF",
            strategy="STR_DEF"
        )
        after = int(time.time())

        # Retrieve the queued item and mark task done
        item = self.reporter._trade_report_queue.get(timeout=1.0)
        self.reporter._trade_report_queue.task_done()

        self.assertGreaterEqual(item["created_at"], before)
        self.assertLessEqual(item["created_at"], after)
        self.assertEqual(item["deployment_type"], "LIVE")
        self.assertEqual(item["counter"], 1)


if __name__ == "__main__":
    unittest.main()

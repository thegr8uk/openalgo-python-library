# -*- coding: utf-8 -*-
"""
OpenAlgo REST API Documentation - Base API Class
    https://docs.openalgo.in
"""

import httpx

class BaseAPI:
    """
    Base class to handle all the API calls to OpenAlgo.
    """

    def __init__(self, api_key, host="http://127.0.0.1:5000", version="v1", timeout=120.0):
        """
        Initialize the api object with an API key and optionally a host URL and API version.

        Attributes:
        - api_key (str): User's API key.
        - host (str): Base URL for the API endpoints. Defaults to localhost.
        - version (str): API version. Defaults to "v1".
        - timeout (float): Request timeout in seconds. Defaults to 120.0 seconds.
        """
        self.api_key = api_key
        self.base_url = f"{host}/api/{version}/"
        self.headers = {
            'Content-Type': 'application/json'
        }
        self.timeout = timeout
        # Single connection-pooled client reused by every REST call. Without
        # this, each request went through the module-level httpx.post/get,
        # which opens and tears down a fresh TCP connection per call, leaving
        # thousands of sockets in TIME_WAIT over a trading session and
        # eventually exhausting ephemeral ports.
        self.client = httpx.Client(
            timeout=timeout,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=120.0,
            ),
        )

    def _post(self, endpoint, payload):
        """POST a JSON payload to an OpenAlgo endpoint and return the parsed body.

        This differs from the older per-mixin ``_make_request`` helpers in one
        way that matters: when the server answers with a non-200 status but a
        JSON body, that body is returned as-is with ``code`` attached, instead
        of being flattened into an ``HTTP 409: {...}`` string. The strategy and
        GTT surfaces put the part a caller has to act on inside that body - a
        409 stop carries ``stop_pending`` and the per-leg outcomes, and a 400
        carries a ``message`` object keyed by field name - so throwing it away
        would leave the caller with nothing to read.
        """
        url = self.base_url + endpoint
        try:
            response = self.client.post(
                url, json=payload, headers=self.headers, timeout=self.timeout
            )
        except httpx.TimeoutException:
            return {
                'status': 'error',
                'message': 'Request timed out. The server took too long to respond.',
                'error_type': 'timeout_error'
            }
        except httpx.ConnectError:
            return {
                'status': 'error',
                'message': 'Failed to connect to the server. Please check if the server is running.',
                'error_type': 'connection_error'
            }
        except httpx.HTTPError as e:
            return {
                'status': 'error',
                'message': f'HTTP error occurred: {str(e)}',
                'error_type': 'http_error'
            }
        except Exception as e:
            return {
                'status': 'error',
                'message': f'An unexpected error occurred: {str(e)}',
                'error_type': 'unknown_error'
            }

        try:
            data = response.json()
        except ValueError:
            return {
                'status': 'error',
                'message': f'HTTP {response.status_code}: {response.text}'
                           if response.status_code != 200
                           else 'Invalid JSON response from server',
                'raw_response': response.text,
                'code': response.status_code,
                'error_type': 'http_error' if response.status_code != 200 else 'json_error'
            }

        if not isinstance(data, dict):
            return {
                'status': 'error',
                'message': 'Unexpected JSON response from server',
                'raw_response': data,
                'code': response.status_code,
                'error_type': 'json_error'
            }

        if response.status_code != 200:
            # Keep the server's own status/message/result fields and note the code.
            data.setdefault('status', 'error')
            data['code'] = response.status_code
        return data

    def close(self):
        """Close the shared HTTP client and release pooled connections."""
        client = getattr(self, "client", None)
        if client is not None:
            client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

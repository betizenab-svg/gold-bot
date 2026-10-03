from __future__ import annotations

import logging
from typing import Dict, List, Optional

import requests

from config.settings import (
    PROXY_FALLBACK_ENABLED,
    PROXY_FALLBACK_MAX_PROXIES,
    PROXY_REQUEST_TIMEOUT_SECONDS,
    PROXYSCRAPE_ENDPOINT,
)


class ProxyAwareHttpClient:
    """HTTP client with direct-first and proxy-on-failure behavior."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        # Retries through one client reuse the list and never re-try a proxy
        # (each sweep can cost minutes of timeouts).
        self._proxy_hosts: Optional[List[str]] = None
        self._tried_proxies: set[str] = set()

    @staticmethod
    def _is_retryable_status(code: int) -> bool:
        return code in {403, 429} or 500 <= code < 600

    def _load_proxy_list(self) -> List[str]:
        if not PROXY_FALLBACK_ENABLED:
            return []

        try:
            response = requests.get(
                PROXYSCRAPE_ENDPOINT,
                timeout=PROXY_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.info("Proxy list fetch unavailable: %s", exc)
            return []

        proxies: List[str] = []
        for line in response.text.splitlines():
            candidate = line.strip()
            if not candidate or ":" not in candidate:
                continue
            proxies.append(candidate)

        return proxies[:PROXY_FALLBACK_MAX_PROXIES]

    def get(self, url: str, **kwargs) -> requests.Response:
        timeout = kwargs.pop("timeout", PROXY_REQUEST_TIMEOUT_SECONDS)

        try:
            response = requests.get(url, timeout=timeout, **kwargs)
            if not self._is_retryable_status(response.status_code):
                return response
            self.logger.info(
                "Direct request returned retryable status %s for %s; trying proxy fallback",
                response.status_code,
                url,
            )
            last_response: Optional[requests.Response] = response
        except requests.RequestException as exc:
            self.logger.info("Direct request failed for %s: %s", url, exc)
            last_response = None

        if not PROXY_FALLBACK_ENABLED:
            if last_response is not None:
                return last_response
            raise requests.RequestException("Direct request failed and proxy fallback is disabled")

        if self._proxy_hosts is None:
            self._proxy_hosts = self._load_proxy_list()
        proxy_hosts = [host for host in self._proxy_hosts if host not in self._tried_proxies]
        if not proxy_hosts:
            if last_response is not None:
                return last_response
            raise requests.RequestException("No untried proxies available from ProxyScrape")

        for host in proxy_hosts:
            self._tried_proxies.add(host)
            proxy_cfg: Dict[str, str] = {
                "http": f"http://{host}",
                "https": f"http://{host}",
            }
            try:
                response = requests.get(url, proxies=proxy_cfg, timeout=timeout, **kwargs)
            except requests.RequestException:
                continue

            if self._is_retryable_status(response.status_code):
                last_response = response
                continue

            self.logger.info("Proxy fallback succeeded for %s via %s", url, host)
            return response

        if last_response is not None:
            return last_response

        raise requests.RequestException("All proxy fallback attempts failed")

"""HTTP client for the local RFID service (rfid_service.py).

The RFID service exposes:
    GET  /health                -> status
    GET  /rfid/latest           -> last RFID tag read
    POST /rfid/write            -> write HEX bytes to USER_DATA bank
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_RFID_URL = "http://127.0.0.1:5000"


class RfidServiceClient:
    """Thin synchronous HTTP client for the RFID service."""

    def __init__(
        self,
        base_url: str = DEFAULT_RFID_URL,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            r = self._session.get(
                f"{self.base_url}/health", timeout=self.timeout
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("RFID /health failed: %s", exc)
            return {"status": "ERROR", "message": str(exc)}

    # ------------------------------------------------------------------
    def get_latest(self) -> dict[str, Any] | None:
        """Return latest tag data dict, or None if no tag / error."""
        try:
            r = self._session.get(
                f"{self.base_url}/rfid/latest", timeout=self.timeout
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            logger.warning("RFID /rfid/latest failed: %s", exc)
            return None

        if payload.get("status") != "OK":
            logger.info(
                "RFID /rfid/latest returned status=%s", payload.get("status")
            )
            return None

        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        return data

    # ------------------------------------------------------------------
    def write(self, start_address: int, data_hex: str) -> dict[str, Any]:
        """Write HEX data to the tag's USER_DATA bank."""
        try:
            r = self._session.post(
                f"{self.base_url}/rfid/write",
                json={"start_address": start_address, "data": data_hex},
                timeout=self.timeout,
            )
            try:
                return r.json()
            except Exception:
                return {
                    "status": "ERROR",
                    "message": f"HTTP {r.status_code}: {r.text}",
                }
        except Exception as exc:
            logger.error("RFID /rfid/write failed: %s", exc)
            return {"status": "ERROR", "message": str(exc)}

    # ------------------------------------------------------------------
    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass
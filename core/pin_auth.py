"""In-memory implementation of the AllDebrid PIN authorisation flow."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading
import time


PIN_TTL_SECONDS = 600
_PIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass
class PinRequest:
    pin: str
    check: str
    apikey: str
    expires_at: float
    activated: bool = False


class PinAuthManager:
    """Keeps short-lived PIN requests safe for Flask's threaded server."""

    def __init__(self, ttl_seconds: int = PIN_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._requests_by_check: dict[str, PinRequest] = {}
        self._lock = threading.Lock()

    def create(self) -> PinRequest:
        """Create a new four-character PIN and its associated API key."""
        with self._lock:
            self._discard_old_requests()

            # A collision is unlikely, but avoid making two active PINs ambiguous.
            active_pins = {
                request.pin
                for request in self._requests_by_check.values()
                if request.expires_at > time.time()
            }
            pin = self._new_pin(active_pins)
            pin_request = PinRequest(
                pin=pin,
                check=secrets.token_hex(20),
                apikey=secrets.token_hex(20),
                expires_at=time.time() + self.ttl_seconds,
            )
            self._requests_by_check[pin_request.check] = pin_request
            return pin_request

    def activate(self, pin: str) -> str:
        """Activate a PIN entered in the local authorisation page.

        Returns ``activated``, ``invalid`` or ``expired``.
        """
        normalized_pin = pin.strip().upper()
        with self._lock:
            for pin_request in self._requests_by_check.values():
                if pin_request.pin != normalized_pin:
                    continue
                if pin_request.expires_at <= time.time():
                    return "expired"
                pin_request.activated = True
                return "activated"
        return "invalid"

    def check(self, pin: str, check_token: str) -> tuple[str, PinRequest | None]:
        """Return the status and request for a Kodi poll of ``/pin/check``."""
        with self._lock:
            pin_request = self._requests_by_check.get(check_token)
            if not pin_request or pin_request.pin != pin.strip().upper():
                return "invalid", None
            if pin_request.expires_at <= time.time():
                return "expired", pin_request
            return "ok", pin_request

    def _new_pin(self, active_pins: set[str]) -> str:
        while True:
            pin = "".join(secrets.choice(_PIN_ALPHABET) for _ in range(4))
            if pin not in active_pins:
                return pin

    def _discard_old_requests(self) -> None:
        """Retain expired requests briefly so Kodi receives PIN_EXPIRED, not invalid."""
        cutoff = time.time() - self.ttl_seconds
        self._requests_by_check = {
            check: pin_request
            for check, pin_request in self._requests_by_check.items()
            if pin_request.expires_at > cutoff
        }

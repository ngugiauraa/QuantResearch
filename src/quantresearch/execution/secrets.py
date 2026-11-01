"""Credential handling.

Credentials come from the Windows Credential Manager through `keyring`, or failing that from
environment variables set for one session. They are never written to a config file, sent to the
frontend or written to logs. `redact` removes secret-looking fields from anything being recorded.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

SERVICE = "QuantResearch.MT5"
SENSITIVE = re.compile(r"pass(word)?|secret|token|api_?key|credential|login|account_?(number|id)", re.I)


@dataclass
class Mt5Credentials:
    login: int
    password: str = field(repr=False)
    server: str = ""

    def as_kwargs(self) -> dict:
        out = {"login": int(self.login), "password": self.password}
        if self.server:
            out["server"] = self.server
        return out

    def __repr__(self) -> str:
        return f"Mt5Credentials(login=<hidden>, password=<hidden>, server={self.server!r})"


def load_mt5_credentials(keyring_module=None) -> Mt5Credentials | None:
    """Returns None if nothing is configured, in which case the terminal's own login is used."""
    keyring_module = keyring_module if keyring_module is not None else _try_keyring()
    if keyring_module is not None:
        login = keyring_module.get_password(SERVICE, "login")
        password = keyring_module.get_password(SERVICE, "password")
        if login and password:
            return Mt5Credentials(int(login), password, keyring_module.get_password(SERVICE, "server") or "")
    login, password = os.environ.get("QR_MT5_LOGIN"), os.environ.get("QR_MT5_PASSWORD")
    if login and password:
        return Mt5Credentials(int(login), password, os.environ.get("QR_MT5_SERVER", ""))
    return None


def _try_keyring():
    try:
        import keyring  # type: ignore
    except ImportError:
        return None
    return keyring


def redact(value):
    """Copy a structure with secret-looking fields replaced. Used on everything that gets logged."""
    if isinstance(value, dict):
        return {k: "<redacted>" if SENSITIVE.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value

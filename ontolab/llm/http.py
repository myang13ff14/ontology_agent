"""HTTPS helper: verify certificates against the certifi bundle.

Some Python installs (notably python.org builds on macOS) ship without a system
certificate store, which makes every HTTPS request fail verification. Using
certifi's bundle fixes that without ever turning verification off.
"""

from __future__ import annotations

import ssl
from functools import lru_cache


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())

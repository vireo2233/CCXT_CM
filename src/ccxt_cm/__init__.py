"""CCXT_CM is an independent extension library, not an official CCXT distribution."""

import ccxt

from .base import AsyncExchange, Exchange
from .capabilities import SpecialMethod, capability, require_capabilities, special_methods
from .exchanges.bifu import BIFU_EXTENSION
from .registry import (
    Extension,
    Mode,
    Registry,
    create_exchange,
    exchange_class,
    list_exchanges,
    load_extensions,
    register,
)

if BIFU_EXTENSION.id not in ccxt.exchanges:
    register(BIFU_EXTENSION)

__version__ = "0.1.0"
__all__ = [
    "AsyncExchange",
    "Exchange",
    "Extension",
    "Mode",
    "Registry",
    "SpecialMethod",
    "capability",
    "create_exchange",
    "exchange_class",
    "list_exchanges",
    "load_extensions",
    "register",
    "require_capabilities",
    "special_methods",
]

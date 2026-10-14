"""Courier lookup: partner name (or AWB shape) -> adapter instance.

Adding a courier means writing one adapter module and appending its class to
``COURIER_CLASSES``. Names and aliases are matched loosely so spreadsheet
spellings like "Xpress Bees", "delivery" or "VOLMO" all resolve.
"""
from __future__ import annotations

import re
from typing import Any

from .base import BaseCourier
from .couriers.bluedart import BlueDart
from .couriers.delhivery import Delhivery
from .couriers.ekart import Ekart
from .couriers.shadowfax import Shadowfax
from .couriers.valmo import Valmo
from .couriers.xpressbees import XpressBees
from .http_client import HttpClient

COURIER_CLASSES: list[type[BaseCourier]] = [
    BlueDart, Delhivery, Ekart, Shadowfax, Valmo, XpressBees,
]

# Fallback only, used when the partner column is blank: AWB shape -> courier.
AWB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^VL\d{10,}$", re.I), "VALMO"),
    (re.compile(r"^(SF|R)\w{8,}$", re.I), "SHADOWFAX"),
    (re.compile(r"^SDLC\d{6,}$", re.I), "EKART"),
    (re.compile(r"^\d{11}$"), "BLUEDART"),
    (re.compile(r"^1950\d{11}$"), "DELHIVERY"),
    (re.compile(r"^\d{13,14}$"), "XPRESSBEES"),
]


def normalize_name(name: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


class CourierRegistry:
    """Builds and caches one adapter instance per courier."""

    def __init__(self, config: dict[str, Any] | None = None,
                 client_factory=None) -> None:
        self.config = config or {}
        self._client_factory = client_factory or (lambda courier_cls: HttpClient(
            impersonate=self.config.get("impersonate", "chrome"),
            timeout=self.config.get("timeout", 30),
            retries=self.config.get("retries", 3),
            min_interval=courier_cls.min_interval,
            proxy=self.config.get("proxy"),
        ))
        self._by_key: dict[str, type[BaseCourier]] = {}
        self._instances: dict[str, BaseCourier] = {}

        for cls in COURIER_CLASSES:
            for label in (cls.name, *cls.aliases):
                self._by_key[normalize_name(label)] = cls

    def known_partners(self) -> list[str]:
        return sorted(cls.name for cls in COURIER_CLASSES)

    def resolve_name(self, partner: Any, awb: Any = None) -> type[BaseCourier] | None:
        key = normalize_name(partner)
        if key and key in self._by_key:
            return self._by_key[key]
        # Partial match: "DELHIVERY SURFACE" -> DELHIVERY
        if key:
            for known, cls in self._by_key.items():
                if len(known) >= 4 and (known in key or key in known):
                    return cls
        if awb:
            text = str(awb).strip()
            for pattern, name in AWB_PATTERNS:
                if pattern.match(text):
                    return self._by_key[normalize_name(name)]
        return None

    def alternatives(self, awb: Any, exclude: str = "") -> list[str]:
        """Couriers whose AWB shape fits, other than `exclude` - tried when the
        named courier does not know the AWB (the order system named the wrong one)."""
        text = str(awb or "").strip()
        names = [name for pattern, name in AWB_PATTERNS if pattern.match(text)]
        return [n for n in dict.fromkeys(names) if n != normalize_name(exclude)]

    def get(self, partner: Any, awb: Any = None) -> BaseCourier | None:
        cls = self.resolve_name(partner, awb)
        if cls is None:
            return None
        if cls.name not in self._instances:
            courier_cfg = self.config.get("couriers", {}).get(cls.name, {})
            self._instances[cls.name] = cls(client=self._client_factory(cls),
                                            config=courier_cfg)
        return self._instances[cls.name]

"""Deep modules of the DeadlockForge engine.

Public surface (frozen):
    - dataset:    load_catalog(), get_catalog()
    - advisor:    recommend()
    - calculator: compute()
    - builds:     equip(), unequip(), validate()
"""

from __future__ import annotations

from .advisor import recommend
from .builds import equip, unequip, validate
from .calculator import CDR_CAP, compute
from .dataset import Catalog, get_catalog, load_catalog

__all__ = [
    "recommend",
    "equip",
    "unequip",
    "validate",
    "compute",
    "CDR_CAP",
    "Catalog",
    "load_catalog",
    "get_catalog",
]

"""Checker package — auto-discovers every checker module on import.

Each checker module ends with ``register(MyChecker)``; importing the module
runs that call and adds the class to the global registry. This file walks
the package directory and imports every submodule so that registration
happens automatically.

The upshot for contributors: adding a new checker is *one new file* in this
directory. There is no barrel to edit and no "I added the file but the
checker didn't show up" footgun.

``base.py`` and any ``_``-prefixed module are skipped — they're framework
or shared-helper code, not checkers.
"""

import importlib
import pkgutil

for _info in pkgutil.iter_modules(__path__):
    if _info.name == "base" or _info.name.startswith("_"):
        continue
    importlib.import_module(f"{__name__}.{_info.name}")

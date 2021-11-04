from __future__ import annotations

import os
import sys

import importlib_metadata as metadata

PACKAGE_NAME = "brood"
__version__ = metadata.version(PACKAGE_NAME)
__python_version__ = ".".join(map(str, sys.version_info))

ON_WINDOWS = os.name == "nt"

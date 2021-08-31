import sys
from importlib import metadata

PACKAGE_NAME = "brood"
__version__ = metadata.version(PACKAGE_NAME)
__python_version__ = ".".join(map(str, sys.version_info))

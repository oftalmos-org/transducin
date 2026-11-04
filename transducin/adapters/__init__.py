# transducin/adapters/__init__.py
# Concrete DeviceAdapter implementations.
# Add new equipment adapters as modules in this package.
from transducin.adapters.cirrus import CirrusDcmAdapter
from transducin.adapters.cirrus_pdf import CirrusPdfAdapter
from transducin.adapters.revo import RevoOptAdapter

__all__ = ["CirrusDcmAdapter", "CirrusPdfAdapter", "RevoOptAdapter"]

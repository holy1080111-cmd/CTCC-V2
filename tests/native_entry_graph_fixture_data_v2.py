"""Serialization of locally generated synthetic benchmark DATA, no owner API.

This helper is never imported by runtime product code. A retained test artifact
can measure exact graph traversal without importing the old fixture producer.
"""

import copyreg
import io
import pickle
from types import MappingProxyType


def restore_mapping_proxy_v2(value):
    return MappingProxyType(value)


def _proxy(value):
    return restore_mapping_proxy_v2, (dict(value),)


def synthetic_graph_bytes_v2(value):
    output = io.BytesIO()
    writer = pickle.Pickler(output, protocol=5)
    writer.dispatch_table = copyreg.dispatch_table | {MappingProxyType: _proxy}
    writer.dump(value)
    return output.getvalue()

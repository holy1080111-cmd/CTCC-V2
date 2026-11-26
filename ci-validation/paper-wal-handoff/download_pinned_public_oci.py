"""Stage only the reviewed public N397/N434 compressed Linux/amd64 layers.

Run with isolated Python and the absolute path of a fresh, file-only support
extraction. Each public-assets directory starts with its two archived index and
manifest JSON files. The original assemblers consume the resulting .blob files.
"""

import hashlib
import http.client
import json
import os
import re
import ssl
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit


_REGISTRY = "registry-1.docker.io"
_AUTH = "auth.docker.io"
_CDN = "production.cloudfront.docker.com"
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}", re.ASCII)
_TOKEN = re.compile(r"[A-Za-z0-9._~+-]{1,16383}", re.ASCII)
_CHUNK = 1024 * 1024
_CHUNK_LIMIT = 128 * _CHUNK
_METADATA_LIMIT = 2 * _CHUNK
_CHAIN = "PINNED_OCI_COMPRESSED_CHAIN_VERIFIED.json"
_DIFF_IDS = "PINNED_OCI_ALL_DIFFIDS_VERIFIED.json"
_VIRTUAL = "PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json"
_RECEIPT = "PINNED_PUBLIC_OCI_COMPRESSED_BLOBS_VERIFIED.json"


class Refused(ValueError):
    """Fixed redacted admission failure."""


@dataclass(frozen=True, slots=True)
class Image:
    directory: str
    repository: str
    index_digest: str
    manifest_digest: str
    chain_sha256: str
    diffids_sha256: str
    inventory_sha256: str
    layer_count: int
    compressed_bytes: int


_IMAGES = (
    Image(
        "N397-validation-wsl-pg-authenticity-20261121",
        "library/postgres",
        "sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995",
        "sha256:aa90e97ee862e558111d34cfb8b2c4bec768c2b039fb791341686928560263b3",
        "db011a08d441d48ad1841d9d28dbd6671756dcdc6d56e549fce62fbb9715f605",
        "a07a704812da6def0876d42771477cc826340ea5e81ef3e8f08e75284b6a7a6e",
        "603f68e5f1059ea01a25ecc0a79a0f7f041b4640a24f5f16fcc3b3b40524f382",
        10,
        117_190_808,
    ),
    Image(
        "N434-pinned-linux-python-runtime-20261121",
        "library/python",
        "sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9",
        "sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b",
        "1bd7dc416b79ce021934b361c0d5ca41da0ddbfeb01f82f8147c7edda8b62ce2",
        "1aeb49a7ec1749a7d403fa5cbfebe0b44eefe550da34d7842afef63c78722074",
        "9b79bfd725a69bbf9ae2d386c3219f5110ffbc2efa5e4b7435552043c87c0a6a",
        4,
        43_241_091,
    ),
)


def _require(condition):
    if condition is not True:
        raise Refused("pinned_public_oci_input_refused")


def _unique(pairs):
    result = {}
    for name, value in pairs:
        _require(name not in result)
        result[name] = value
    return result


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)


def _file_name(digest, extension):
    _require(type(digest) is str and _DIGEST.fullmatch(digest) is not None)
    return "sha256-" + digest[7:] + extension


def _direct_directory(path):
    _require(path.is_absolute() and path.resolve(strict=True) == path)
    _require(stat.S_ISDIR(path.lstat().st_mode))
    return path


def _file_identity(file):
    info = os.fstat(file.fileno())
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_sealed(path, expected):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as data:
        before = _file_identity(data)
        _require(0 < before[2] <= _METADATA_LIMIT)
        raw = data.read(_METADATA_LIMIT + 1)
        _require(len(raw) == before[2] and _file_identity(data) == before)
    _require(hashlib.sha256(raw).hexdigest() == expected)
    return raw


def _layer_inputs(root, image):
    directory = _direct_directory(root / "validation-results" / image.directory)
    assets = _direct_directory(directory / "public-assets")
    chain = _json(_read_sealed(directory / _CHAIN, image.chain_sha256))
    diffids = _json(_read_sealed(directory / _DIFF_IDS, image.diffids_sha256))
    _read_sealed(directory / _VIRTUAL, image.inventory_sha256)
    index_name = _file_name(image.index_digest, ".json")
    manifest_name = _file_name(image.manifest_digest, ".json")
    _require(set(os.listdir(assets)) == {index_name, manifest_name})
    _require(
        chain["repository"] == "docker.io/" + image.repository
        and chain["source_pinned_index"] == diffids["source_pinned_index"] == image.index_digest
        and chain["index_file"] == index_name
        and chain["manifest_file"] == manifest_name
        and diffids["linux_amd64_manifest"] == image.manifest_digest
        and diffids["virtual_rootfs_inventory_sha256"] == image.inventory_sha256
    )
    index = _json(_read_sealed(assets / index_name, image.index_digest[7:]))
    child_raw = _read_sealed(assets / manifest_name, image.manifest_digest[7:])
    manifest = _json(child_raw)
    children = [
        row for row in index["manifests"]
        if row.get("platform", {}).get("os") == "linux"
        and row.get("platform", {}).get("architecture") == "amd64"
        and row.get("platform", {}).get("variant") in (None, "v1")
    ]
    _require(index["schemaVersion"] == manifest["schemaVersion"] == 2)
    _require(children == [chain["linux_amd64_child"]])
    _require(children[0]["digest"] == image.manifest_digest and children[0]["size"] == len(child_raw))
    layers, measured, diff_layers = manifest["layers"], chain["layers"], diffids["layers"]
    _require(len(layers) == len(measured) == len(diff_layers) == image.layer_count)
    _require(chain["rootfs_diff_ids"] == [row["diff_id"] for row in diff_layers])
    result = []
    for ordinal, (layer, observed, diff) in enumerate(zip(layers, measured, diff_layers, strict=True)):
        digest, size = layer["digest"], layer["size"]
        name = _file_name(digest, ".blob")
        _require(
            layer["mediaType"] == "application/vnd.oci.image.layer.v1.tar+gzip"
            and type(size) is int and 0 < size <= _CHUNK_LIMIT
            and observed["digest"] == diff["compressed_digest"] == digest
            and observed["size"] == size and observed["file"] == name
            and diff["layer"] == ordinal
            and observed["download_hosts"] == [_REGISTRY, _CDN]
        )
        result.append((name, digest, size))
    _require(
        sum(layer[2] for layer in result) == image.compressed_bytes
        and len({layer[0] for layer in result}) == image.layer_count
    )
    return assets, frozenset({index_name, manifest_name}), tuple(result)


@contextmanager
def _get(context, host, path, *, token=None):
    # Direct TLS. http.client never consults proxy, netrc or cookie settings.
    connection = http.client.HTTPSConnection(host, port=443, timeout=30, context=context)
    headers = {"Accept-Encoding": "identity"}
    if token is not None:
        _require(host == _REGISTRY)
        headers["Authorization"] = "Bearer " + token
    try:
        connection.request("GET", path, headers=headers)
        with connection.getresponse() as response:
            yield response
    finally:
        connection.close()


def _header(response, name):
    values = response.headers.get_all(name, [])
    _require(len(values) <= 1)
    return values[0] if values else None


def _anonymous_pull_token(context, repository):
    query = urlencode({"service": "registry.docker.io", "scope": f"repository:{repository}:pull"})
    with _get(context, _AUTH, "/token?" + query) as response:
        _require(response.status == 200 and _header(response, "Content-Encoding") in (None, "identity"))
        raw = response.read(32769)
        _require(0 < len(raw) <= 32768)
    token = _json(raw)["token"]
    _require(type(token) is str and _TOKEN.fullmatch(token) is not None)
    return token


def _cloudfront_path(location, digest):
    _require(
        type(location) is str and 0 < len(location) <= 8192
        and all(33 <= ord(char) <= 126 for char in location)
    )
    parts = urlsplit(location)
    blob = digest[7:]
    _require(
        parts.scheme == "https" and parts.netloc in (_CDN, _CDN + ":443")
        and not parts.fragment
        and parts.path == f"/registry-v2/docker/registry/v2/blobs/sha256/{blob[:2]}/{blob}/data"
    )
    pairs = parse_qsl(parts.query, strict_parsing=True, max_num_fields=3)
    _require(
        len(pairs) == 3 and all(value for _, value in pairs)
        and {key for key, _ in pairs} in (
            {"Expires", "Signature", "Key-Pair-Id"}, {"Policy", "Signature", "Key-Pair-Id"}
        )
    )
    return parts.path + "?" + parts.query


def _verify_bytes(source, expected, size, *, output=None, deadline=None):
    digestor, length = hashlib.sha256(), 0
    while chunk := source.read(min(_CHUNK, size + 1 - length)):
        length += len(chunk)
        _require(length <= size and (deadline is None or time.monotonic() < deadline))
        digestor.update(chunk)
        if output is not None:
            output.write(chunk)
    _require(length == size and digestor.hexdigest() == expected[7:])


def _write_blob(response, path, digest, size):
    _require(response.status == 200 and _header(response, "Content-Encoding") in (None, "identity"))
    length = _header(response, "Content-Length")
    _require(length is None or length == str(size))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        _verify_bytes(response, digest, size, output=output, deadline=time.monotonic() + 300)
        output.flush()
        os.fsync(output.fileno())


def _fetch_blob(context, repository, token, assets, layer):
    name, digest, size = layer
    with _get(context, _REGISTRY, f"/v2/{repository}/blobs/{digest}", token=token) as response:
        if response.status == 200:
            _write_blob(response, assets / name, digest, size)
            return
        _require(response.status in (301, 302, 303, 307, 308))
        target = _cloudfront_path(_header(response, "Location"), digest)
    # Independent TLS connection. The registry bearer is never forwarded.
    with _get(context, _CDN, target) as response:
        _write_blob(response, assets / name, digest, size)


def _readback(assets, fixed_names, layers):
    _require(set(os.listdir(assets)) == fixed_names | {name for name, _, _ in layers})
    for name, digest, size in layers:
        descriptor = os.open(assets / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as data:
            before = _file_identity(data)
            _require(before[2] == size)
            _verify_bytes(data, digest, size)
            _require(_file_identity(data) == before)
    _sync_directory(assets)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    _require(sys.platform == "linux" and sys.flags.isolated == 1 and len(sys.argv) == 2)
    _require(os.geteuid() != 0)
    root = _direct_directory(Path(sys.argv[1]))
    receipt = root / _RECEIPT
    _require(not os.path.lexists(receipt))
    inputs = tuple(_layer_inputs(root, image) for image in _IMAGES)
    # Avoid create_default_context's optional ambient SSLKEYLOGFILE.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile="/etc/ssl/certs/ca-certificates.crt")
    records = []
    for image, (assets, fixed_names, layers) in zip(_IMAGES, inputs, strict=True):
        token = _anonymous_pull_token(context, image.repository)
        try:
            for layer in layers:
                _fetch_blob(context, image.repository, token, assets, layer)
        finally:
            del token
        _readback(assets, fixed_names, layers)
        records.append({
            "repository": image.repository,
            "index_digest": image.index_digest,
            "manifest_digest": image.manifest_digest,
            "compressed_chain_sha256": image.chain_sha256,
            "diffid_receipt_sha256": image.diffids_sha256,
            "rootfs_inventory_sha256": image.inventory_sha256,
            "layers": [{"name": name, "digest": digest, "bytes": size} for name, digest, size in layers],
        })
    data = {
        "schema": "ctcc.hosted.fresh_custody_pinned_public_oci_blobs.v1",
        "status": "PINNED_PUBLIC_OCI_COMPRESSED_BLOBS_VERIFIED",
        "images": records,
        "layer_count": sum(image.layer_count for image in _IMAGES),
        "compressed_bytes": sum(image.compressed_bytes for image in _IMAGES),
        "execution_authority": False,
    }
    raw = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    digest = hashlib.sha256(raw).hexdigest()
    _require(_read_sealed(receipt, digest) == raw)
    _sync_directory(root)
    print("PINNED_PUBLIC_OCI_COMPRESSED_BLOBS_VERIFIED=" + digest)


if __name__ == "__main__":
    try:
        main()
    except (OSError, http.client.HTTPException, ValueError, LookupError, TypeError, AttributeError, RecursionError):
        print("PINNED_PUBLIC_OCI_COMPRESSED_BLOBS_REFUSED", file=sys.stderr)
        raise SystemExit(2) from None

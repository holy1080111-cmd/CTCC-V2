"""Bind actual native-platform wheel bytes to official PyPI release metadata."""

import argparse
import hashlib
import json
import re
import tomllib
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


def digest(data):
    return hashlib.sha256(data).hexdigest()


def wheel_record(path):
    raw = path.read_bytes()
    with zipfile.ZipFile(path) as wheel:
        names = [
            name
            for name in wheel.namelist()
            if name.count("/") == 1 and name.endswith(".dist-info/METADATA")
        ]
        if len(names) != 1:
            raise ValueError("wheel METADATA count differs")
        metadata_raw = wheel.read(names[0])
        metadata = BytesParser().parsebytes(metadata_raw)
    name = re.sub(r"[-_.]+", "-", metadata["Name"]).lower()
    return {
        "name": name,
        "distribution_name": metadata["Name"],
        "version": metadata["Version"],
        "filename": path.name,
        "sha256": digest(raw),
        "byte_size": len(raw),
        "metadata_sha256": digest(metadata_raw),
    }


def official(project, workspace):
    name, version = project
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    request = Request(
        url, headers={"User-Agent": "CTCC-reproducible-dependency-audit/1.0"}
    )
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.url != url:
            raise ValueError("official PyPI metadata response mismatch")
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("official metadata exceeds bound")
    payload = json.loads(raw)
    if payload["info"]["version"] != version:
        raise ValueError("official metadata version mismatch")
    (workspace / "official-metadata").mkdir(exist_ok=True)
    (workspace / "official-metadata" / f"{name}-{version}.json").write_bytes(raw)
    return project, (payload, digest(raw), url)


def resolver_requirements(source: Path):
    source_config = tomllib.loads((source / "pyproject.toml").read_text())
    return list(
        dict.fromkeys(
            source_config["project"]["dependencies"]
            + source_config["project"]["optional-dependencies"]["test"]
            + source_config["project"]["optional-dependencies"]["validation"]
            + ["pip==26.2.1", "setuptools==84.0.0"]
        )
    )


def prepare_workspace(workspace: Path, source: Path):
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "validation.in"
    content = ("\n".join(resolver_requirements(source)) + "\n").encode()
    if target.exists() and target.read_bytes() != content:
        raise ValueError("existing resolver input changed; use a fresh workspace")
    target.write_bytes(content)
    for platform_name in ("linux", "windows"):
        (workspace / f"{platform_name}-wheels").mkdir(exist_ok=True)
    print(json.dumps({"resolver_input_sha256": digest(content)}))


def build_locks(workspace: Path, source: Path, targets=("linux", "windows")):
    ROOT = workspace.resolve(strict=True)
    SOURCE = source.resolve(strict=True)
    OUT = SOURCE / "requirements"
    OUT.mkdir(exist_ok=True)
    requirements = resolver_requirements(SOURCE)
    if (ROOT / "validation.in").read_text().splitlines() != requirements:
        raise ValueError(
            "resolver inputs differ from current source and pinned bootstrap"
        )

    records = {
        target: sorted(
            [wheel_record(path) for path in (ROOT / f"{target}-wheels").glob("*.whl")],
            key=lambda row: row["name"],
        )
        for target in targets
    }
    projects = sorted(
        {(row["name"], row["version"]) for rows in records.values() for row in rows}
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        official_map = dict(pool.map(lambda project: official(project, ROOT), projects))

    for target, rows in records.items():
        required_names = {
            re.sub(r"[-_.]+", "-", re.match(r"[A-Za-z0-9_.-]+", item)[0]).lower()
            for item in requirements
        }
        if not required_names.issubset({row["name"] for row in rows}) or len(
            {row["name"] for row in rows}
        ) != len(rows):
            raise ValueError("wheel inventory incomplete or duplicate")
        for row in rows:
            payload, metadata_hash, metadata_url = official_map[
                (row["name"], row["version"])
            ]
            matches = [
                item for item in payload["urls"] if item["filename"] == row["filename"]
            ]
            if len(matches) != 1:
                raise ValueError("wheel filename absent from official metadata")
            item = matches[0]
            if (
                item["digests"]["sha256"] != row["sha256"]
                or item["size"] != row["byte_size"]
                or item["packagetype"] != "bdist_wheel"
                or item["yanked"]
            ):
                raise ValueError(
                    "wheel differs from official non-yanked release metadata"
                )
            if not item["url"].startswith("https://files.pythonhosted.org/"):
                raise ValueError("wheel distribution host mismatch")
            row.update(
                url=item["url"],
                official_metadata_url=metadata_url,
                official_metadata_sha256=metadata_hash,
            )
        lock_name = f"validation-{target}-py312.lock"
        lock = (
            "# Native CPython 3.12 " + target + " x86_64 validation environment.\n"
            "# Fully hashed wheels; regenerate using the declared source and native platform.\n"
            "# Local ctcc-v2 source is separately manifest-bound and installed --no-deps --no-build-isolation.\n"
            "--index-url https://pypi.org/simple\n--only-binary :all:\n\n"
            + "\n".join(
                f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}"
                for row in rows
            )
            + "\n"
        ).encode()
        (OUT / lock_name).write_bytes(lock)
        declaration = (SOURCE / "pyproject.toml").read_bytes()
        manifest = {
            "schema": "ctcc.validation_dependency_lock.v1",
            "target": target,
            "architecture": "x86_64",
            "python": "3.12",
            "index": "https://pypi.org/simple",
            "source_pyproject_sha256": digest(declaration),
            "lock_sha256": digest(lock),
            "resolver_input_sha256": digest((ROOT / "validation.in").read_bytes()),
            "resolver_input": (ROOT / "validation.in").read_text().splitlines(),
            "observed_at": datetime.now(UTC).isoformat(),
            "packages": rows,
            "accepted_full_validation_baseline": False,
        }
        (OUT / f"validation-{target}-py312.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )
        print(
            json.dumps(
                {"target": target, "packages": len(rows), "lock_sha256": digest(lock)}
            )
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--target", choices=("linux", "windows"), action="append")
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare_workspace(args.workspace, args.source)
    else:
        build_locks(args.workspace, args.source, args.target or ("linux", "windows"))


if __name__ == "__main__":
    main()

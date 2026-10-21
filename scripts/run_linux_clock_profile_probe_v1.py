"""Bounded, credential-free host/container measurement; never clock admission."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

BASE_IMAGE = "python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"
PROBE_PINS = {
    "probe_linux_clock_profile_v1.c": "ab41d2b1580ac727dfd56124e2a99fb808558e459578888c08560f22dcd07e4b",
    "probe_linux_clock_profile_v1.py": "49d79ccb0334575979998aa2aaae03ec11af5c96d7d0d81904f4a200e49b5471",
}
OUTPUT_LIMIT = 256 * 1024
ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(output, records, name, command, *, timeout):
    record = {"stage": name, "exit_code": None, "error": None}
    records.append(record)
    paths = [output / f"{name}.{suffix}" for suffix in ("stdout", "stderr")]
    started = time.monotonic()
    process = None
    try:
        with paths[0].open("xb") as stdout, paths[1].open("xb") as stderr:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=ENVIRONMENT,
                start_new_session=True,
            )
            while process.poll() is None:
                if any(path.stat().st_size > OUTPUT_LIMIT for path in paths):
                    record["error"] = "output_limit_exceeded"
                    break
                if time.monotonic() - started > timeout:
                    record["error"] = "timeout"
                    break
                time.sleep(0.05)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            record["exit_code"] = process.wait(timeout=5)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Never include environment or arbitrary error text.
        record["error"] = type(error).__name__
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            record["exit_code"] = process.wait(timeout=5)
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - started, 6)
        for path in paths:
            if path.exists():
                size = path.stat().st_size
                field = path.suffix.removeprefix(".")
                record[field + "_observed_bytes"] = size
                record[field + "_truncated"] = size > OUTPUT_LIMIT
                if size > OUTPUT_LIMIT:
                    with path.open("r+b") as stream:
                        stream.truncate(OUTPUT_LIMIT)
                    record["error"] = "output_limit_exceeded"
                record[field + "_sha256"] = _sha(path)
    return record["exit_code"] == 0 and record["error"] is None


def _json_output(output, records, stage):
    try:
        raw = (output / f"{stage}.stdout").read_bytes()
        value = json.loads(raw)
        if type(value) is not dict:
            raise ValueError
        (output / f"{stage}.json").write_bytes(raw)
        return value
    except (OSError, ValueError) as error:
        records[-1]["output_error"] = type(error).__name__
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or not re.fullmatch("[a-f0-9]{40}", args.expected_head):
        raise SystemExit("Linux and exact expected commit required")
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    records = []
    metadata = {
        "schema_version": "ctcc.linux_clock_profile_measurement.v1",
        "scope": "measurement_not_clock_admission",
        "clock_admission": False,
        "automatic_profile_admission": False,
        "pinned_base": BASE_IMAGE,
        "binary_origin": "host_gcc_same_elf_executed_on_host_and_container_glibc",
        "container_compilation": False,
        "host_credentials_or_env_forwarded": False,
        "steps": records,
        "measurement_complete": False,
    }
    docker_config = output / "empty-docker-config"
    docker_config.mkdir(mode=0o700)
    docker = ["/usr/bin/docker", "--config", str(docker_config)]
    container_name = "ctcc-clock-profile-" + uuid4().hex
    attempted_container = False
    try:
        for field, revision in (("head", "HEAD"), ("tree", "HEAD^{tree}")):
            stage = "git-" + field
            if not _run(
                output,
                records,
                stage,
                ["/usr/bin/git", "-C", str(root), "rev-parse", revision],
                timeout=5,
            ):
                raise ValueError
            value = (output / f"{stage}.stdout").read_text().strip()
            if not re.fullmatch("[a-f0-9]{40}", value):
                raise ValueError
            metadata[field] = value
        if metadata["head"] != args.expected_head:
            raise ValueError
        metadata["manifest_sha256"] = _sha(root / "MANIFEST.sha256")
        metadata["probe_sha256"] = {
            name: _sha(root / "scripts" / name) for name in PROBE_PINS
        }
        if metadata["probe_sha256"] != PROBE_PINS:
            raise ValueError
        binary = output / "clock-probe"
        c_probe, python_probe = (root / "scripts" / name for name in PROBE_PINS)
        if not _run(
            output,
            records,
            "gcc",
            [
                "/usr/bin/gcc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-O2",
                str(c_probe),
                "-o",
                str(binary),
            ],
            timeout=30,
        ):
            raise ValueError
        metadata["binary_sha256"] = _sha(binary)
        host_ok = _run(
            output,
            records,
            "host",
            [sys.executable, "-B", str(python_probe), str(binary)],
            timeout=15,
        )
        host_value = _json_output(output, records, "host") if host_ok else None
        if not _run(
            output, records, "docker-pull", [*docker, "pull", BASE_IMAGE], timeout=180
        ):
            raise ValueError
        inspect_format = '{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}}}'
        if not _run(
            output,
            records,
            "docker-image",
            [*docker, "image", "inspect", "--format", inspect_format, BASE_IMAGE],
            timeout=15,
        ):
            raise ValueError
        metadata["image"] = _json_output(output, records, "docker-image")
        attempted_container = True
        container_ok = _run(
            output,
            records,
            "container",
            [
                *docker,
                "run",
                "--rm",
                "--name",
                container_name,
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--read-only",
                "--security-opt",
                "no-new-privileges:true",
                "--user",
                "65534:65534",
                "--pids-limit",
                "32",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "--mount",
                f"type=bind,source={c_probe},target=/probe.c,readonly",
                "--mount",
                f"type=bind,source={python_probe},target=/probe.py,readonly",
                "--mount",
                f"type=bind,source={binary},target=/clock-probe,readonly",
                BASE_IMAGE,
                "python",
                "-B",
                "/probe.py",
                "/clock-probe",
            ],
            timeout=30,
        )
        container_value = (
            _json_output(output, records, "container") if container_ok else None
        )
        metadata["measurement_complete"] = bool(
            host_value
            and container_value
            and metadata["image"]
            and _sha(binary) == metadata["binary_sha256"]
        )
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
        metadata["failure_type"] = type(error).__name__
    finally:
        if attempted_container:
            _run(
                output,
                records,
                "container-cleanup",
                [*docker, "rm", "--force", container_name],
                timeout=15,
            )
        metadata["output_sha256"] = {
            path.name: _sha(path) for path in sorted(output.iterdir()) if path.is_file()
        }
        (output / "metadata.json").write_text(
            json.dumps(metadata, sort_keys=True, indent=2) + "\n"
        )
        (output / "outputs.sha256.json").write_text(
            json.dumps(
                {
                    path.name: _sha(path)
                    for path in sorted(output.iterdir())
                    if path.is_file()
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    raise SystemExit(0 if metadata["measurement_complete"] else 1)


if __name__ == "__main__":
    main()

"""Build one real offline maintenance venv in the verified image.

No CTCC project is installed/imported, and no PG/entrypoint/service is run.
This is vendor preparation DATA, never a maintenance or trading capability.
"""

import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT_SHA = "cc4eec913361243481206d76a87d9f9b76296d2ab9df85ea018c4891792f2caf"
PROBE_SHA = "0d48e9abc3ed91eb1913a2a3c6048557737d6cfab37225a261a22e6869bac5ea"
SOURCE = HERE.parent / "n330runtime58"
WHEELS = HERE.parents[1] / "dependency-lock-tzdata/linux-wheels"
stage = "initial_public_inputs"


def need(value):
    if value is not True:
        raise ValueError("offline_vendor_environment_refused") from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def measured_module(path, expected, name):
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size < 40000)
    raw = path.read_bytes()
    need(sha(raw) == expected)
    result = importlib.util.module_from_spec(importlib.util.spec_from_file_location(name, path))
    exec(compile(raw, str(path), "exec"), result.__dict__)
    return result


def write_private(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:])
            need(count > 0)
            offset += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def venv_tree(root):
    result = []
    def walk(directory, prefix):
        for name in sorted(os.listdir(directory)):
            path = directory / name
            relative = prefix + name
            before = path.lstat()
            row = {"path": relative, "mode": stat.S_IMODE(before.st_mode), "uid": before.st_uid, "gid": before.st_gid}
            if stat.S_ISDIR(before.st_mode):
                row["kind"] = "directory"
                result.append(row)
                walk(path, relative + "/")
            elif stat.S_ISLNK(before.st_mode):
                target = os.readlink(path)
                need(not target.startswith("/") and "\\" not in target and "\x00" not in target)
                resolved = (path.parent / target).resolve(strict=True)
                need(resolved.is_relative_to(root))
                row.update(kind="symlink", target=target)
                result.append(row)
            else:
                need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    opened = os.fstat(descriptor)
                    need((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) ==
                         (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns))
                    digest, size = hashlib.sha256(), 0
                    while raw := os.read(descriptor, 1024 * 1024):
                        size += len(raw)
                        digest.update(raw)
                    after = os.fstat(descriptor)
                    need((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) ==
                         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
                    need(size == before.st_size)
                finally:
                    os.close(descriptor)
                row.update(kind="file", bytes=size, sha256=digest.hexdigest())
                result.append(row)
    walk(root, "")
    need(0 < len(result) <= 40000)
    return result


def run():
    global stage
    need(os.geteuid() == 0 and set(os.environ) <= {"PATH", "LC_ALL", "LANG"})
    os.umask(0o077)
    output = HERE / "OFFLINE_MAINTENANCE_VENDOR_ENVIRONMENT_V1.json"
    need(not output.exists())
    probe = measured_module(HERE / "probe_pinned_python_execution_v1.py", PROBE_SHA, "accepted_python_image_probe")
    inputs = json.loads(probe.read_regular(HERE / "OFFLINE_DEPENDENCY_INPUTS_VERIFIED_V1.json", INPUT_SHA, 128 * 1024))
    need(inputs["vendor_only_maintenance_provider"] is True and inputs["project_source_installed"] is False)
    need(inputs["full_validation_baseline"] is False and inputs["execution_authority"] is False)
    rows = inputs["packages"]
    need(type(rows) is list and len(rows) == inputs["vendor_wheel_count"] == 41)
    need(len({row["filename"] for row in rows}) == len(rows))
    need(set(os.listdir(WHEELS)) == {row["filename"] for row in rows})
    stage = "all_offline_wheel_bytes_reverified"
    for row in rows:
        need(re.fullmatch(r"[A-Za-z0-9_.-]+\.whl", row["filename"]) is not None)
        need(type(row["byte_size"]) is int and 0 < row["byte_size"] < 64 * 1024 * 1024)
        raw = probe.read_regular(WHEELS / row["filename"], row["sha256"], row["byte_size"])
        need(len(raw) == row["byte_size"])
    lock = probe.read_regular(SOURCE / "requirements/validation-linux-py312.lock", inputs["lock_sha256"], 128 * 1024)
    image_locator = json.loads(probe.read_regular(probe.LOCATOR, probe.LOCATOR_SHA, 4096))
    image = Path(image_locator["rootfs"])
    helper = measured_module(HERE / "materialize_private_pinned_python_rootfs_v1.py", probe.MATERIALIZER_SHA, "accepted_python_image_materializer")
    inventory = json.loads(probe.read_regular(HERE / "PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json", probe.INVENTORY_SHA, 2 * 1024 * 1024))
    stage = "complete_image_before_venv"
    probe.verify_complete_tree(image, inventory, helper)
    parent_pid = os.getppid()
    parent_namespaces = probe.ns_ids(parent_pid)
    before = probe.ns_ids("self")
    need(probe.libc_function("unshare", [probe.ctypes.c_int])(0x40000000 | 0x00020000 | 0x08000000) == 0)
    namespaces = probe.ns_ids("self")
    need(all(namespaces[name] != before[name] for name in before))
    probe.mount(None, "/", None, 16384 | 262144)
    outer = Path("/root") / ("ctcc-python-vendor41-" + uuid.uuid4().hex)
    outer.mkdir(mode=0o700)
    image_mount, runtime = outer / "rootfs", outer / "runtime"
    image_mount.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    os.chown(runtime, 70, 70)
    for name in ("wheels", "tmp"):
        (runtime / name).mkdir(mode=0o700)
        os.chown(runtime / name, 70, 70)
    probe.mount(image, image_mount, None, 4096)
    probe.mount(None, image_mount, None, 4096 | 32 | 1 | 2 | 4)
    probe.mount(runtime, image_mount / "run", None, 4096)
    probe.mount(WHEELS, image_mount / "run/wheels", None, 4096)
    probe.mount(None, image_mount / "run/wheels", None, 4096 | 32 | 1 | 2 | 4 | 8)
    probe.mount("tmpfs", image_mount / "dev", "tmpfs", 2 | 8, "mode=0755,size=1m")
    for name, major, minor in (("null", 1, 3), ("random", 1, 8), ("urandom", 1, 9)):
        os.mknod(image_mount / "dev" / name, stat.S_IFCHR | 0o666, os.makedev(major, minor))
        os.chmod(image_mount / "dev" / name, 0o666)
    write_private(runtime / "validation-linux-py312.lock", lock)
    os.chown(runtime / "validation-linux-py312.lock", 70, 70)
    child_env = {"PATH": "/run/venv/bin:/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/nonexistent",
                 "TMPDIR": "/run/tmp", "PIP_CONFIG_FILE": "/dev/null", "PIP_NO_INDEX": "1", "PIP_NO_CACHE_DIR": "1",
                 "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONSAFEPATH": "1", "PYTHONNOUSERSITE": "1"}
    executed = []
    # Capture the actual supervisor PID BEFORE entering preexec in a child.
    supervisor_pid = os.getpid()
    def fixed_command(label, argv, timeout):
        need(re.fullmatch(r"[a-z_]+", label) is not None)
        result = subprocess.run(argv, preexec_fn=lambda: probe.child_preexec(image_mount, supervisor_pid),
            env=child_env, close_fds=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        write_private(runtime / (label + ".stdout.private"), result.stdout)
        write_private(runtime / (label + ".stderr.private"), result.stderr)
        executed.append({"label": label, "returncode": result.returncode,
                         "stdout_sha256": sha(result.stdout), "stderr_sha256": sha(result.stderr)})
        need(result.returncode == 0 and len(result.stdout) <= 2 * 1024 * 1024 and len(result.stderr) <= 2 * 1024 * 1024)
        return result.stdout
    stage = "genuine_offline_venv_creation"
    fixed_command("create_venv", ["/usr/local/bin/python3.12", "-I", "-B", "-m", "venv", "--copies", "--without-pip", "/run/venv"], 45)
    stage = "locked_offline_wheel_installation"
    pip_rows = [row for row in rows if row["name"] == "pip"]
    need(len(pip_rows) == 1)
    # Bootstrap the installer from its source-hashed wheel, never a host pip
    # or invented installed metadata. Force an actual fresh installation of
    # that pip too, then verify in a NEW process without the zip sys.path.
    pip_bootstrap = "import sys;sys.path.insert(0,sys.argv[1]);from pip._internal.cli.main import main;raise SystemExit(main(sys.argv[2:]))"
    fixed_command("install_locked_wheels", ["/run/venv/bin/python", "-I", "-B", "-c", pip_bootstrap,
        "/run/wheels/" + pip_rows[0]["filename"], "install", "--ignore-installed", "--no-index",
        "--find-links=/run/wheels", "--no-cache-dir", "--only-binary=:all:", "--require-hashes", "--no-compile",
        "--disable-pip-version-check", "-r", "/run/validation-linux-py312.lock"], 120)
    stage = "pip_dependency_consistency"
    fixed_command("pip_check", ["/run/venv/bin/python", "-I", "-B", "-m", "pip", "check"], 20)
    expected = [[row["distribution_name"], row["version"]] for row in rows]
    program = '''import collections,importlib.metadata as m,json,os,sys,sysconfig
assert sys.version_info[:3]==(3,12,14) and sys.flags.isolated==1 and sys.dont_write_bytecode
assert sys.prefix=="/run/venv" and sys.base_prefix=="/usr/local" and sys.flags.no_site==0
assert os.getuid()==os.getgid()==70 and os.getgroups()==[]
actual=sorted((d.metadata["Name"],d.version) for d in m.distributions())
expected=json.loads(sys.argv[1])
assert collections.Counter(actual)==collections.Counter(tuple(x) for x in expected)
assert not any(n=="ctcc-v2" for n,v in actual)
import sqlalchemy,alembic,asyncpg,pydantic
assert not any(n=="app" or n.startswith("app.") for n in sys.modules)
assert sysconfig.get_config_var("SOABI")=="cpython-312-x86_64-linux-gnu"
print(json.dumps({"python_version":sys.version,"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix,
"isolated":sys.flags.isolated,"no_site":sys.flags.no_site,"uid":os.getuid(),"inventory":actual,
"vendor_files":{n:sys.modules[n].__file__ for n in ("sqlalchemy","alembic","asyncpg","pydantic")},
"project_installed_or_imported":False,"full_validation_baseline":False},sort_keys=True))
'''
    stage = "actual_venv_vendor_import_and_inventory"
    metadata_raw = fixed_command("vendor_inventory", ["/run/venv/bin/python", "-I", "-B", "-c", program,
                                  json.dumps(expected, separators=(",", ":"))], 25)
    metadata = json.loads(metadata_raw)
    need(metadata["prefix"] == "/run/venv" and len(metadata["inventory"]) == 41
         and metadata["project_installed_or_imported"] is False)
    stage = "independent_full_venv_tree_readbacks"
    actual = venv_tree(runtime / "venv")
    tree_raw = canonical(actual)
    write_private(outer / "VENDOR_ENV_TREE_PRIVATE.json", tree_raw)
    need(venv_tree(runtime / "venv") == actual)
    probe.mount(runtime / "venv", runtime / "venv", None, 4096)
    probe.mount(None, runtime / "venv", None, 4096 | 32 | 1 | 2 | 4)
    need(venv_tree(runtime / "venv") == actual)
    stage = "base_image_reverification_after_installation"
    probe.verify_complete_tree(image, inventory, helper)
    need(probe.ns_ids(parent_pid) == parent_namespaces)
    locator = {"schema": "ctcc.private_python.maintenance_vendor_locator.v1", "outer": str(outer),
        "venv": str(runtime / "venv"), "tree_manifest": str(outer / "VENDOR_ENV_TREE_PRIVATE.json"),
        "tree_sha256": sha(tree_raw), "python_image_tree_sha256": probe.EXPECTED_TREE,
        "source_dependency_input_sha256": INPUT_SHA, "lock_sha256": inputs["lock_sha256"],
        "logical_executable": "/run/venv/bin/python", "logical_venv": "/run/venv",
        "vendor_count": 41, "project_installed": False, "consumer_must_verify_tree_and_remount_readonly": True,
        "saved_locator_is_maintenance_authority": False}
    locator_raw = canonical(locator)
    write_private(outer / "VENDOR_ENV_LOCATOR_PRIVATE.json", locator_raw)
    # Fixed private discovery location, exclusive first publication. A
    # consumer must verify actual bytes/tree and re-establish its own live
    # supervisor; neither this file nor its hash grants authority.
    write_private(probe.LOCATOR.parent / "MAINTENANCE_VENDOR_LOCATOR_PRIVATE.json", locator_raw)
    report = {"schema": "ctcc.pinned_python.maintenance_vendor_preparation.v1", "status": "VENDOR_ONLY_ENVIRONMENT_VERIFIED",
        "controller_sha256": sha(Path(__file__).read_bytes()), "input_sha256": INPUT_SHA,
        "base_image_tree_sha256": probe.EXPECTED_TREE, "venv_tree_sha256": sha(tree_raw),
        "private_locator_sha256": sha(locator_raw), "venv_entries": len(actual),
        "private_provider_locator_filename": "MAINTENANCE_VENDOR_LOCATOR_PRIVATE.json",
        "actual_vendor_count": 41, "actual_vendor_metadata": metadata,
        "commands": executed, "namespace_ids": namespaces, "parent_namespaces_unchanged": True,
        "pip_offline_require_hashes": True, "base_image_before_after_verified": True,
        "venv_readonly_in_preparation_namespace": True, "consumer_must_reapply_readonly_mount": True,
        "project_installed_or_imported": False, "accepted_full_validation_baseline": False,
        "pg_private_inputs_read": 0, "sql_connections": 0, "server_or_original_service_starts": 0,
        "maintenance_authority": False, "execution_authority": False}
    raw = canonical(report)
    with output.open("xb") as stream:
        need(stream.write(raw) == len(raw))
    print(json.dumps({"status": report["status"], "receipt_sha256": sha(raw),
                      "private_locator_sha256": sha(locator_raw), "venv_tree_sha256": sha(tree_raw)}))


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        report = {"schema": "ctcc.pinned_python.maintenance_vendor_preparation_failure.v1", "status": "FAIL",
                  "stage": stage, "safe_error_type": type(error).__name__, "partial_files_preserved": True,
                  "maintenance_authority": False, "execution_authority": False}
        with (HERE / ("OFFLINE_VENDOR_PREPARATION_FAILURE_" + uuid.uuid4().hex + ".json")).open("xb") as stream:
            stream.write(canonical(report))
        print(json.dumps(report))
        raise SystemExit(1) from None

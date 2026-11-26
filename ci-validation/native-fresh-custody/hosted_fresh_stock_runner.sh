#!/usr/bin/env bash
# Disposable GitHub-hosted proof of the stock fresh DB0017 -> stopped DB0055 path.
set -eEuo pipefail
umask 077

readonly schema=ctcc.validation.hosted_fresh_custody.v1
readonly pg_bundle=N397-validation-wsl-pg-authenticity-20261121
readonly py_bundle=N434-pinned-linux-python-runtime-20261121
readonly fresh_bundle=N470-native-maintenance-fresh-acceptance-20261126

inside() {
    stage=namespace_preflight
    reported=0
    on_exit() {
        local code=$?
        if (( code != 0 && reported == 0 )); then
            local outcome=REFUSED
            case $stage in
                canonical_fresh_supervisor|verify_canonical_readback)
                    outcome=HOST_RUN_OUTCOME_UNKNOWN_DO_NOT_RETRY
                    ;;
            esac
            printf '{"schema":"%s","status":"%s","stage":"%s","execution_authority":false}\n' \
                "$schema" "$outcome" "$stage"
        fi
    }
    trap on_exit EXIT
    [[ $# == 10 && $EUID == 0 && $$ == 1 ]]
    local subject=$1 digest=$2 commit=$3 tree=$4 manifest=$5 support=$6
    local ns previous
    shift 6
    for ns in net mnt ipc pid; do
        previous=$1
        shift
        [[ $(readlink "/proc/self/ns/$ns") != "$previous" ]]
    done
    /usr/bin/python3 -I -S -B -c \
        'import socket,sys; sys.exit(socket.if_nameindex() != [(1, "lo")])'
    # This mount hides the entire original home and has no persistent backing.
    # The new PID namespace's init exit kills any remaining descendants.
    mount -t tmpfs -o size=3G,mode=0700,nosuid,nodev tmpfs /root
    [[ $(stat -f -c %T /root) == tmpfs && $(stat -c '%u:%g:%a' /root) == 0:0:700 ]]
    mkdir -m 0700 /root/work
    cd /root/work

    stage=verify_public_inputs
    /usr/bin/python3 -I -S -B - "$subject" "$digest" "$commit" "$manifest" "$support" "$pg_bundle" "$py_bundle" "$fresh_bundle" <<'PY'
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile

subject, archive_hash, commit, manifest_hash, support, pg, py, fresh = sys.argv[1:]
support = Path(support)
output = Path('/root/work')
sha256 = re.compile('[a-f0-9]{64}\\Z')

def need(condition):
    if not condition:
        raise ValueError('hosted_fresh_input_refused')

def open_input(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(fd, 'rb')
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        stream.close()
        raise ValueError('hosted_fresh_input_type_refused')
    return stream

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def measured(path, expected, limit):
    need(sha256.fullmatch(expected) is not None)
    with open_input(path) as stream:
        raw = stream.read(limit + 1)
    need(0 < len(raw) <= limit and digest(raw) == expected)
    return raw

def write(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)

def stage_fixed(relative, expected, limit=2**22):
    source = support / relative
    raw = measured(source, expected, limit)
    write(output / relative, raw)
    return raw

def stage_image(name, tool, tool_hash, receipt_hash, inventory_hash):
    relative = Path('validation-results') / name
    stage_fixed(relative / tool, tool_hash)
    stage_fixed(relative / 'PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json', inventory_hash)
    receipt = json.loads(stage_fixed(relative / 'PINNED_OCI_ALL_DIFFIDS_VERIFIED.json', receipt_hash))
    need(receipt['virtual_rootfs_inventory_sha256'] == inventory_hash)
    assets = output / relative / 'public-assets'
    assets.mkdir(mode=0o700)
    for index, layer in enumerate(receipt['layers']):
        compressed = layer['compressed_digest'].removeprefix('sha256:')
        expected = layer['diff_id'].removeprefix('sha256:')
        need(sha256.fullmatch(compressed) is not None and sha256.fullmatch(expected) is not None)
        blob = 'sha256-' + compressed + '.blob'
        raw = measured(support / relative / 'public-assets' / blob, compressed, 128 * 1024 * 1024)
        write(assets / blob, raw)
        need(layer['layer'] == index and layer['tar_file'] == f'layer-{index}.tar')
        length = layer['uncompressed_bytes']
        need(type(length) is int and 0 < length <= 512 * 1024 * 1024)
        h = hashlib.sha256()
        consumed = 0
        with gzip.open(assets / blob, 'rb') as data, (assets / layer['tar_file']).open('xb') as stream:
            while block := data.read(1024 * 1024):
                consumed += len(block)
                need(consumed <= length)
                h.update(block)
                stream.write(block)
        need(consumed == length and h.hexdigest() == expected)

pg_inventory = '603f68e5f1059ea01a25ecc0a79a0f7f041b4640a24f5f16fcc3b3b40524f382'
py_inventory = '9b79bfd725a69bbf9ae2d386c3219f5110ffbc2efa5e4b7435552043c87c0a6a'
stage_image(pg, 'materialize_ci_rootfs.py', '4cea19f3e77b972cb0e788707722b0438339b59c25bbcfda1410b4ce9e649ad6',
            'a07a704812da6def0876d42771477cc826340ea5e81ef3e8f08e75284b6a7a6e', pg_inventory)
stage_image(py, 'materialize_private_pinned_python_rootfs_v1.py', 'da5f4d976f4a961bce6b7adc8c108b0e5f66f8ec48487770494fd50d018d77ba',
            '1aeb49a7ec1749a7d403fa5cbfebe0b44eefe550da34d7842afef63c78722074', py_inventory)
py_relative = Path('validation-results') / py
stage_fixed(py_relative / 'probe_pinned_python_execution_v1.py', '0d48e9abc3ed91eb1913a2a3c6048557737d6cfab37225a261a22e6869bac5ea')
stage_fixed(py_relative / 'prepare_offline_vendor_environment_v1.py', '9cdc89eca799834987c17a7ab52badb3def4aa168c99bdc65b10e57f02e40142')
inputs = json.loads(stage_fixed(py_relative / 'OFFLINE_DEPENDENCY_INPUTS_VERIFIED_V1.json',
                               'cc4eec913361243481206d76a87d9f9b76296d2ab9df85ea018c4891792f2caf'))
stage_fixed(Path('validation-results') / fresh / 'run_fresh_custody_once_linux_v1.py',
            '3876042ea90094fcf7240423b79745226cf65843a357c4870dffc9f68bf16ff7')

archive = measured(subject, archive_hash, 128 * 1024 * 1024)
write(output / 'subject.tar', archive)
with tarfile.open(output / 'subject.tar', 'r:') as tar:
    need(tar.pax_headers.get('comment') == commit)
    for name, expected in {
        'MANIFEST.sha256': manifest_hash,
        'pyproject.toml': inputs['pyproject_sha256'],
        'requirements/validation-linux-py312.json': inputs['metadata_sha256'],
        'requirements/validation-linux-py312.lock': inputs['lock_sha256'],
    }.items():
        matches = [member for member in tar.getmembers() if member.name == name]
        need(len(matches) == 1 and matches[0].isfile() and 0 < matches[0].size <= 4 * 1024 * 1024)
        with tar.extractfile(matches[0]) as stream:
            raw = stream.read()
        need(digest(raw) == expected)
        if name.endswith('.lock'):
            write(output / 'validation-results/n330runtime58' / name, raw)

wheels = Path('dependency-lock-tzdata/linux-wheels')
rows = inputs['packages']
need(len(rows) == len({row['filename'] for row in rows}) == inputs['vendor_wheel_count'] == 41)
for row in rows:
    filename, length = row['filename'], row['byte_size']
    need(re.fullmatch(r'[A-Za-z0-9_.-]+\.whl', filename) is not None)
    need(type(length) is int and 0 < length < 64 * 1024 * 1024)
    raw = stage_fixed(wheels / filename, row['sha256'], length)
    need(len(raw) == length)
    target = output / wheels / filename
    os.chown(target, 0, 70, follow_symlinks=False)
    target.chmod(0o440)
    info = target.stat()
    need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 70, 0o440))
wheel_directory = output / wheels
os.chown(wheel_directory, 0, 70, follow_symlinks=False)
wheel_directory.chmod(0o550)
info = wheel_directory.stat()
need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 70, 0o550))
PY

    stage=postgres_image_materialization
    local pg="validation-results/$pg_bundle" py="validation-results/$py_bundle"
    /usr/bin/python3 -I -S -B "$pg/materialize_ci_rootfs.py" > pg.private.log 2>&1
    stage=python_image_materialization
    /usr/bin/python3 -I -S -B "$py/materialize_private_pinned_python_rootfs_v1.py" > python.private.log 2>&1
    stage=offline_vendor_materialization
    if ! /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C \
        /usr/bin/python3 -I -S -B "$py/prepare_offline_vendor_environment_v1.py" \
        > vendor.private.stdout 2> vendor.private.stderr; then
        local vendor_stage
        vendor_stage=$(/usr/bin/python3 -I -S -B - "$py" <<'PY'
import json
from pathlib import Path
import re
import sys

stages = {
    "initial_public_inputs": "vendor_initial_checks",
    "all_offline_wheel_bytes_reverified": "vendor_wheel_readback",
    "complete_image_before_venv": "vendor_base_image_before_venv",
    "genuine_offline_venv_creation": "vendor_venv_creation",
    "locked_offline_wheel_installation": "vendor_wheel_installation",
    "pip_dependency_consistency": "vendor_dependency_consistency",
    "actual_venv_vendor_import_and_inventory": "vendor_import_inventory",
    "independent_full_venv_tree_readbacks": "vendor_tree_readback",
    "base_image_reverification_after_installation": "vendor_base_image_after_installation",
}

def bounded(path):
    with path.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError
    return raw

try:
    raw = bounded(Path("vendor.private.stdout"))
    private_records = list(Path(sys.argv[1]).glob("OFFLINE_VENDOR_PREPARATION_FAILURE_*.json"))
    if (
        Path("vendor.private.stderr").stat().st_size == 0
        and raw.count(b"\n") == 1
        and raw.endswith(b"\n")
        and len(private_records) == 1
        and re.fullmatch(r"OFFLINE_VENDOR_PREPARATION_FAILURE_[a-f0-9]{32}\.json", private_records[0].name)
    ):
        report = json.loads(raw)
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        if (
            type(report) is dict
            and report.get("schema") == "ctcc.pinned_python.maintenance_vendor_preparation_failure.v1"
            and report.get("status") == "FAIL"
            and report.get("partial_files_preserved") is True
            and report.get("maintenance_authority") is False
            and report.get("execution_authority") is False
            and bounded(private_records[0]) == canonical
        ):
            print(stages.get(report.get("stage"), ""))
except (OSError, ValueError, TypeError):
    pass
PY
)
        if [[ -n $vendor_stage ]]; then
            stage=$vendor_stage
        fi
        return 1
    fi

    stage=synthetic_tls_preflight
    {
        openssl req -new -x509 -newkey rsa:2048 -nodes -sha256 -days 2 -batch -config /dev/null \
            -subj /CN=ctcc-fresh-ci-ca -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
            -addext 'keyUsage=critical,keyCertSign,cRLSign' -keyout ca.key -out ca.pem
        openssl req -new -newkey rsa:2048 -nodes -sha256 -batch -config /dev/null \
            -subj /CN=ctcc-fresh-ci-server -keyout server.key -out server.csr
        printf '%s\n' 'basicConstraints=critical,CA:FALSE' 'extendedKeyUsage=serverAuth' \
            'subjectAltName=IP:127.0.0.1' > tls.ext
        openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -set_serial 2 \
            -sha256 -days 1 -extfile tls.ext -out server.pem
        openssl verify -purpose sslserver -verify_ip 127.0.0.1 -CAfile ca.pem server.pem
    } > tls.private.log 2>&1

    stage=synthetic_request_staging
    local status=0
    /usr/bin/python3 -I -S -B - "$commit" "$tree" "$digest" "$manifest" "$pg" "$py" > request.private.json <<'PY'
import base64
import json
from pathlib import Path
import sys

commit, tree, archive, manifest, pg, python = sys.argv[1:]
here = Path.cwd()
names = {'ca_certificate': 'ca.pem', 'server_certificate': 'server.pem', 'server_private_key': 'server.key'}
print(json.dumps({
    'schema': 'ctcc.fresh_native.external_resource_staging.v1',
    'source': dict(commit=commit, tree=tree, archive=archive, manifest=manifest),
    'source_archive_file': str(here / 'subject.tar'),
    'pg_inventory_file': str(here / pg / 'PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json'),
    'python_inventory_file': str(here / python / 'PINNED_OCI_VIRTUAL_ROOTFS_INVENTORY.json'),
    'tls_files_b64': {key: base64.b64encode((here / path).read_bytes()).decode('ascii') for key, path in names.items()},
}, separators=(',', ':')))
PY
    stage=canonical_fresh_supervisor
    /usr/bin/python3 -I -S -B "validation-results/$fresh_bundle/run_fresh_custody_once_linux_v1.py" \
        < request.private.json > canonical.private.json 2> canonical.private.stderr || status=$?

    stage=verify_canonical_readback
    /usr/bin/python3 -I -S -B - "$status" "$commit" "$tree" "$digest" "$manifest" "$pg" "$py" "$schema" 2> report.private.stderr <<'PY'
import hashlib
import json
from pathlib import Path
import re
import sys

status, commit, tree, archive, manifest, pg, python, schema = sys.argv[1:]
source = dict(commit=commit, tree=tree, archive=archive, manifest=manifest)

def need(condition):
    if not condition:
        raise ValueError('hosted_fresh_result_refused')

def read(path, maximum=65536):
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    need(len(raw) <= maximum)
    return raw

def emit(status, stage, **data):
    print(json.dumps(dict(schema=schema, status=status, stage=stage, source=source,
                          execution_authority=False, **data), sort_keys=True, separators=(',', ':')))

native_raw = read('canonical.private.json')
native = json.loads(native_raw)
need(read('canonical.private.stderr') == b'')
if status != '0':
    allowed = {'COMMITTED_READBACK_REQUIRED', 'COMMIT_UNKNOWN_DO_NOT_RETRY', 'FRESH_NATIVE_ACCEPTANCE_REFUSED'}
    need(status == '2' and native['status'] in allowed and native['execution_authority'] is False)
    emit(native['status'], 'canonical_fresh_supervisor')
    raise SystemExit(0)

expected = dict(schema='ctcc.retained_pg.fresh_acceptance_result.v1',
                status='FRESH_CANONICAL17_COLD_TO_STOPPED0055_VERIFIED',
                origin='fresh_canonical17_cold_fixture', database_head='0055',
                row_encoding='ctcc.owner.retained_row_sql_null_bitmap.v2', exchange_flat=None,
                independent_readbacks=2, commit_attempts=1, preserved_pg_inputs_read=0, control_revision=0)
expected.update(dict.fromkeys(('migration_performed', 'source17_bootstrap_catalog_verified',
                              'rehearsal_rolled_back_verified', 'cold_source_unchanged_verified',
                              'distinct_working_copy_verified', 'owned_server_fast_shutdown_verified',
                              'emergency_stop', 'restart_latch_required'), True))
expected.update(dict.fromkeys(('armed', 'preserved_backup_lineage_verified', 'schema_upgrade_authorized',
                              'account_complete', 'execution_authority'), False))
sha_keys = {'cold_manifest_sha256', 'origin_sha256', 'system_identifier_sha256', 'before_sha256', 'after_sha256'}
need(set(native) == set(expected) | sha_keys)
need(all(type(native[key]) is type(value) and native[key] == value for key, value in expected.items()))
need(all(type(native[key]) is str and re.fullmatch('[a-f0-9]{64}', native[key]) for key in sha_keys))
operation_dirs = sorted(Path('/root').glob('ctcc-fresh-maintenance-*'))
need(len(operation_dirs) == 1)
supervisors = sorted((operation_dirs[0] / 'operations').glob('ctcc-preserved-supervisor-*'))
need(len(supervisors) == 1)
direct = read(supervisors[0] / 'supervisor.stdout.private')
need(json.loads(direct) == native and read(supervisors[0] / 'supervisor.stderr.private') == b'')
need(read(operation_dirs[0] / 'accepted-result.private.json') == native_raw)
pg_tree = json.loads(read(Path(pg) / 'PRIVATE_CI_ROOTFS_MATERIALIZED_VERIFIED.json'))['physical_tree_sha256']
py_tree = json.loads(read(Path(python) / 'PRIVATE_CI_ROOTFS_MATERIALIZED_VERIFIED.json'))['physical_tree_sha256']
vendor = json.loads(read(Path(python) / 'OFFLINE_MAINTENANCE_VENDOR_ENVIRONMENT_V1.json'))
need(pg_tree == 'a4413022a5e18d5c8b4e3221be80bd7d21aa13c85093ae1adb7e01423da78556')
need(py_tree == 'a601e5ccc5a8a43752448bf394ee481e18b8048b085300dc448b5192186439f4')
need(vendor['venv_tree_sha256'] == 'f2864cee254e795416117789eda90fd97d6d3c9492fde7090737d08f34a3ae28')
need(vendor['actual_vendor_count'] == 41 and vendor['project_installed_or_imported'] is False)
emit('PASS_FRESH_DISPOSABLE_0017_TO_0055', 'canonical_quiescent_readback', canonical_result=native,
     canonical_stdout_sha256=hashlib.sha256(direct).hexdigest(), postgres_tree_sha256=pg_tree,
     python_tree_sha256=py_tree, vendor_tree_sha256=vendor['venv_tree_sha256'],
     vendor_count=41, fresh_request_rejected_by_preserved_entry=True)
PY
    reported=1
    return "$status"
}

if [[ ${1-} == _isolated_inner ]]; then
    shift
    inside "$@"
    exit
fi

[[ $# == 0 && $EUID != 0 && ${GITHUB_ACTIONS-} == true && ${RUNNER_ENVIRONMENT-} == github-hosted ]]
[[ $(uname -sm) == 'Linux x86_64' ]]
: "${CTCC_SUBJECT_TAR:?}" "${CTCC_SUBJECT_TAR_SHA256:?}" "${CTCC_SUBJECT_COMMIT:?}" \
    "${CTCC_SUBJECT_TREE:?}" "${CTCC_SUBJECT_MANIFEST:?}" "${CTCC_SUPPORT_ROOT:?}" "${CTCC_RESULT_DIR:?}"
[[ $CTCC_SUBJECT_TAR == /* && $CTCC_SUPPORT_ROOT == /* && $CTCC_RESULT_DIR == /* ]]
[[ $CTCC_SUBJECT_TAR_SHA256 =~ ^[0-9a-f]{64}$ && $CTCC_SUBJECT_MANIFEST =~ ^[0-9a-f]{64}$ ]]
[[ $CTCC_SUBJECT_COMMIT =~ ^[0-9a-f]{40}$ && $CTCC_SUBJECT_TREE =~ ^[0-9a-f]{40}$ ]]
[[ -d $CTCC_SUPPORT_ROOT && -f $CTCC_SUBJECT_TAR && ! -L $CTCC_SUBJECT_TAR ]]
mkdir -m 0700 -- "$CTCC_RESULT_DIR"
runner=$(readlink -f -- "$0")
namespaces=()
for ns in net mnt ipc pid; do
    namespaces+=("$(readlink "/proc/self/ns/$ns")")
done

status=0
report=$(sudo -n /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C \
    /usr/bin/unshare --net --mount --ipc --pid --fork --kill-child=SIGKILL --mount-proc --propagation private \
    /usr/bin/bash "$runner" _isolated_inner "$CTCC_SUBJECT_TAR" "$CTCC_SUBJECT_TAR_SHA256" \
    "$CTCC_SUBJECT_COMMIT" "$CTCC_SUBJECT_TREE" "$CTCC_SUBJECT_MANIFEST" "$CTCC_SUPPORT_ROOT" \
    "${namespaces[@]}") || status=$?
if [[ -z $report ]]; then
    report=$(printf '{"schema":"%s","status":"HOST_RUN_OUTCOME_UNKNOWN_DO_NOT_RETRY","stage":"isolated_runner","execution_authority":false}' "$schema")
    status=1
fi

# The root init has exited, which destroys its PID and mount namespaces. Only
# the fixed public report is written after that point, never raw runtime files.
[[ ${#report} -le 16384 && $report != *$'\n'* ]]
/usr/bin/python3 -I -S -B - "$CTCC_RESULT_DIR" "$report" "$status" "$schema" \
    "$CTCC_SUBJECT_COMMIT" "$CTCC_SUBJECT_TREE" "$CTCC_SUBJECT_TAR_SHA256" "$CTCC_SUBJECT_MANIFEST" <<'PY'
import json
import os
from pathlib import Path
import sys

result_dir, encoded, status, schema, commit, tree, archive, manifest = sys.argv[1:]
report = json.loads(encoded)
source = dict(commit=commit, tree=tree, archive=archive, manifest=manifest)
accepted = report.get('status') == 'PASS_FRESH_DISPOSABLE_0017_TO_0055'
if (report.get('schema') != schema or report.get('execution_authority') is not False
        or report.get('source', source) != source or accepted != (status == '0')):
    raise ValueError('hosted_fresh_report_publication_refused')
report['source'] = source
after = (json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
target = Path(result_dir) / 'fresh-custody-hosted-result.json'
with target.open('xb') as stream:
    stream.write(after)
    stream.flush()
    os.fsync(stream.fileno())
if target.read_bytes() != after:
    raise ValueError('hosted_fresh_report_readback_refused')
PY
exit "$status"

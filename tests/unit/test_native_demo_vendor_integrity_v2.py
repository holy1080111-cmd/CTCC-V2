"""Public runtime/source mechanisms only; no socket, client, owner, or order IO."""

import builtins
from dataclasses import replace
from types import FunctionType

import pytest

from app.exchange.okx import native_demo_vendor_integrity_v2 as subject


def _socket_send_owner():
    return subject._MODULES[3][1].SocketStream


def test_pristine_twenty_five_bindings_and_six_files_remain_inert():
    result = subject.admit_native_demo_vendors_v2()
    subject.require_native_demo_vendors_v2(result)
    assert len(result.bindings) == 26
    assert len(result.file_pins) == 6
    assert result.execution_authority is False
    assert result.order_retry_authority is False
    assert result.bindings[14][0].__name__ == "_ProactorBaseWritePipeTransport"
    assert result.bindings[16][0].__name__ == "_ProactorBasePipeTransport"
    assert result.bindings[18][0].__name__ == "_SelectorTransport"


def _inert_cleanup_target(admitted, *, proactor=True):
    index = 0 if proactor else 1
    pin = next(
        pin
        for pin in admitted._functions
        if pin.module_index == index and pin.attribute == "abort"
    )
    native = object.__new__(pin.lookup)
    # Prevent the native class finalizer from interpreting this inert mechanism
    # fixture as a real socket. No native transport constructor or IO is run.
    native._sock = None
    return native


@pytest.mark.parametrize("index", range(20, 25))
def test_changed_actual_cleanup_callee_is_not_invoked(monkeypatch, index):
    admitted = subject.admit_native_demo_vendors_v2()
    held = {"phase": "attempted"}
    cleanup = subject.capture_native_demo_cleanup_v2(admitted, held)
    native = _inert_cleanup_target(admitted)
    owner, attribute, _original = admitted.bindings[index]
    called = []

    def foreign(*_args):
        called.append(True)

    monkeypatch.setattr(owner, attribute, foreign)
    assert cleanup(native) is False
    assert called == [] and held["phase"] == "unknown"
    assert held["cleanup_unknown_sockets"] == [native]


def test_in_place_force_close_code_change_retains_inert_target(monkeypatch):
    admitted = subject.admit_native_demo_vendors_v2()
    held = {"phase": "attempted"}
    cleanup = subject.capture_native_demo_cleanup_v2(admitted, held)
    native = _inert_cleanup_target(admitted)
    function = admitted._functions[20].function
    monkeypatch.setattr(
        function, "__code__", function.__code__.replace(co_name="changed_cleanup")
    )
    assert cleanup(native) is False
    assert held["cleanup_unknown_sockets"] == [native]


@pytest.mark.parametrize("name", ("abort", "_force_close", "_call_connection_lost"))
def test_cleanup_instance_shadow_is_not_invoked(monkeypatch, name):
    admitted = subject.admit_native_demo_vendors_v2()
    held = {"phase": "attempted"}
    cleanup = subject.capture_native_demo_cleanup_v2(admitted, held)
    native = _inert_cleanup_target(admitted)
    called = []
    monkeypatch.setattr(native, name, lambda *_args: called.append(True))
    assert cleanup(native) is False and called == []
    assert held["cleanup_unknown_sockets"] == [native]


def test_fallback_does_not_invoke_mutated_global_need(monkeypatch):
    admitted = subject.admit_native_demo_vendors_v2()
    held = {"phase": "attempted"}
    cleanup = subject.capture_native_demo_cleanup_v2(admitted, held)
    native = _inert_cleanup_target(admitted)
    called = []
    owner, attribute, _original = admitted.bindings[20]
    monkeypatch.setattr(owner, attribute, lambda *_args: called.append("callee"))
    monkeypatch.setattr(subject, "_need", lambda *_args: called.append("need"))
    assert cleanup(native) is False and called == []
    assert held["cleanup_unknown_sockets"] == [native]


def test_scheduled_lost_callback_rechecks_before_changed_callee(monkeypatch):
    admitted = subject.admit_native_demo_vendors_v2()
    held = {"phase": "attempted"}
    cleanup = subject.capture_native_demo_cleanup_v2(admitted, held)
    native = _inert_cleanup_target(admitted)
    scheduled = []

    class InertLoop:
        def call_soon(self, function, *args):
            scheduled.append((function, args))

    native._loop = InertLoop()
    native._empty_waiter = native._write_fut = native._read_fut = None
    native._closing = native._called_connection_lost = False
    native._conn_lost = native._pending_write = 0
    native._buffer = None
    assert cleanup(native) is True and len(scheduled) == 1
    owner, attribute, _original = admitted.bindings[21]
    called = []
    monkeypatch.setattr(owner, attribute, lambda *_args: called.append(True))
    callback, args = scheduled[0]
    assert callback(*args) is False
    assert called == [] and held["cleanup_unknown_sockets"] == [native]


@pytest.mark.parametrize("index", range(26))
def test_every_declared_binding_replacement_denied_before_callback(monkeypatch, index):
    admitted = subject.admit_native_demo_vendors_v2()
    owner, attribute, _original = admitted.bindings[index]
    called = []

    def foreign(*_args, **_kwargs):
        called.append(True)

    monkeypatch.setattr(owner, attribute, foreign)
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.require_native_demo_vendors_v2(admitted)
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.admit_native_demo_vendors_v2()
    assert called == []


def test_preexisting_exact_code_clone_in_foreign_globals_is_not_adopted(monkeypatch):
    owner = _socket_send_owner()
    original = vars(owner)["send"]
    foreign = FunctionType(
        original.__code__,
        {"__builtins__": builtins.__dict__},
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    foreign.__module__ = original.__module__
    foreign.__qualname__ = original.__qualname__
    monkeypatch.setattr(owner, "send", foreign)
    with pytest.raises(
        subject.NativeDemoVendorIntegrityError,
        match="native_demo_vendor_namespace_unbound",
    ):
        subject.admit_native_demo_vendors_v2()


def test_candidate_module_string_cannot_redirect_expected_source(monkeypatch):
    owner = _socket_send_owner()

    def foreign(*_args, **_kwargs):
        raise AssertionError("foreign callback must never execute")

    foreign.__module__ = "anyio._backends._asyncio"
    foreign.__qualname__ = "SocketStream.send"
    monkeypatch.setattr(owner, "send", foreign)
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.admit_native_demo_vendors_v2()


def test_descriptor_getter_never_executes_during_admission_or_recheck(monkeypatch):
    result = subject.admit_native_demo_vendors_v2()
    called = []

    class ForeignDescriptor:
        def __get__(self, _instance, _owner):
            called.append(True)
            raise AssertionError("descriptor callback must never execute")

    monkeypatch.setattr(_socket_send_owner(), "send", ForeignDescriptor())
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.admit_native_demo_vendors_v2()
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.require_native_demo_vendors_v2(result)
    assert called == []


def test_late_inherited_lookup_override_denied_without_invocation(monkeypatch):
    result = subject.admit_native_demo_vendors_v2()
    requested = subject._MODULES[0][1]._ProactorSocketTransport
    called = []

    def foreign(*_args, **_kwargs):
        called.append(True)

    monkeypatch.setattr(requested, "write", foreign)
    with pytest.raises(
        subject.NativeDemoVendorIntegrityError,
        match="native_demo_vendor_declaring_owner_changed",
    ):
        subject.require_native_demo_vendors_v2(result)
    assert called == []


def test_late_code_object_mutation_denied_before_vendor_callback(monkeypatch):
    result = subject.admit_native_demo_vendors_v2()
    function = vars(_socket_send_owner())["send"]

    async def foreign(_self, _item):
        raise AssertionError("foreign callback must never execute")

    monkeypatch.setattr(function, "__code__", foreign.__code__)
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.require_native_demo_vendors_v2(result)


def test_preexisting_default_change_is_not_a_pristine_signature(monkeypatch):
    function = vars(subject._MODULES[4][1].TLSStream)["receive"]
    monkeypatch.setattr(function, "__defaults__", (1,))
    with pytest.raises(
        subject.NativeDemoVendorIntegrityError,
        match="native_demo_vendor_defaults_changed",
    ):
        subject.admit_native_demo_vendors_v2()


def test_frozen_record_metadata_does_not_grant_permission():
    result = subject.admit_native_demo_vendors_v2()
    mutant = replace(result, _functions=())
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.require_native_demo_vendors_v2(mutant)
    assert result.execution_authority is False


@pytest.mark.parametrize("name", ("_SPECS", "_MODULES", "_FILES"))
def test_rebinding_global_metadata_cannot_vacate_checks(monkeypatch, name):
    result = subject.admit_native_demo_vendors_v2()
    monkeypatch.setattr(subject, name, ())
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.admit_native_demo_vendors_v2()
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.require_native_demo_vendors_v2(result)


def test_source_copy_truncation_and_same_size_mutation_fail(tmp_path):
    # A temporary COPY of public runtime bytes; accepted source is never edited.
    admitted = subject.admit_native_demo_vendors_v2()
    source, digest = admitted.file_pins[4]
    size = subject._FILES[4][2]
    raw = source.read_bytes()
    copied = tmp_path / "tls-copy.py"
    copied.write_bytes(raw)
    assert subject._read_fixed_source((copied, digest, size)) == raw
    copied.write_bytes(raw[:-1])
    with pytest.raises(
        subject.NativeDemoVendorIntegrityError,
        match="native_demo_vendor_source_changed",
    ):
        subject._read_fixed_source((copied, digest, size))
    copied.write_bytes(bytes((raw[0] ^ 1,)) + raw[1:])
    with pytest.raises(
        subject.NativeDemoVendorIntegrityError,
        match="native_demo_vendor_source_changed",
    ):
        subject._read_fixed_source((copied, digest, size))


def test_descriptor_normalisation_does_not_call_wrappers():
    def pure():
        raise AssertionError("normalisation must not invoke functions")

    static = staticmethod(pure)
    klass = classmethod(pure)
    assert subject._normalise_descriptor(static) == (static, pure, "staticmethod")
    assert subject._normalise_descriptor(klass) == (klass, pure, "classmethod")
    assert subject._normalise_descriptor(str.join) == (str.join, str.join, "builtin")
    assert subject._normalise_descriptor(object.__str__) == (
        object.__str__,
        object.__str__,
        "builtin",
    )


def test_malformed_inert_record_cannot_invoke_equality_or_iteration_callbacks():
    result = subject.admit_native_demo_vendors_v2()
    called = []

    class ForeignValue:
        def __eq__(self, _other):
            called.append("eq")
            raise AssertionError("equality callback must never execute")

        def __iter__(self):
            called.append("iter")
            raise AssertionError("iteration callback must never execute")

    mutations = (
        replace(result, file_pins=ForeignValue()),
        replace(result, file_pins=(ForeignValue(), *result.file_pins[1:])),
        replace(result, _module_pins=(ForeignValue(), *result._module_pins[1:])),
        replace(
            result,
            bindings=(
                (result.bindings[0][0], ForeignValue(), result.bindings[0][2]),
                *result.bindings[1:],
            ),
        ),
        replace(
            result,
            _functions=(
                replace(result._functions[0], module_index=ForeignValue()),
                *result._functions[1:],
            ),
        ),
    )
    for mutant in mutations:
        with pytest.raises(subject.NativeDemoVendorIntegrityError):
            subject.require_native_demo_vendors_v2(mutant)
    assert called == []


@pytest.mark.parametrize("attribute", ("__file__", "__name__"))
def test_module_metadata_type_rejected_before_foreign_string_or_path_callbacks(
    monkeypatch, attribute
):
    admitted = subject.admit_native_demo_vendors_v2()
    called = []

    class ForeignMetadata:
        def __fspath__(self):
            called.append("path")
            raise AssertionError("path callback must never execute")

        def __str__(self):
            called.append("str")
            raise AssertionError("string callback must never execute")

        def __eq__(self, _other):
            called.append("eq")
            raise AssertionError("equality callback must never execute")

    monkeypatch.setattr(subject._MODULES[4][1], attribute, ForeignMetadata())
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.admit_native_demo_vendors_v2()
    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject.require_native_demo_vendors_v2(admitted)
    assert called == []


def test_descriptor_kind_rejection_uses_identity_not_foreign_metaclass_equality():
    called = []

    class ForeignMeta(type):
        def __eq__(cls, _other):
            called.append(True)
            raise AssertionError("metaclass equality must never execute")

    class ForeignDescriptor(metaclass=ForeignMeta):
        pass

    with pytest.raises(subject.NativeDemoVendorIntegrityError):
        subject._normalise_descriptor(ForeignDescriptor())
    assert called == []

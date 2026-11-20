# Disposable native private-directory test helper

`tests/native_private_temp_v1.py` contains the original SID/DACL and
`private-custody` temporary-root helpers, extracted without changing their
function ASTs. Existing custody and Windows inheritance tests re-export their
original helper names. The ENTRY worker fixture imports the small helper
directly instead of importing a custody test module and its Demo caller chain.

The helper performs no filesystem or ACL operation at import. Callers create
their own pytest temporary directory; Windows grants the same protected,
inheritable owner/SYSTEM/Administrators ACEs and POSIX retains mode 0700.
The additional Users ACE remains available only to existing rejection tests.
No product module imports this helper and no global ACL is modified.

Validation: 25 tests passed with zero failures/errors/skips, including the
original four Windows inheritance/unauthorized-ACE cases, custody regression,
native root/no-clobber checks and original ENTRY worker LONG/SHORT positives.
Three AST identity tests preserve the original helper behavior. Clean child
imports loaded neither the custody/inheritance test modules nor Demo native
caller/source-only modules: helper 0.012s, worker fixture 7.483s on this host.
These are test startup measurements, not trading latency or venue acceptance.
The full validation intentionally imported the old inheritance test module and
took 146.98s; future worker-only tests avoid that heavy import chain.

Retained local result: `reports/light-private-temp-native-regression.xml`.
No account, key, runtime, database, operator Arm or venue was used.

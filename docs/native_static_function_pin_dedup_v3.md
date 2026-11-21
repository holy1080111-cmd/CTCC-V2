# Static duplicate function predicates

During source-guard assembly, multiple imports can refer to the same Python
function. The assembler represents that function once only when all captured
checks have identical obligations. It retains the first occurrence and strong
function references. It does not cache a guard result or an execution decision.

Equivalence requires the same function, code, globals, builtins, defaults,
keyword-default dictionary and closure identities. Keyword snapshots require
exact string names in the same order and identical value objects; closure-cell
snapshots require identical objects. Ordinary compiled-source records must have
the same defining globals/builtins and the same typed builtin-name/value identity
checks. Differing duplicate snapshots reject the assembly instead of selecting
one. Comparisons never call a captured value's equality method.

Factory and other nonstandard source validators are not collapsed. Every
original validator call remains. Namespace/attribute bindings, class shapes,
source files, fresh file reads and actual-write guard frequency are untouched.
Every retained function is still checked against its current mutable state on
every guard invocation. Source changes and revocation after an actual await must
still reject the same attempt.

The scoped tests exercise every differing duplicate slot, typed keyword/cell
and compiled-lookup obligations, callback-free rejection, retained factory
validator counts, distinct function identities, alias rebinding, code mutation,
fresh source bytes after a real yield and after-await liveness. They use declared
synthetic metadata/upstream ingress and do not establish database, exchange,
credential, runtime or trading acceptance. Performance remains subject to a
separate exact-source clean-interpreter measurement.

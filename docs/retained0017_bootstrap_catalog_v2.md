# Separate source17 bootstrap-owner catalog DATA

`require_bootstrap_catalog17_v2` is a separate readonly DATA gate. It does not
change `require_sealed_catalog17_v1`, the original normalizer, or the existing
42c73… reference purpose. Neither API grants migration or runtime authority.
The new function accepts the actual raw catalog, never a receipt or pass flag.

The actual physical actor must be the PG17.11 initdb bootstrap owner OID10,
with all seven original bootstrap role flags true, exactly zero memberships
and the empty membership digest. Its actual shared-dependency array must be
empty. The entire original normalization must match the separately reviewed
e1f30… literal and the same frozen0001–0017 source prefix. Every object, raw
node semantic, default, constraint, index, trigger, function, ACL, used native
definition and ordinary dependency remains covered. No dependency is filled,
dropped, filtered or substituted by this gate.

The two retained public references came from independent initdb clusters,
each with one fresh database owned by its original bootstrap role. Their
actual cluster identifiers and database names differ; both actual normalized
catalogs have exactly the same bytes. Controller/input/DDL/query/source-file
hashes and the exact b50 source archive identity are retained in the origin.
The original raw/normalized/physical/receipt bytes remain unchanged. No TLS
private key, input packet, original host database or operator file is shipped.

The primary REL_17_11 proof is explicit. `recordDependencyOnOwner` calls
`recordSharedDependencyOn` with `SHARED_DEPENDENCY_OWNER`; that routine adds a
row only for a non-pinned target. `IsPinnedObject` treats the bootstrap role
as pinned, with its source OID10 below the source cutoff12000 and outside the
explicit large-object/public-namespace/database exceptions. Native omission
of the owner edges is therefore a separate physical purpose. It is not a
license to remove the original61 observed edges from a non-bootstrap owner.
The four exact primary files and their SHA256/URLs are retained alongside
PostgreSQL's copyright/license. The DATA gate rereads those pinned bytes.

Tests replay both actual positives, preserve the original v1 positive and
cross-purpose refusals, and mutate physical flags, owner/membership, catalog
fields/defaults/dependencies/version and primary-source availability. These
pure tests do not execute SQL or accept a maintained/retained database. Actual
maintenance still needs independently owned connection/source/profile/lock
and safety checks, approved lineage and a separately reviewed route.

# Independent Notion outbox runtime

`app.main` owns the default-off `NotionOutboxRuntime` lifecycle. The runtime has
its own thread and event loop. Durable filesystem checks, local token permission
checks, and HTTP do not execute on the API/trading event loop. Reporter startup,
missing configuration, destination mismatch, or Notion failure cannot stop API
startup or invoke an order callback. Core shutdown runs before reporter shutdown;
the latter still runs if a core shutdown operation raises.

The worker uses the existing append-only `outbox` and `NotionDeliveryAdapter`.
It does not construct trading reports from arbitrary data or call exchange code.
Only real, accepted post-submit payloads belong in its trusted queue. Deployment
must bind the producer and worker to the same persistent queue and destination.
An empty queue is not evidence of delivery or Demo acceptance.

Every pass verifies the pinned local destination bytes, loads a private token,
performs authenticated database/data-source GET readback, and validates all four
property IDs, names and types before claiming a report. The database must contain
the pinned data source. No first-data-source inference, property creation, schema
repair, proxy, redirect, or hidden HTTP retry is performed. The current API
separates database data-source membership from the data-source property schema.
[Database retrieval](https://developers.notion.com/reference/retrieve-database),
[data-source retrieval](https://developers.notion.com/reference/retrieve-a-data-source),
[API versioning](https://developers.notion.com/reference/versioning).

Fair passes contain at most 16 reports, processed one at a time under a separate
native process lease and a total async deadline. Each uses the existing durable
lease, dispatch marker, and verified envelope. Delivered/exhausted reports remain
terminal. Expired claimed work can recover; expired dispatching work becomes
uncertain. An ambiguous page create or cancellation never automatically creates
another page. An uncertain or unresolved dispatched report pauses every later
request, including schema GETs after restart. Orphan journals from a late
publication failure are retained.
Unknown work needs explicit outbox reconciliation while workers are quiesced.
Independent queues and external Notion edits do not have an exactly-once guarantee.

Missing/bad configuration reports a static `pending` code and retries with a
bounded delay (up to 300 seconds). A verified rate limit honors `Retry-After`
through append-only, hash-linked cooldown records in `.notion-runtime` beneath
the trusted queue. The cooldown survives worker/process restart and is shared
by cooperating runtimes. Missing or malformed limits remain paused until an
explicit quiesced recovery. The delivery adapter retains uncertain state for a
rate limit after dispatch; expiry of a cooldown cannot resolve that uncertainty.
Arbitrary response
bodies and exception details never enter runtime logs. Current local status is
available at `app.state.notion_outbox_runtime`; it grants no execution authority.
[Rate-limit contract](https://developers.notion.com/reference/request-limits).

## Secure local setup

The runtime REST token must be entered through the local hidden-input prompt,
never through chat, command arguments, environment variables, or a report. The
setup command performs GET requests only and leaves the worker disabled. It
reads actual property IDs from the API and independently reads back the complete
binding. A mismatch fails closed; the tool does not create replacement fields.

The original CTCC destination and verified field names can be supplied without
asking the operator to rediscover them. Run from the installed canonical source,
using its validated Python environment and the real persistent producer queue:

```powershell
python -m scripts.setup_notion_outbox `
  --outbox-root '<absolute existing persistent outbox directory>' `
  --database-id 13d5e61fce534184a42e0b01c4f372d3 `
  --data-source-id cbbdc739-2723-4e07-a5d7-7d4d1395658e `
  --report-id-property-name '報告編號' `
  --envelope-sha256-property-name 'Outbox Envelope SHA256' `
  --payload-sha256-property-name 'Outbox Payload SHA256' `
  --metadata-json-property-name 'Outbox Metadata JSON'
```

The report-ID property must have `title` type and the other three must have
`rich_text` type, as required by the reviewed adapter. The connector's generic
text-field description alone cannot attest those REST types. Generic interactive
database/source/property selection is available when nonsecret options are omitted.

Setup defaults to `%LOCALAPPDATA%\CTCC\private-notion` on Windows and
`~/.local/share/CTCC/private-notion` on POSIX. Token files are created exclusively,
with a protected current-user-only Windows DACL or POSIX mode `0600`, before
token bytes are written. Parent directories and the exact native file handle are
pinned during permission/owner/identity validation, IO and readback. Windows
denies replacement while the handle is open; POSIX uses relative no-follow open
and checks descriptor identity/owner/mode/link count before and after IO. Existing
files are never overwritten. The tool rejects
Git/source/workspace paths, evidence directories, queue paths and reparse paths.
Only the exact new file's Windows permissions change, never machine policy or
directory ACLs. Token files must be excluded from every source/archive/evidence
bundle and build context. In containers, provision the private token read-only
outside the image and maintain ownership/permissions for the runtime identity.

Successful setup prints only nonsecret paths and the destination-file SHA256:

| Runtime setting | Meaning |
| --- | --- |
| `NOTION_OUTBOX_ENABLED` | Defaults to `false`; enable only after binding review |
| `NOTION_OUTBOX_ROOT` | Absolute, existing persistent producer queue |
| `NOTION_OUTBOX_TOKEN_FILE` | Private external token file |
| `NOTION_OUTBOX_DESTINATION_FILE` | Four actual property pins from setup |
| `NOTION_OUTBOX_DESTINATION_SHA256` | Exact destination-file digest |
| `NOTION_OUTBOX_POLL_SECONDS` | 1–300, default 30 |
| `NOTION_OUTBOX_PASS_TIMEOUT_SECONDS` | 1–300, default 30 |
| `NOTION_OUTBOX_BATCH_SIZE` | 1–16, default 16 |

The setup receipt contains binding hashes, time and GET-only status, no token.
Restarting the API starts only the reporting worker when configured; it does not
restore trading Arm. Reporter failures leave accepted trading evidence intact.

For an unknown rate-limit duration, stop every reporting worker, preserve the
entire control directory and outbox journals, and obtain an operator-reviewed
external confirmation that the rate limit has cleared. Record that confirmation
with the preserved control-directory hash before archiving the control directory
outside the active queue. Only then may the runtime create a new control history.
Do not delete or edit individual cooldown records to remove a pause. Any uncertain
report must separately use the existing explicit outbox reconciliation with
verified remote page state; resetting cooldown history does not make it retryable.

## Acceptance boundary

The targeted tests use native durable files and synthetic HTTP: successful page
readback, restart idempotency, crashed dispatch recovery, cancellation during
create, retry timing, fair scheduling, preserved orphan journals, exact schema
binding, private-file permissions and API lifecycle isolation. They do not claim
real Notion delivery. A real token, verified production schema and real accepted
trade payload are still required for actual delivery acceptance.

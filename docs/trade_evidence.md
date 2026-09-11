# Same-candidate evidence packet

Offline Linux implementation verified at `021183f`, 2026-09-12. This is step 10 of the
[Notion construction specification](https://app.notion.com/p/3d832165a6888173bfb1df896604fc7c),
after the source/structure/economics/portfolio checkpoint `6e2a00c`.
No renderer result is an order permit, a completed twelve-gate engine or a
post-render execution recheck.

## Required output

Each candidate retains exactly these files:

```text
artifacts/trade_evidence/<report_id>/
  4h.png
  1h.png
  15m.png
  5m.png
  summary.png
  report.json
```

All panels use the same recorded OHLC/analysis and unchanged candidate. They show
the recorded regime, trend/structure, relevant support/resistance, OB/FVG and
liquidity evidence, entry zone, candidate and executable reference, actual
trigger time, SL/TP and raw/effective score. Missing observations remain unknown.
Score is a ranking input, not win probability. Summary answers why, why now,
entry, thesis invalidation, target and recorded gate outcomes. Rendering must
not fill an unevaluated gate with a pass or invent a source level for a label.

Panels crop only after calculating needed causal history. Candle timestamps are
opens; close-confirmed triggers belong at their actual close time. Structure
levels cannot appear as known before their confirmation. Independent timeframe
scales and displayed windows must be explicit, with all candidate levels visible.
Source rows, precise decimal values, full gate reasons and original text remain
inspectable in JSON even when a panel uses a compact label.

## Implementation boundaries

The preparation service creates an immutable source-bound snapshot, reconstructs
events/geometry where present, and rejects identity or provenance mismatch.
Only pre-render G1–G11 gate prefixes are accepted. Consistent gate records are
still claims supplied by the future trusted qualification service; their schema
does not prove those evaluators ran. Hashes bind content, not external authenticity.

The renderer is pure and returns six immutable byte payloads. It uses pinned
`Pillow==12.3.0` and the embedded Aileron font, with no system-font search, browser,
network or AI-image generation. Pillow's default font has a limited character
set; unsupported text must be visibly escaped or otherwise disclosed, never
silently replaced with missing glyphs. Full Unicode text stays in the JSON.
The [official font documentation](https://pillow.readthedocs.io/en/stable/reference/ImageFont.html#PIL.ImageFont.load_default)
describes the embedded font. Version availability was checked against
[the PyPI release](https://pypi.org/project/pillow/12.3.0/).

Reproducible bytes require matching renderer, Pillow, font and rasterizer/runtime.
Cross-OS PNG compression or rasterization is not assumed byte-identical. Each
packet records its renderer identity and image hashes; decoded geometry and
visual readability require their own verification.

The publisher accepts an already provisioned trusted root and fixed filenames.
It must reject traversal, reserved device names, symlinks/junctions/reparse points,
conflicting packets and incomplete previous attempts without overwriting them.
All five PNGs must decode and match the bounded manifest before `report.json` is
published last as the logical completion marker. This is not a six-file atomic
filesystem transaction. Identical complete packets may be recognized idempotently;
partial or changed packets fail closed. Runtime artifacts are excluded from Git
and the source-release manifest and Docker build context, not uploaded to the
public repository.

POSIX publication uses held, no-follow directory descriptors, relative operations,
an exclusive root lease, file and directory synchronization, and bounded readback.
Windows publication must acquire real directory-data access with delete sharing
disabled throughout the ancestor chain, not merely attribute-only handles (which
do not enforce the intended sharing restriction). It also uses an exclusive
publisher handle. File flush and report-last logical completion on Windows do not
claim power-loss durability for directory metadata. See the
[Microsoft CreateFileW sharing contract](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).

On this development host, opening the user-profile ancestor with the required
access currently returns access denied. The full Windows publisher therefore
fails closed before writing a PNG. Native primitive sharing/rename checks on a
test-owned directory are separate evidence and do not establish that the complete
ancestor chain can be opened. Do not weaken these checks, change ACLs automatically,
or count platform-specific skips as Windows acceptance. The deployment target is
validated independently in an isolated Linux container; native Windows end-to-end
publication remains a known environmental blocker.

Actual publication/readback completion belongs in a separate immutable receipt,
not a rewrite of the rendered report. A later execution recheck must fetch new
executable data **after** this completion, revalidate the unchanged candidate and
cancel if conditions have deteriorated. The evidence service does not fetch that
quote, submit orders, reserve account risk or synchronize Notion.

## Visual acceptance and remaining integration

The second visual-review build was inspected across all twenty PNGs: long,
short, missing trigger/insufficient 5m history, and captured price outside the
unchanged entry zone. Ordinary axes use absolute prices; precision-aliasing
scales use a disclosed origin/delta. Labels follow actual price positions with
collision avoidance, exact values remain in JSON, and RR uses compact readable
formatting. The summary distinguishes recorded evaluation from preparation and
warns if the trigger has already expired when prepared. Missing values stay
unknown and all examples remain explicitly synthetic with order eligibility NO.
These visual files were written to a separate review directory, not issued
production publication receipts, and are excluded from the public source archive.

Reproduce the input cases with `synthetic_snapshot` in
`tests/unit/test_trade_evidence_pipeline.py`. Its G1–G11 records are deliberately
synthetic presentation claims, not an actual gate-engine integration result.
The cancel case shows a captured price outside the zone **before** rendering;
it is not the still-pending post-render price-movement cancellation scenario.

The 371 new passing Linux cases cover source mutation, identity/geometry mismatch,
typed-copy tampering, bounded inputs, PNG decoding, deterministic same-runtime
output, exact image hashes, publication conflicts/crashes/races and POSIX path
defenses. The complete immutable-source regression for `021183f` passed 3061
tests, with 10 Windows-only skips and 5 existing/expected warnings. Alembic was
0016 with no schema drift, and all 473 source manifest entries matched. Native
Windows acceptance remains limited as described above. Synthetic fixtures
demonstrate engineering behavior; they do not count as real shadow observations,
Demo trades or strategy outcomes. Actual G12 integration, fresh post-render
recheck and all downstream runtime acceptance remain separate pending work.

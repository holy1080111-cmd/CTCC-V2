"""Browser-only consistency check for the optional public snapshot receipt."""

SNAPSHOT_RECEIPT_SCRIPT = r"""
const SNAPSHOT_RECEIPT_LIMIT = 8192;
const SNAPSHOT_RECEIPT_NAME = /(?:^snapshot-receipt|\.receipt)\.json$/i;
const SNAPSHOT_DIGEST = /^[a-f0-9]{64}$/;
const SNAPSHOT_RECEIPT_FIELDS = [
  "as_of", "environment", "execution_authority", "historical_availability_verified",
  "instrument_id", "journal_sha256", "packet_sha256", "plan_sha256", "schema",
  "snapshot_sha256", "source_authenticity_verified"
];

function selectSnapshotFiles(files) {
  if (!files || files.length < 1 || files.length > 2) return null;
  const selected = Array.from(files);
  const receipts = selected.filter(file => SNAPSHOT_RECEIPT_NAME.test(file.name));
  const snapshots = selected.filter(file => !SNAPSHOT_RECEIPT_NAME.test(file.name));
  if (snapshots.length !== 1 || receipts.length > 1) return null;
  return {snapshot: snapshots[0], receipt: receipts[0]};
}

async function checkSnapshotReceipt(snapshot, receipt) {
  if (!receipt) return {file: snapshot, digest: null};
  if (receipt.size < 1 || receipt.size > SNAPSHOT_RECEIPT_LIMIT || !globalThis.crypto?.subtle) {
    return null;
  }
  const raw = await receipt.text();
  const fields = JSON.parse(raw);
  if (fields === null || typeof fields !== "object" ||
      JSON.stringify(Object.keys(fields)) !== JSON.stringify(SNAPSHOT_RECEIPT_FIELDS) ||
      JSON.stringify(fields) !== raw ||
      fields.schema !== "ctcc.local_analysis.public_snapshot_export.v1" ||
      fields.environment !== "anonymous_global_public" ||
      fields.source_authenticity_verified !== false ||
      fields.historical_availability_verified !== false ||
      fields.execution_authority !== false ||
      !["journal_sha256", "packet_sha256", "plan_sha256", "snapshot_sha256"].every(
        key => typeof fields[key] === "string" && SNAPSHOT_DIGEST.test(fields[key])
      ) ||
      typeof fields.instrument_id !== "string" ||
      typeof fields.as_of !== "string" || !fields.as_of.endsWith("+00:00")) {
    return null;
  }
  const exactBytes = await snapshot.arrayBuffer();
  const digestBytes = await crypto.subtle.digest("SHA-256", exactBytes);
  const digest = Array.from(new Uint8Array(digestBytes), value => value.toString(16).padStart(2, "0"))
    .join("");
  if (digest !== fields.snapshot_sha256) return null;
  const market = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(exactBytes));
  if (market === null || typeof market !== "object" ||
      market.instrument_id !== fields.instrument_id ||
      market.received_at !== fields.as_of.replace(/\+00:00$/, "Z")) {
    return null;
  }
  return {file: exactBytes, digest};
}

function snapshotAnalysisFinished(digest) {
  const pairing = digest ? "核對單與快照相符。" : "未提供核對單。";
  return "分析完成。" + pairing + "匯入來源仍未驗證，交易資格維持拒絕。";
}
"""

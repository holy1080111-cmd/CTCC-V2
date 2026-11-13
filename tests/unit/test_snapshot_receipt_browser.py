"""Executable browser receipt checks, with no DB or exchange calls."""

import json
import re
import shutil
import subprocess

import pytest

from app.dashboard.local_analysis_page import LOCAL_ANALYSIS_HTML
from app.dashboard.offline_snapshot_only_page import OFFLINE_SNAPSHOT_ONLY_HTML

_PROBE = r"""
const assert = require("node:assert/strict");
const {createHash, webcrypto} = require("node:crypto");
const vm = require("node:vm");
const script = __SCRIPT__;
const bytes = '{"instrument_id":"BTC-USDT-SWAP","received_at":"2026-11-13T16:05:15.678893Z"}';
const sha = createHash("sha256").update(bytes).digest("hex");
const values = {
  as_of: "2026-11-13T16:05:15.678893+00:00",
  environment: "anonymous_global_public",
  execution_authority: false,
  historical_availability_verified: false,
  instrument_id: "BTC-USDT-SWAP",
  journal_sha256: "b".repeat(64),
  packet_sha256: "c".repeat(64),
  plan_sha256: "d".repeat(64),
  schema: "ctcc.local_analysis.public_snapshot_export.v1",
  snapshot_sha256: sha,
  source_authenticity_verified: false
};
const file = (name, value) => new File([value], name, {type:"application/json"});
const snapshot = file("MarketSnapshot.json", bytes);
const receipt = file("snapshot-receipt.json", JSON.stringify(values));
function board(files, {crypto = webcrypto, resultSha = sha} = {}) {
  const calls = [];
  const elements = new Map();
  const element = () => ({
    events: {}, value: "", hidden: true, disabled: false,
    addEventListener(key, fn) { this.events[key] = fn; },
    replaceChildren() {},
    append() {}
  });
  function get(id) {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  }
  const context = {
    TextDecoder, crypto, AbortController, setTimeout, clearTimeout,
    document: {getElementById: get, createElement: element},
    window: {addEventListener() {}},
    localStorage: {getItem: () => null},
    navigator: {},
    fetch: async (path, options) => {
      calls.push([path, options]);
      return {ok: true, status: 200, json: async () => ({
        input_sha256: resultSha,
        schema_version: "ctcc.offline_snapshot_analysis.v1",
        source_authenticity_verified: false, execution_authority: false,
        analysis: {trade_ready: false, blockers: ["imported_snapshot_unverified"]}
      })};
    }
  };
  vm.runInNewContext(script, context);
  get("snapshot").files = files;
  get("token").value = "test-only-token";
  return {get, calls};
}
async function run(files, config) {
  const data = board(files, config);
  await data.get("analyze").events.click();
  assert.equal(data.get("token").value, "");
  assert.equal(data.get("snapshot").disabled, false);
  assert.equal(data.get("analyze").disabled, false);
  return data;
}
async function refused(files, config) {
  const {calls, get} = await run(files, config);
  assert.equal(calls.length, 0, get("status").textContent);
  assert.equal(get("result").hidden, true);
  assert.doesNotMatch(get("status").textContent, /test-only-token/);
}
(async () => {
  for (const matching of [receipt, file("copied.receipt.json", JSON.stringify(values))]) {
    const {calls, get} = await run([matching, snapshot]);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/api/analysis/snapshot");
    assert.equal(calls[0][1].credentials, "omit");
    assert.equal(calls[0][1].headers["X-CTCC-Token"], "test-only-token");
    assert.equal(Buffer.from(calls[0][1].body).toString(), bytes);
    assert.equal(get("result").hidden, false);
    assert.match(get("status").textContent, /核對單與快照相符.*匯入來源仍未驗證/);
  }
  const bare = await run([snapshot], {crypto: {}});
  assert.equal(bare.calls[0][1].body, snapshot);
  assert.match(bare.get("status").textContent, /未提供核對單.*匯入來源仍未驗證/);
  const changedResponse = await run([receipt, snapshot], {resultSha: "e".repeat(64)});
  assert.equal(changedResponse.calls.length, 1);
  assert.equal(changedResponse.get("result").hidden, true);
  await refused([receipt, file("changed.json", bytes + " ")]);
  await refused([receipt, snapshot], {crypto: {}});
  await refused([receipt, snapshot, file("other.json", bytes)]);
  await refused([receipt]);
  await refused([snapshot, file("snapshot-receipt.json", " ".repeat(8193))]);
  await refused([snapshot, file("snapshot-receipt.json", '{"as_of":null,' + JSON.stringify(values).slice(1))]);
  for (const changed of [
    {execution_authority: true}, {source_authenticity_verified: true},
    {instrument_id: "ETH-USDT-SWAP"}, {as_of: "2026-11-13T16:05:15.678894+00:00"}
  ]) {
    await refused([snapshot, file("snapshot-receipt.json", JSON.stringify({...values, ...changed}))]);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize(
    "page", (LOCAL_ANALYSIS_HTML, OFFLINE_SNAPSHOT_ONLY_HTML), ids=("paper", "offline")
)
def test_optional_receipt_checks_before_analysis(page: str) -> None:
    assert 'id="snapshot" type="file"' in page
    assert 'multiple aria-describedby="snapshotHint"' in page
    assert "/api/analysis/snapshot" in page
    node = shutil.which("node")
    if node is None:
        return
    match = re.search(r"<script>(.*?)</script>", page, re.DOTALL)
    assert match is not None
    result = subprocess.run(
        [node, "-"],
        input=_PROBE.replace("__SCRIPT__", json.dumps(match[1])),
        text=True,
        encoding="utf-8",
        timeout=10,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

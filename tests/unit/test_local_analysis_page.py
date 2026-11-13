"""Browser entry for disconnected imported-snapshot analysis."""

import json
import re
import shutil
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import api_router
from app.dashboard.local_analysis_page import LOCAL_ANALYSIS_HTML


def verify_paper_browser_mutations_remain_fenced_across_reload() -> None:
    """Run the offline browser script explicitly using a local Node.js."""
    node = shutil.which("node")
    assert node is not None
    script = re.search(r"<script>(.*?)</script>", LOCAL_ANALYSIS_HTML, re.DOTALL)[1]
    probe = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const script = __SCRIPT__;
const order = '88888888-8888-4888-8888-888888888888';
const position = '99999999-9999-4999-8999-999999999999';
const installation = 'a'.repeat(64);
const runtime = '11111111-1111-4111-8111-111111111111';
const newer = '22222222-2222-4222-8222-222222222222';
const operation = '33333333-3333-4333-8333-333333333333';
let view = {persistent:true, recovered:true, installation_binding:installation, runtime_id:runtime,
            state:{account:{}, orders:[{id:order,status:'pending'}],
                   positions:[{id:position,status:'open'}]}};
const store = new Map();
let outcome = null;
const calls = [];
const response = (status, data) => ({status, ok: status < 300, json: async () => data});
function tab() {
  const elements = new Map();
  function element() {
    return {value:'',checked:false,hidden:true,children:[],events:{},
            addEventListener(k,f){ this.events[k]=f; },
            replaceChildren(x){ this.children=[x]; this.value=x.value; },
            append(x){this.children.push(x);}};
  }
  const get = id => { if (!elements.has(id)) elements.set(id,element()); return elements.get(id); };
  const board = {document:{getElementById:get,createElement:element},
                 window:{addEventListener(){}}, AbortController, setTimeout, clearTimeout,
                 navigator:{locks:{request:async (_,opts,run)=>run({})}},
                 crypto:{randomUUID:()=>operation},
                 localStorage:{getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
                 fetch:async (path, options) => {
                   calls.push([path,options]);
                   assert.equal(options.credentials,'omit');
                   assert.equal(options.redirect,'error');
                   assert.equal(options.headers['X-CTCC-Token'],'test-local-token');
                   if (options.method==='GET') return response(200,view);
                   assert.equal(options.headers['X-CTCC-Paper-Runtime'],view.runtime_id);
                   assert.equal(store.size,1);
                   if (!outcome) throw new Error('transport loss');
                   return outcome(path, options);
                 }};
  vm.runInNewContext(script,board);
  return {get, click: id=>get(id).events.click(), authorize:()=>{get('token').value='test-local-token';get('paperConfirm').checked=true;}};
}
(async () => {
  let a=tab();
  a.authorize(); await a.click('paperRead');
  a.get('paperPendingOrder').value=order;
  a.get('paperOpenPosition').value=position;
  a.get('paperClosePrice').value='101';
  a.authorize();
  const cancelled='/api/paper/orders/'+order+'/cancel';
  const sent=a.click('paperCancel');
  await a.click('paperClose');
  await sent;
  const posts=()=>calls.filter(([,o])=>o.method==='POST');
  assert.equal(posts().length,1);
  assert.equal(posts()[0][0],cancelled);
  assert.equal(Object.hasOwn(posts()[0][1],'body'),false);
  assert.equal(Object.hasOwn(posts()[0][1].headers,'Content-Type'),false);
  assert.equal(a.get('token').value,'');
  const marker=[...store.values()][0];
  assert.equal(marker.includes('test-local-token'),false);
  assert.equal(marker.includes('101'),false);
  assert.equal(JSON.parse(marker).target,order);
  a=tab();
  for (const id of ['paperOrder','paperTick','paperCancel','paperClose']) assert.equal(a.get(id).disabled,true);
  a.authorize(); await a.click('paperRead');
  a.get('paperRecoveryConfirm').checked=true;
  a.authorize(); await a.click('paperResume');
  assert.equal(store.size,1);
  view={...view,runtime_id:newer,installation_binding:'b'.repeat(64)};
  a.authorize(); await a.click('paperRead');
  assert.equal(a.get('paperResume').disabled,true);
  view={...view,installation_binding:installation,recovered:false};
  a.authorize(); await a.click('paperRead');
  assert.equal(a.get('paperResume').disabled,true);
  view={...view,recovered:true};
  a.authorize(); await a.click('paperRead');
  a.get('paperRecoveryConfirm').checked=true;
  view={...view,state:{...view.state,account:{cash_balance:'123'}}};
  a.authorize(); await a.click('paperResume');
  assert.equal(store.size,1);
  a.get('paperRecoveryConfirm').checked=true;
  a.authorize(); await a.click('paperResume');
  assert.equal(store.size,0);
  a.get('paperOpenPosition').value=position;
  a.get('paperClosePrice').value='101';
  outcome=(path, options)=> {
    assert.equal(path,'/api/paper/positions/'+position+'/close');
    assert.deepEqual(JSON.parse(options.body),{price:'101',reason:'manual_local:'+operation});
    return response(200,{id:position,status:'closed',close_reason:'manual_local:'+operation});
  };
  a.authorize(); await a.click('paperClose');
  assert.equal(posts().length,2);
  assert.equal(store.size,0);
})().catch(()=>{process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-"],
        input=probe.replace("__SCRIPT__", json.dumps(script)),
        text=True,
        encoding="utf-8",
        timeout=10,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_local_analysis_page_is_served_with_no_external_assets() -> None:
    app = FastAPI(openapi_url=None, docs_url=None)
    app.include_router(api_router)
    with TestClient(app) as client:
        response = client.get("/dashboard/local-analysis")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    html = response.text
    assert "<script>" in html and "<style>" in html
    assert "<script src=" not in html and "<link rel=" not in html
    assert "https://" not in html and "http://" not in html
    assert "匯入檔案的來源未經驗證" in html
    assert 'href="/readiness"' in html
    assert 'href="#paper-section"' in html
    assert 'href="/docs' not in html


def test_local_analysis_page_only_posts_exact_file_to_existing_offline_api() -> None:
    app = FastAPI(openapi_url=None, docs_url=None)
    app.include_router(api_router)
    paths = {getattr(route, "path", None): route for route in api_router.routes}
    assert paths["/api/analysis/snapshot"].methods == {"POST"}
    html = TestClient(app).get("/dashboard/local-analysis").text
    assert 'fetch("/api/analysis/snapshot", {' in html
    assert 'method: "POST"' in html
    assert "body: file" in html
    assert 'credentials: "omit"' in html
    assert 'redirect: "error"' in html
    assert "sessionStorage" not in html
    assert 'tokenInput.value = "";' in html
    assert "response.text()" not in html
    assert "result.textContent = JSON.stringify(value, null, 2);" in html
    assert 'paperFetch("/api/paper/runtime/state"' in html
    assert 'method: "GET"' in html
    assert "OKX 實際持倉仍未知" in html
    assert paths["/api/paper/orders"].methods == {"POST"}
    assert paths["/api/paper/ticks"].methods == {"POST"}
    assert 'runPaperAction("order", "/api/paper/orders"' in html
    assert 'runPaperAction("tick", "/api/paper/ticks"' in html
    assert paths["/api/paper/orders/{order_id}/cancel"].methods == {"POST"}
    assert paths["/api/paper/positions/{position_id}/close"].methods == {"POST"}
    assert '"/cancel", undefined' in html
    assert '"/close", {price}' in html
    assert 'paperLimitWrap.hidden = paperMode.value !== "limit";' in html
    assert 'payload.client_order_id = "local-ui-" + operation;' in html
    assert "localStorage.setItem(PAPER_FENCE, retained);" in html
    assert '"X-CTCC-Paper-Runtime": runtimeId' in html
    assert "paperConfirm.checked = false;" in html
    assert "/api/paper/reset" not in html and "/api/okx" not in html
    for denied in (
        "source_authenticity_verified === false",
        "execution_authority === false",
        "analysis.trade_ready === false",
        'includes("imported_snapshot_unverified")',
    ):
        assert denied in html


def test_dashboard_links_self_contained_local_analysis_page() -> None:
    app = FastAPI(openapi_url=None, docs_url=None)
    app.include_router(api_router)
    html = TestClient(app).get("/dashboard").text
    assert 'href="/dashboard/local-analysis"' in html


if __name__ == "__main__":
    verify_paper_browser_mutations_remain_fenced_across_reload()

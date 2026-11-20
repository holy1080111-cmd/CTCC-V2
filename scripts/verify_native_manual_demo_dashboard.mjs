// Executable DOM behavior checks. All fetches are fixture-owned; no network I/O.
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import fs from "node:fs";
import vm from "node:vm";

const [htmlPath, reportPath] = process.argv.slice(2);
assert.ok(htmlPath && reportPath, "Expected rendered HTML path and new report path");
const html = fs.readFileSync(htmlPath, "utf8");
const metadata = JSON.parse(fs.readFileSync(htmlPath + ".headers.json", "utf8"));
const script = /<script>([\s\S]*?)<\/script>/.exec(html)?.[1];
const style = /<style>([\s\S]*?)<\/style>/.exec(html)?.[1];
assert.ok(script && style);
const config = JSON.parse(/const config = (\{[^\n]+\});/.exec(script)[1]);
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(value => value[1]);
assert.equal(new Set(ids).size, ids.length);
const hash = text => createHash("sha256").update(text, "utf8").digest("hex");
const sourceHash = text => createHash("sha256").update(text, "utf8").digest("base64");
const token = "synthetic-dom-only-token-00000000000000000000000000";
const cleanStatus = {recovered:true, armed:false, emergency_stop:false, locked:false};
const results = [];
let totalRequests = 0;

function response(value, status = 200, contentType = "application/json") {
  return {ok:status >= 200 && status < 300, status,
    headers:{get:() => contentType}, text:async () => JSON.stringify(value)};
}
function board() {
  const elements = new Map(ids.map(id => {
    const element = {value:"", textContent:"", disabled:false, hidden:false, events:{},
      addEventListener(name, callback) { this.events[name] = callback; }};
    Object.defineProperty(element, "innerHTML", {set() { throw Error("Unsafe HTML write"); }});
    return [id,element];
  }));
  const calls = [];
  const queue = [];
  const context = vm.createContext({
    document:{getElementById(id) { assert.ok(elements.has(id), id); return elements.get(id); }},
    fetch:async (path, options) => {
      assert.ok(path.startsWith("/api/demo-automation/"));
      assert.equal(options.credentials, "omit");
      assert.equal(options.redirect, "error");
      assert.equal(options.mode, "same-origin");
      assert.equal(options.cache, "no-store");
      assert.equal(options.referrerPolicy, "no-referrer");
      assert.equal(options.headers["X-CTCC-Token"], token);
      calls.push({path, method:options.method,
        body:options.body === undefined ? undefined : JSON.parse(options.body)});
      totalRequests += 1;
      assert.ok(queue.length, "Unexpected automatic request");
      const next = queue.shift();
      if (next instanceof Error) throw next;
      return typeof next === "function" ? await next() : next;
    },
  });
  vm.runInContext(script, context, {timeout:2000});
  const get = id => elements.get(id);
  const fire = async (id, event = "click") => await get(id).events[event]();
  const authenticate = async () => { get("token").value = token; await fire("token","input"); };
  const refresh = async (value = cleanStatus) => { queue.push(response(value)); await fire("refresh"); };
  const order = () => {
    get("instrument").value = config.symbols[0];
    get("strategy").value = config.strategies[0];
    get("contracts").value = "1";
    get("leverage").value = "3";
  };
  const arm = () => { get("armPhrase").value = config.phrases.arm; get("executePhrase").value = config.phrases.execute; };
  return {get,fire,authenticate,refresh,queue,calls,order,arm};
}
async function check(name, body) { await body(); results.push({name,status:"PASS"}); }

try {
  await check("exact_hash_csp_and_secret_free_initial_page", async () => {
    assert.equal(metadata.synthetic_configuration,true);
    assert.equal(metadata.runtime_settings_loaded,false);
    assert.equal(metadata.html_sha256,hash(html));
    const csp = metadata.headers["content-security-policy"];
    assert.ok(csp.includes("default-src 'none'"));
    assert.ok(csp.includes("connect-src 'self'"));
    assert.ok(csp.includes("script-src 'sha256-" + sourceHash(script) + "'"));
    assert.ok(csp.includes("style-src 'sha256-" + sourceHash(style) + "'"));
    assert.ok(csp.includes("form-action 'none'"));
    assert.ok(csp.includes("frame-ancestors 'none'"));
    assert.equal(metadata.headers["cache-control"],"no-store");
    assert.ok(!/\b(?:src|href|onclick|onsubmit)=/.test(html));
    assert.ok(!/localStorage|sessionStorage|indexedDB|document\.cookie|sendBeacon|setInterval|innerHTML/.test(script));
    assert.ok(!html.includes(token));
    assert.equal(config.strategies.length,8);
    assert.deepEqual(config.leverage,[3,5,8,10,20]);
    assert.equal(config.execution_enabled,true,"Render the explicit execution-enabled synthetic fixture");
    assert.equal(config.max_contracts,"1","Render the explicit one-contract synthetic fixture");
    const view = board();
    assert.equal(view.calls.length,0);
    assert.equal(view.get("armExecute").disabled,true);
    assert.equal(view.get("armState").textContent,"");
  });
  await check("typed_phrase_required_and_model_fields_exact", async () => {
    const view = board(); await view.authenticate(); await view.refresh(); view.order();
    await view.fire("armExecute");
    assert.equal(view.calls.length,1);
    assert.match(view.get("resultMessage").textContent,/兩項/);
    for (const bad of ["1e2","1x2","0","0.0","01","-1","2","1.000000000000000000001"]) {
      view.get("contracts").value = bad; view.arm(); await view.fire("armExecute");
      assert.equal(view.calls.length,1);
    }
    view.order();
    view.get("clearPhrase").value = config.phrases.clear;
    view.queue.push(response({invocation_id:"fixture",action:"clear_stop",status:"operator_command_recorded_disarmed",execution_complete:false}));
    await view.fire("clearStop");
    assert.deepEqual(view.calls[1],{path:"/api/demo-automation/native/clear-stop",method:"POST",body:{confirmation:config.phrases.clear}});
    assert.equal(view.get("clearPhrase").value,"");
    await view.refresh();
    view.get("counterPhrase").value = config.phrases.counter;
    view.queue.push(response({invocation_id:"fixture",action:"initialize_policy_counter",status:"operator_command_recorded_disarmed",execution_complete:false}));
    await view.fire("initializeCounter");
    assert.deepEqual(view.calls[3].body,{confirmation:config.phrases.counter});
    await view.refresh();
    view.get("leveragePhrase").value = config.phrases.leverage;
    view.queue.push(response({invocation_id:"fixture",action:"configure_leverage",classification:"no_change",configuration_outcome_id:"a".repeat(64),receipt_sha256:"b".repeat(64),execution_authority:false,order_retry_authority:false}));
    await view.fire("configureLeverage");
    assert.deepEqual(view.calls[5].body,{instrument_id:config.symbols[0],strategy:config.strategies[0],contracts:"1",leverage_ceiling:3,confirmation:config.phrases.leverage});
    assert.equal(view.get("leveragePhrase").value,"");
  });
  await check("long_invocation_keeps_estop_live_and_never_resubmits", async () => {
    const view = board(); await view.authenticate(); await view.refresh(); view.order(); view.arm();
    let finish;
    view.queue.push(() => new Promise(resolve => { finish = resolve; }));
    const running = view.fire("armExecute");
    await Promise.resolve(); await Promise.resolve();
    assert.equal(view.calls.length,2);
    assert.deepEqual(view.calls[1].body,{instrument_id:config.symbols[0],strategy:config.strategies[0],contracts:"1",leverage:3,arm_confirmation:config.phrases.arm,execute_confirmation:config.phrases.execute});
    assert.equal(view.get("armExecute").disabled,true);
    assert.equal(view.get("emergencyStop").disabled,false);
    assert.equal(view.get("stop").disabled,false);
    assert.equal(view.get("armPhrase").value,"");
    assert.equal(view.get("executePhrase").value,"");
    await view.fire("armExecute");
    assert.equal(view.calls.length,2);
    view.get("emergencyPhrase").value = config.phrases.emergency;
    view.queue.push(response({...cleanStatus,emergency_stop:true,locked:true}));
    await view.fire("emergencyStop");
    assert.equal(view.calls[2].path,"/api/demo-automation/emergency-stop");
    assert.deepEqual(view.calls[2].body,{confirmation:config.phrases.emergency});
    finish(response({detail:"native_demo_action_failed_or_unknown"},409));
    await running;
    assert.equal(view.get("uncertain").hidden,false);
    for (const id of ["armExecute","clearStop","initializeCounter","configureLeverage"]) assert.equal(view.get(id).disabled,true);
    await view.refresh();
    assert.equal(view.get("armExecute").disabled,true);
    view.get("reservation").value = "c".repeat(64);
    await view.fire("reservation","input");
    assert.equal(view.get("reconcile").disabled,false);
    view.queue.push(response({invocation_id:"fixture",status:"reconciliation_data",reservation_id:"c".repeat(64),outcome_id:"d".repeat(64),receipt_sha256:"e".repeat(64),risk_hold_retained:false,execution_authority:false,order_retry_authority:false,execution_complete:false}));
    await view.fire("reconcile");
    assert.equal(view.get("uncertain").hidden,false);
    assert.equal(view.get("armExecute").disabled,true);
    assert.equal(view.calls.filter(call => call.path.endsWith("/arm-execute")).length,1);
  });
  await check("successful_terminal_response_requires_new_manual_status", async () => {
    const view = board(); await view.authenticate(); await view.refresh(); view.order(); view.arm();
    view.queue.push(response({invocation_id:"fixture",action:"arm_execute",status:"zero_fill_flat_settled",classification:"terminal_zero_fill_flat_settled",closure_outcome_id:"a".repeat(64),cleanup_outcome_id:"b".repeat(64),execution_complete:true,risk_hold_retained:false}));
    await view.fire("armExecute");
    assert.equal(view.calls.length,2);
    assert.equal(view.get("uncertain").hidden,true);
    assert.equal(view.get("armExecute").disabled,true);
    assert.match(view.get("resultMessage").textContent,/重新讀取/);
  });
  await check("malformed_200_and_transport_loss_latch_exposure", async () => {
    for (const reply of [response({passed:true}),response({},200,"text/html"),new Error("fixture_network_loss")]) {
      const view = board(); await view.authenticate(); await view.refresh(); view.order(); view.arm();
      view.queue.push(reply);
      await view.fire("armExecute");
      assert.equal(view.get("uncertain").hidden,false);
      view.arm(); await view.fire("armExecute");
      assert.equal(view.calls.length,2);
      assert.equal(view.get("emergencyStop").disabled,false);
    }
  });
  await check("unknown_status_and_reflected_token_stay_safe", async () => {
    const view = board(); await view.authenticate();
    await view.refresh({armed:false});
    assert.equal(view.get("armExecute").disabled,true);
    assert.equal(view.get("armState").textContent,"未知");
    await view.refresh({...cleanStatus,note:"<img src=x onerror=alert(1)>" + token,api_key:"must_not_display"});
    const rendered = view.get("statusDetail").textContent;
    assert.ok(!rendered.includes(token));
    assert.ok(!rendered.includes("must_not_display"));
    assert.ok(rendered.includes("<img"));
    assert.equal(view.get("armExecute").disabled,false);
    let finish;
    view.queue.push(() => new Promise(resolve => { finish = resolve; }));
    const pending = view.fire("refresh");
    await Promise.resolve(); await Promise.resolve();
    await view.fire("forget");
    finish(response({...cleanStatus,note:token}));
    await pending;
    assert.ok(!view.get("statusDetail").textContent.includes(token));
    assert.equal(view.get("token").value,"");
    assert.equal(view.get("armExecute").disabled,true);
  });
  const report = {schema:"ctcc.native_manual_demo.dashboard_dom.v1",status:"PASS",node_version:process.version,synthetic_dom_only:true,html_sha256:hash(html),script_sha256:hash(script),style_sha256:hash(style),scenarios:results,fixture_requests:totalRequests,external_requests:0,order_writes:0};
  fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+"\n",{flag:"wx"});
  console.log(JSON.stringify({status:report.status,scenarios:results.length,external_requests:0}));
} catch (error) {
  fs.writeFileSync(reportPath,JSON.stringify({schema:"ctcc.native_manual_demo.dashboard_dom.v1",status:"FAIL",node_version:process.version,synthetic_dom_only:true,html_sha256:hash(html),scenarios:results,error:String(error.message)},null,2)+"\n",{flag:"wx"});
  throw error;
}

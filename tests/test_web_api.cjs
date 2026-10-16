const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/api.js"), "utf8");
const origin = "http://127.0.0.1:8000";

function client(fetch) {
  const context = vm.createContext({ fetch, URL, Headers, location: { origin } });
  vm.runInContext(source, context);
  return context.zeusAPI;
}

test("concurrent requests share one bootstrap and send session in header", async () => {
  const calls = [];
  const api = client(async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(url.endsWith("/api/session")
      ? { session_token: "a".repeat(43) } : { ok: true }));
  });
  await Promise.all([api.fetch("/api/status"), api.fetch("/api/history")]);
  assert.equal(calls.filter(x => x.url.endsWith("/api/session")).length, 1);
  for (const call of calls.filter(x => !x.url.endsWith("/api/session"))) {
    assert.equal(call.options.headers.get("X-Zeus-Session"), "a".repeat(43));
    assert.equal(call.options.redirect, "error");
    assert.equal(call.options.cache, "no-store");
    assert.ok(!call.url.includes("a".repeat(43)));
  }
});

test("POST body and JSON headers survive session injection", async () => {
  let sent;
  const api = client(async (url, options) => {
    if (url.endsWith("/api/session")) return Response.json({ session_token: "b".repeat(43) });
    sent = options;
    return Response.json({ ok: true });
  });
  await api.fetch("/api/transactions/consent", {
    method: "POST", body: '{"transaction_id":"test"}',
    headers: { "Content-Type": "application/json" },
  });
  assert.equal(sent.method, "POST");
  assert.equal(sent.body, '{"transaction_id":"test"}');
  assert.equal(sent.headers.get("Content-Type"), "application/json");
});

test("foreign destinations are rejected before fetching or revealing token", async () => {
  let calls = 0;
  const api = client(async () => { calls++; });
  await assert.rejects(api.fetch("https://outside.example/api/config"));
  await assert.rejects(api.fetch("/outside"));
  assert.equal(calls, 0);
});

test("failed bootstrap can recover on a later request", async () => {
  let bootstraps = 0;
  const api = client(async url => {
    if (url.endsWith("/api/session")) {
      bootstraps++;
      if (bootstraps === 1) return new Response("offline", { status: 503 });
      return Response.json({ session_token: "c".repeat(43) });
    }
    return Response.json({ ok: true });
  });
  await assert.rejects(api.fetch("/api/status"));
  assert.equal((await api.fetch("/api/status")).status, 200);
  assert.equal(bootstraps, 2);
});

test("403 invalidates session without replaying a mutation", async () => {
  let bootstraps = 0;
  let mutations = 0;
  const api = client(async (url, options) => {
    if (url.endsWith("/api/session")) {
      bootstraps++;
      return Response.json({ session_token: String(bootstraps).repeat(43) });
    }
    if (options.method === "POST") { mutations++; return new Response("", { status: 403 }); }
    return Response.json({ ok: true });
  });
  assert.equal((await api.fetch("/api/transactions/execute", { method: "POST" })).status, 403);
  assert.equal(mutations, 1);
  await api.fetch("/api/status");
  assert.equal(bootstraps, 2);
});

test("report remains a downloadable response with authenticated fetch", async () => {
  const api = client(async url => url.endsWith("/api/session")
    ? Response.json({ session_token: "d".repeat(43) })
    : new Response("<html>report</html>", { headers: { "Content-Type": "text/html" } }));
  const report = await api.fetch("/api/report?scan_id=7");
  assert.equal(await (await report.blob()).text(), "<html>report</html>");
});

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const script = readFileSync(join(__dirname, "..", "agentic_itops", "review", "static", "review.js"), "utf8");

function page() {
  const requests = [];
  function element(className, fields = {}, dataset = {}) {
    const listeners = {};
    const button = { disabled: false };
    return {
      fields, dataset, listeners, button,
      classList: { contains: (name) => name === className },
      addEventListener(name, callback) { (listeners[name] ??= []).push(callback); },
      querySelector() { return button; },
    };
  }
  const source = { producer_key: "producer", run_id: "run", domain: "upstream" };
  const forms = {
    ".advance-form": element("advance-form", { id: "job/1" }),
    ".projection-recovery-form": element("projection-recovery-form", { id: "workflow/1" }),
    ".replacement-form": element("replacement-form", { id: "failed/1", root_job_id: "root/1" }),
    ".publication-advance-form": element("publication-advance-form", { id: "publication/1" }),
    ".investigation-form": element("investigation-form", {
      source: JSON.stringify(source), start_utc: "2026-09-14T23:03:30",
      end_utc: "2026-09-15T01:03:30Z", surface: "upstream",
      entity_id: "asset-1", location_id: "location-1", reason: "Operator investigation",
    }),
    ".lifecycle-form": element("lifecycle-form", { kind: "postmortem" }, { incident: "incident/1" }),
    ".work-form": element("work-form", {
      source: JSON.stringify(source), start_utc: "2026-09-14T23:03:30Z",
      end_utc: "2026-09-15T01:03:30Z",
    }, { route: "/analysis-jobs" }),
  };
  const nodes = Object.fromEntries(["theme", "inbox-filter", "request-status", "request-result"]
    .map((name) => [name, element(name)]));
  let nextId = 0;
  let responseOk = true;
  vm.runInNewContext(script, {
    document: {
      getElementById: (id) => nodes[id] ?? null,
      documentElement: { getAttribute: () => "light" },
      querySelector: () => ({ content: "csrf-test" }),
      querySelectorAll: (selector) => forms[selector] ? [forms[selector]] : [],
    },
    crypto: { randomUUID: () => `request-${++nextId}` },
    FormData: class { constructor(form) { this.form = form; } get(key) { return this.form.fields[key]; } },
    fetch: async (route, options) => {
      requests.push({ route, ...options });
      return { ok: responseOk, status: responseOk ? 200 : 503,
        headers: { get: () => null }, text: async () => '{"status":"queued"}' };
    },
  });
  async function submit(selector) {
    const form = forms[selector];
    assert.equal(form.listeners.submit?.length, 1, "exactly one handler must exist from page load");
    let prevented = 0;
    form.listeners.submit[0]({ preventDefault() { prevented++; } });
    await new Promise(setImmediate);
    assert.equal(prevented, 1);
    assert.equal(form.button.disabled, false);
  }
  return { forms, requests, submit, failRequests: () => { responseOk = false; } };
}

test("new actions work on initial load and reload without accumulating handlers", async () => {
  for (let load = 0; load < 2; load++) {
    const app = page();
    const cases = [
      [".advance-form", "/jobs/job%2F1/advance"],
      [".projection-recovery-form", "/jobs/workflow%2F1/projection-jobs"],
      [".replacement-form", "/jobs/failed%2F1/replacement-jobs?root_job_id=root%2F1"],
      [".publication-advance-form", "/publications/publication%2F1/advance"],
      [".investigation-form", "/investigation-jobs"],
      [".lifecycle-form", "/incidents/incident%2F1/lifecycle-jobs"],
    ];
    for (let round = 0; round < 3; round++) {
      for (const [selector, route] of cases) {
        const count = app.requests.length;
        await app.submit(selector);
        assert.equal(app.requests.length, count + 1);
        const request = app.requests.at(-1);
        assert.equal(request.route, route);
        assert.equal(request.method, "POST");
        assert.equal(request.credentials, "same-origin");
        assert.equal(request.headers["X-CSRF-Token"], "csrf-test");
        assert.ok(request.headers["Idempotency-Key"]);
      }
      await app.submit(".work-form");
    }
    const investigation = app.requests.find((request) => request.route === "/investigation-jobs");
    assert.equal(JSON.parse(investigation.body).window.start_utc, "2026-09-14T23:03:30Z");
    const lifecycle = app.requests.find((request) => request.route.includes("/lifecycle-jobs"));
    assert.deepEqual(JSON.parse(lifecycle.body), { kind: "postmortem" });
  }
});

test("recovery and replacement retain submission keys and send no result or approval content", async () => {
  for (const selector of [".projection-recovery-form", ".replacement-form"]) {
    const app = page();
    await app.submit(selector);
    await app.submit(selector);
    assert.equal(app.requests[0].headers["Idempotency-Key"], app.requests[1].headers["Idempotency-Key"]);
    assert.deepEqual(JSON.parse(app.requests[0].body), {});
    app.forms[selector].fields.id = "workflow/2";
    await app.submit(selector);
    assert.notEqual(app.requests[1].headers["Idempotency-Key"], app.requests[2].headers["Idempotency-Key"]);
  }
});

test("advance rotates successful request keys but preserves failed-request keys", async () => {
  for (const selector of [".advance-form", ".publication-advance-form"]) {
    const app = page();
    await app.submit(selector);
    await app.submit(selector);
    assert.notEqual(app.requests[0].headers["Idempotency-Key"], app.requests[1].headers["Idempotency-Key"]);
    app.failRequests();
    await app.submit(selector);
    await app.submit(selector);
    assert.equal(app.requests[2].headers["Idempotency-Key"], app.requests[3].headers["Idempotency-Key"]);
  }
});

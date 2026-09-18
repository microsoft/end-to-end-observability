"use strict";

const theme = document.getElementById("theme");
theme.value = document.documentElement.getAttribute("data-theme");
theme.addEventListener("change", () => {
  document.documentElement.setAttribute("data-theme", theme.value);
  const url = new URL(window.location.href);
  url.searchParams.set("scoutTheme", theme.value);
  window.history.replaceState(null, "", url);
});

document.getElementById("inbox-filter").addEventListener("input", (event) => {
  const query = event.target.value.toLocaleLowerCase();
  for (const row of document.querySelectorAll("#incident-rows tr")) {
    row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
  }
});

const statusNode = document.getElementById("request-status");
const resultNode = document.getElementById("request-result");
const csrf = document.querySelector('meta[name="csrf-token"]').content;

function utcValue(value) {
  // Preserve fractional source-window precision instead of truncating through Date.
  return value.endsWith("Z") ? value : value + "Z";
}

async function callApi(route, options = {}) {
  statusNode.textContent = "Request pending; no completion is implied.";
  resultNode.textContent = "";
  try {
    const response = await fetch(route, { credentials: "same-origin", ...options });
    const text = await response.text();
    let result;
    try { result = JSON.parse(text); } catch { result = { error: "Unexpected response; no result confirmed." }; }
    statusNode.textContent = response.ok
      ? "Receipt received. Review the persisted state below; no operational action was performed."
      : `Request failed (${response.status}). No success is implied.`;
    if (response.headers.get("ETag")) result.etag = response.headers.get("ETag");
    resultNode.textContent = JSON.stringify(result, null, 2);
    return response.ok;
  } catch {
    statusNode.textContent = "Connection failed; outcome unknown. Retrying unchanged input reuses its idempotency key.";
    return false;
  }
}

async function write(form, route, payload, etag) {
  const signature = JSON.stringify({ route, payload, etag });
  // Keep the same key after an unknown/failed response, but never reuse it for an edit.
  if (form.dataset.signature !== signature) {
    form.dataset.signature = signature;
    form.dataset.idempotencyKey = crypto.randomUUID();
  }
  const headers = { "Content-Type": "application/json", "X-CSRF-Token": csrf,
    "Idempotency-Key": form.dataset.idempotencyKey };
  if (etag) headers["If-Match"] = etag;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const ok = await callApi(route, { method: "POST", headers, body: JSON.stringify(payload) });
    if (ok && (form.classList.contains("advance-form") || form.classList.contains("publication-advance-form"))) {
      delete form.dataset.signature;
    }
  } finally {
    button.disabled = false;
  }
}

function jobContext(form, route) {
  const root = new FormData(form).get("root_job_id");
  return root ? `${route}?root_job_id=${encodeURIComponent(root)}` : route;
}

for (const form of document.querySelectorAll(".advance-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    write(form, jobContext(form, `/jobs/${encodeURIComponent(new FormData(form).get("id"))}/advance`), {});
  });
}
for (const form of document.querySelectorAll(".publication-advance-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    write(form, `/publications/${encodeURIComponent(new FormData(form).get("id"))}/advance`, {});
  });
}
for (const form of document.querySelectorAll(".projection-recovery-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    write(form, jobContext(form, `/jobs/${encodeURIComponent(new FormData(form).get("id"))}/projection-jobs`), {});
  });
}
for (const form of document.querySelectorAll(".replacement-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    write(form, jobContext(form, `/jobs/${encodeURIComponent(new FormData(form).get("id"))}/replacement-jobs`), {});
  });
}
for (const form of document.querySelectorAll(".investigation-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    write(form, "/investigation-jobs", {
      source: JSON.parse(data.get("source")),
      window: { start_utc: utcValue(data.get("start_utc")), end_utc: utcValue(data.get("end_utc")) },
      target: { surface: data.get("surface"), entity_id: data.get("entity_id"), location_id: data.get("location_id") },
      reason: data.get("reason"),
    });
  });
}
for (const form of document.querySelectorAll(".lifecycle-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    write(form, `/incidents/${encodeURIComponent(form.dataset.incident)}/lifecycle-jobs`,
      { kind: new FormData(form).get("kind") });
  });
}

for (const form of document.querySelectorAll(".work-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    write(form, form.dataset.route, {
      source: JSON.parse(data.get("source")),
      window: { start_utc: utcValue(data.get("start_utc")), end_utc: utcValue(data.get("end_utc")) },
    });
  });
}
for (const form of document.querySelectorAll(".review-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    write(form, form.dataset.route, { action: data.get("action"), reason: data.get("reason") }, form.dataset.etag);
  });
}
for (const form of document.querySelectorAll(".publication-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    write(form, "/publications", {
      incident_id: form.dataset.incident, analysis_revision: Number(form.dataset.revision),
      draft_sha256: form.dataset.draft, reason: data.get("reason"),
      expires_at_utc: utcValue(data.get("expires_at_utc")),
    }, form.dataset.etag);
  });
}
for (const form of document.querySelectorAll(".lookup-form")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const id = new FormData(form).get("id");
    callApi(jobContext(form, `/${form.dataset.kind}/${encodeURIComponent(id)}`));
  });
}
const revoke = document.getElementById("revoke-form");
if (revoke) revoke.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(revoke);
  write(revoke, `/publications/${encodeURIComponent(data.get("id"))}/revoke`,
    { reason: data.get("reason") }, data.get("etag"));
});

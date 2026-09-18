(() => {
  "use strict";
  const panel = document.querySelector("#v2-controls");
  if (!panel) return;
  document.body.prepend(panel);
  const surface = panel.dataset.surface;
  const endpoint = surface ? `/api/v2/surfaces/${encodeURIComponent(surface)}` : null;
  const state = panel.querySelector("#v2-state");
  const error = panel.querySelector("#v2-error");
  const scenarios = panel.querySelector("#v2-scenario");
  const buttons = [...panel.querySelectorAll("button[data-v2-action]")];
  const choices = [...panel.querySelectorAll('[name="v2-surface"]')];
  const pendingCommands = new Map();
  let csrf, busy = false, timer, closed = false, selectionInitialized = false;
  let latest, currentSurface, lastView = null;
  const logDetails = panel.querySelector("#v2-log-details");
  let logBusy = false;

  async function request(url, options = {}) {
    const response = await fetch(url, {cache: "no-store", credentials: "same-origin", ...options});
    const body = await response.json();
    if (!response.ok) {
      const failure = new Error(body.message || `Request failed (${response.status})`);
      failure.status = response.status;
      throw failure;
    }
    return body;
  }
  function minutes(seconds) {
    return `${Math.ceil(Math.max(0, seconds) / 60)} min`;
  }
  async function refreshLogDetails() {
    if (!endpoint || logBusy) return;
    logBusy = true;
    const refreshButton = panel.querySelector("#v2-log-refresh");
    const message = panel.querySelector("#v2-log-message");
    refreshButton.disabled = true;
    message.textContent = "Loading selected observations...";
    try {
      const detail = await request(`${endpoint}/observations`);
      message.textContent = `${detail.record_count} records | ${detail.view_state} | ${detail.message}`;
      panel.querySelector("#v2-log-records").textContent = JSON.stringify(detail.records, null, 2);
    } catch (failure) {
      message.textContent = `Could not load log details: ${failure.message}`;
      panel.querySelector("#v2-log-records").textContent = "";
    } finally {
      refreshButton.disabled = false;
      logBusy = false;
    }
  }
  if (logDetails) {
    logDetails.addEventListener("toggle", () => {
      if (logDetails.open) refreshLogDetails();
    });
    panel.querySelector("#v2-log-refresh").addEventListener("click", refreshLogDetails);
  }
  function enableControls() {
    for (const button of buttons) {
      const action = button.dataset.v2Action;
      button.disabled = busy || !latest ||
        (action === "prepare" && (latest.running || latest.prepare_blocked)) ||
        (action === "stop" && !latest.running) ||
        (action === "inject" && (!latest.running || !latest.ready || !currentSurface?.selected)) ||
        (action === "recover" && (!latest.running || !currentSurface?.selected));
    }
    choices.forEach(choice => { choice.disabled = busy || Boolean(latest?.running); });
    panel.querySelector("#v2-duration").disabled = busy || Boolean(latest?.running);
  }
  async function refresh() {
    const [session, detail] = await Promise.all([
      request("/api/v2/demo"), endpoint ? request(endpoint) : Promise.resolve(null),
    ]);
    latest = session;
    currentSurface = detail;
    if ((!selectionInitialized || session.running) && session.selected_surfaces.length) {
      choices.forEach(choice => { choice.checked = session.selected_surfaces.includes(choice.value); });
    }
    selectionInitialized = true;
    if (scenarios && !scenarios.options.length) {
      for (const scenario of detail.scenarios) {
        const option = document.createElement("option");
        option.value = scenario.id;
        option.textContent = scenario.label;
        option.title = scenario.description;
        scenarios.append(option);
      }
    }
    state.textContent = `${session.phase.toUpperCase()} | ${session.running ? `Stops in ${minutes(session.remaining_seconds)}` : "Generation stopped"} | ${session.selected_surfaces.length} participating portals`;
    const local = session.delivery_mode !== "azure";
    const readiness = session.readiness || {};
    const baseline = session.ready
      ? (local ? "Local baseline ready; Azure queryability is not verified." : "Normal baseline is queryable in Log Analytics.")
      : (session.running
        ? `Preparing normal baseline. Warm-up remaining: ${minutes(session.warmup_remaining_seconds)}. ${local ? "Local capture only." : "Waiting for queryable observations from every selected portal."}`
        : "Prepare a session to begin normal baseline logging.");
    panel.querySelector("#v2-readiness").textContent = baseline;
    const evidence = Object.entries(readiness.surfaces || {}).map(([name, item]) =>
      `${item.surface || name}: ${item.distinct_observed_at ?? "?"} baseline frames, ${item.ready ? "complete" : "incomplete"}`);
    panel.querySelector("#v2-evidence").textContent = evidence.length
      ? evidence.join(" | ") : "No baseline query evidence yet.";
    panel.querySelector("#v2-delivery").textContent =
      `${local ? "Local capture accepted (not Azure)" : "Azure ingestion accepted"}: ${session.accepted_records} records | Pending: ${session.pending_records} | Parked batches: ${session.parked_batches}`;
    if (detail) {
      const intervention = panel.querySelector("#v2-intervention");
      intervention.textContent = !detail.selected ? "This portal is not participating in the current session." :
        detail.active_anomaly
          ? `Active anomaly: ${detail.active_anomaly}. Recovery deadline: ${detail.anomaly_until}. Last queued frame: ${detail.steps}.`
          : `Normal replay. Last queued frame: ${detail.steps}.`;
      const view = `${session.session_id}:${detail.steps}`;
      if (lastView !== null && lastView !== view && !busy) {
        location.reload();
        return;
      }
      if (lastView === null) lastView = view;
    }
    error.textContent = [session.last_error, session.worker_error, readiness.error, detail?.last_error]
      .filter(Boolean).join(" | ");
    enableControls();
  }
  async function poll() {
    if (closed) return;
    try { await refresh(); }
    catch (failure) { error.textContent = failure.message; }
    finally { if (!closed) timer = setTimeout(poll, 5000); }
  }
  for (const button of buttons) button.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    enableControls();
    error.textContent = "";
    const action = button.dataset.v2Action;
    const intervention = action === "inject" || action === "recover";
    const url = intervention ? `${endpoint}/commands` : "/api/v2/demo/commands";
    const body = {action};
    if (action === "prepare") {
      body.surfaces = choices.filter(choice => choice.checked).map(choice => choice.value);
      body.duration_seconds = Number(panel.querySelector("#v2-duration").value) * 60;
    } else if (action === "inject") {
      body.scenario = scenarios.value;
      body.anomaly_duration_seconds = Number(panel.querySelector("#v2-anomaly-duration").value) * 60;
    }
    const fingerprint = url + JSON.stringify(body);
    if (!pendingCommands.has(fingerprint)) pendingCommands.set(fingerprint, crypto.randomUUID());
    try {
      if (!csrf) csrf = (await request("/api/v2/session")).csrf_token;
      await request(url, {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf,
          "Idempotency-Key": pendingCommands.get(fingerprint)},
        body: JSON.stringify(body),
      });
      pendingCommands.delete(fingerprint);
      if (action === "prepare") {
        location.reload();
        return;
      }
      await refresh();
    } catch (failure) {
      if (failure.status) pendingCommands.delete(fingerprint);
      if (failure.status === 403) csrf = undefined;
      error.textContent = failure.message;
    } finally {
      busy = false;
      enableControls();
    }
  });
  window.addEventListener("pagehide", () => { closed = true; clearTimeout(timer); });
  window.addEventListener("pageshow", event => { if (event.persisted) { closed = false; poll(); } });
  poll();
})();

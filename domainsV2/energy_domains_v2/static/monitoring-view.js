/* V2-only monitoring view; legacy controls have no V2 write routes. */
(() => {
  const hide = () => {
    for (const id of ["live-scenario", "live-apply", "live-start", "run-scenario",
      "run-start", "scenario", "start", "scenario-description", "control-message"]) {
      const element = document.getElementById(id);
      if (element) element.hidden = true;
    }
    for (const element of document.querySelectorAll(".control, .status")) {
      if (element.querySelector("#start, #job-alerts")) element.hidden = true;
    }
    for (const element of document.querySelectorAll(".storm-command, .alert-card")) {
      element.hidden = true;
    }
    for (const element of document.querySelectorAll("p")) {
      if (/raise Azure Monitor alerts|trace every abnormal instance into Azure Monitor|Set the midstream control flag|Start an on-demand run|Only the sparse monitoring evidence layer is published/i.test(element.textContent)) {
        element.textContent = "V2 uses the shared controls and durable observation delivery. No Azure Monitor alerts are created.";
      }
    }
    // Hide obsolete Azure alert/control claims, keeping the domain visuals.
    for (const element of document.querySelectorAll("button")) {
      if (/activate storm|clear storm|start simulation|run scenario/i.test(element.textContent)) {
        element.hidden = true;
      }
    }
  };
  let queued = false;
  const observer = new MutationObserver(() => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(() => { queued = false; hide(); });
    }
  });
  hide();
  observer.observe(document.body, { childList: true, subtree: true });
})();

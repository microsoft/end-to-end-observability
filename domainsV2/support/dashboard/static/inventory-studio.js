(function (root) {
  "use strict";

  const speeds = [500, 1000, 2000, 4000];
  const stockKey = row => JSON.stringify([row.part_master_id, row.location_id]);
  const quantityStatus = (quantity, known = true) => {
    if (!known || !Number.isSafeInteger(quantity) || quantity < 0) return "unknown";
    return quantity === 0 ? "out_of_stock" : quantity < 10 ? "low" : "healthy";
  };
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const text = value => typeof value === "string" && value.trim().length > 0;
  function requireValue(condition, message) {
    if (!condition) throw new Error(`Invalid inventory movement: ${message}`);
  }
  function validate(payload) {
    requireValue(object(payload) && object(payload.inventory) && object(payload.movement), "missing payload");
    const {inventory, movement} = payload;
    requireValue(["depletion_restock_v1", "replenishment_depletion_v1"].includes(movement.scenario_id),
      "unsupported scenario");
    const replenishFirst = movement.scenario_id === "replenishment_depletion_v1";
    requireValue(typeof inventory.stock_is_known === "boolean", "unknown stock visibility");
    for (const field of ["parts", "locations", "stock"]) {
      requireValue(Array.isArray(inventory[field]), `missing ${field}`);
    }
    requireValue(movement.threshold === 10 && movement.azure_connected === false
      && movement.is_synthetic === true && movement.operational_use === "prohibited", "unsupported policy");
    requireValue(typeof movement.available === "boolean" && Array.isArray(movement.focus)
      && Array.isArray(movement.frames), "missing scenario");
    requireValue(movement.frames.length === (movement.available ? 6 : 1), "incorrect frame count");
    requireValue(!movement.available || inventory.stock_is_known, "unknown stock cannot play");
    requireValue(movement.available || text(movement.unavailable_reason), "missing unavailable reason");
    const parts = new Set();
    const locations = new Set();
    for (const part of inventory.parts) {
      requireValue(object(part) && text(part.part_master_id) && text(part.uom)
        && !parts.has(part.part_master_id), "invalid part identity");
      parts.add(part.part_master_id);
    }
    for (const location of inventory.locations) {
      requireValue(object(location) && text(location.location_id)
        && !locations.has(location.location_id), "invalid location identity");
      locations.add(location.location_id);
    }
    function positions(rows) {
      const result = new Map();
      for (const row of rows) {
        requireValue(object(row) && parts.has(row.part_master_id) && locations.has(row.location_id)
          && quantityStatus(row.quantity) !== "unknown", "invalid stock position");
        const key = stockKey(row);
        requireValue(!result.has(key), "duplicate stock position");
        result.set(key, row.quantity);
      }
      return result;
    }
    const opening = positions(inventory.stock);
    const focus = new Set();
    for (const row of movement.focus) {
      requireValue(object(row) && opening.has(stockKey(row)) && !focus.has(stockKey(row))
        && (replenishFirst || opening.get(stockKey(row)) >= 12), "invalid focus position");
      focus.add(stockKey(row));
    }
    requireValue(movement.available ? focus.size >= 1 && focus.size <= 3 : focus.size === 0, "invalid focus count");
    let previous = opening;
    for (const [index, frame] of movement.frames.entries()) {
      requireValue(object(frame) && frame.index === index && frame.elapsed_minutes === index * 5
        && text(frame.label), "invalid frame identity");
      for (const field of ["stock", "movements", "alerts", "active_alerts"]) {
        requireValue(Array.isArray(frame[field]), `missing frame ${field}`);
      }
      const current = positions(frame.stock);
      requireValue(current.size === opening.size, "changed stock positions");
      for (const [key, quantity] of opening) {
        const expected = focus.has(key)
          ? [quantity, 12, 10, 9, 0, replenishFirst ? 12 : quantity][index] : quantity;
        requireValue(current.get(key) === expected, "unexpected stock quantity");
      }
      const changed = new Set();
      for (const row of frame.movements) {
        requireValue(object(row) && text(row.movement_id), "invalid movement");
        const key = stockKey(row);
        requireValue(current.has(key) && !changed.has(key) && Number.isSafeInteger(row.delta)
          && row.delta !== 0 && row.quantity_before === previous.get(key)
          && row.quantity_after === current.get(key)
          && row.quantity_before + row.delta === row.quantity_after, "unreconciled movement");
        changed.add(key);
      }
      for (const [key, quantity] of current) {
        requireValue(changed.has(key) === (quantity !== previous.get(key)), "missing movement");
      }
      for (const field of ["alerts", "active_alerts"]) {
        const identities = new Set();
        for (const alert of frame[field]) {
          requireValue(object(alert) && text(alert.alert_id) && !identities.has(alert.alert_id)
            && current.has(stockKey(alert)) && alert.quantity === current.get(stockKey(alert))
            && alert.threshold === 10 && alert.frame_index === index
            && ["fired", "resolved"].includes(alert.state)
            && (alert.state === "fired" ? alert.quantity < 10 : alert.quantity >= 10)
            && (field !== "active_alerts" || alert.state === "fired"), "invalid alert");
          identities.add(alert.alert_id);
        }
      }
      requireValue(inventory.stock_is_known || (!frame.alerts.length && !frame.active_alerts.length), "alerts on unknown stock");
      previous = current;
    }
    return payload;
  }
  function freeze(value) {
    if (value && typeof value === "object") {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
    return value;
  }
  function create(payload) {
    validate(payload);
    const data = freeze(JSON.parse(JSON.stringify(payload)));
    const listeners = new Set();
    let index = 0;
    let timer = null;
    let speed = 1000;
    let disposed = false;
    function snapshot() {
      return {
        inventory: data.inventory,
        movement: data.movement,
        frame: data.movement.frames[index],
        index,
        playing: timer !== null,
        speed,
        history: {
          movements: data.movement.frames.slice(0, index + 1).flatMap(frame => frame.movements),
          alerts: data.movement.frames.slice(0, index + 1).flatMap(frame => frame.alerts),
        },
      };
    }
    function emit() { for (const listener of listeners) listener(snapshot()); }
    function stop() {
      if (timer !== null) root.clearInterval(timer);
      timer = null;
    }
    function advance() {
      index = Math.min(index + 1, data.movement.frames.length - 1);
      if (index === data.movement.frames.length - 1) stop();
      emit();
    }
    const controller = {
      snapshot,
      subscribe(listener) {
        requireValue(typeof listener === "function" && !disposed, "invalid subscriber");
        listeners.add(listener);
        listener(snapshot());
        return () => listeners.delete(listener);
      },
      play() {
        if (disposed || timer !== null || !data.movement.available
          || index === data.movement.frames.length - 1) return false;
        timer = root.setInterval(advance, speed);
        emit();
        return true;
      },
      pause() { stop(); emit(); },
      step() { stop(); if (!disposed) advance(); },
      reset() { stop(); index = 0; emit(); },
      setSpeed(milliseconds) {
        requireValue(speeds.includes(milliseconds), "unsupported playback speed");
        speed = milliseconds;
        if (timer !== null) {
          stop();
          timer = root.setInterval(advance, speed);
        }
        emit();
      },
      dispose() { stop(); listeners.clear(); disposed = true; },
    };
    return controller;
  }
  const api = {create, validate, stockKey, quantityStatus};
  if (typeof module === "object" && module.exports) module.exports = api;
  root.InventoryStudio = api;
})(globalThis);

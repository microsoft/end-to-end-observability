(function (root) {
  "use strict";
  const stockKey = row => JSON.stringify([row.part_master_id, row.location_id]);
  const quantityStatus = (quantity, known = true) => {
    if (!known || !Number.isSafeInteger(quantity) || quantity < 0) return "unknown";
    return quantity === 0 ? "out_of_stock" : quantity < 10 ? "low" : "healthy";
  };
  function validate(payload) {
    if (!payload || !payload.inventory || !payload.movement ||
        !Array.isArray(payload.inventory.stock) || payload.movement.available !== false ||
        payload.movement.frames.length !== 1) throw new Error("Invalid V2 current stock");
    for (const row of payload.inventory.stock) {
      if (row.quantity === null ? row.quality !== "unavailable" :
          quantityStatus(row.quantity) === "unknown" || row.quality !== "good") {
        throw new Error("Invalid V2 quantity quality");
      }
    }
    return payload;
  }
  function create(payload) {
    const data = JSON.parse(JSON.stringify(validate(payload)));
    const snapshot = () => JSON.parse(JSON.stringify({
      inventory: data.inventory, movement: data.movement, frame: data.movement.frames[0],
      index: 0, playing: false, speed: 1000, history: {movements: [], alerts: []}
    }));
    return {
      snapshot, subscribe(listener) { listener(snapshot()); return () => {}; },
      play() { return false; }, pause() {}, step() {}, reset() {}, setSpeed() {}, dispose() {}
    };
  }
  root.InventoryStudio = {stockKey, quantityStatus, validate, create};
  if (typeof module !== "undefined") module.exports = root.InventoryStudio;
})(typeof window === "undefined" ? globalThis : window);

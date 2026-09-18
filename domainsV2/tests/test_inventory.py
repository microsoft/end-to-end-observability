import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from flask import Flask

from energy_domains_v2 import inventory
from energy_domains_v2.contracts import INVENTORY_SURFACES


class InventoryTests(unittest.TestCase):
    def test_opening_domain_artifacts_and_copy_on_write(self):
        for surface in INVENTORY_SURFACES:
            with self.subTest(surface=surface):
                root = inventory.dataset_root(surface)
                if not root.is_dir():
                    self.fail(f"Package required explicit dataset or set inventory root: {root}")
                files = list(root.glob("*.jsonl"))
                before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
                model = inventory.initial_model(surface, 42)
                self.assertEqual(model, inventory.initial_model(surface, 42))
                original = json.dumps(model, sort_keys=True)
                self.assertGreater(len(model["stock"]), 20)
                self.assertTrue(all(r["quantity"] is not None for r in model["stock"]))
                self.assertEqual(len(inventory.scenarios(surface)), 3)
                normal = inventory.frame(surface, model, 0, "2026-09-10T12:00:00Z")
                self.assertNotIn("units", normal["snapshot"]["inventory"]["totals"])
                opening_focus = next(row["quantity"] for row in model["stock"]
                                     if [row["part_master_id"], row["location_id"]] == model["focus"])
                for scenario, quantity, quality in (
                    ("low_stock", min(9, opening_focus - 1), "good"), ("stockout", 0, "good"),
                    ("observation_unavailable", None, "unavailable"),
                ):
                    mutated = inventory.apply_action(surface, model, "inject", scenario)
                    result = inventory.frame(surface, mutated, 1, "2026-09-10T12:00:30Z")
                    focus = result["snapshot"]["movement"]["focus"][0]
                    self.assertEqual(focus["quantity"], quantity)
                    self.assertEqual(focus["quality"], quality)
                    observation = next(r for r in result["observations"]
                                       if [r["entity_id"], r["location_id"]] == model["focus"])
                    self.assertEqual(observation["payload"]["quantity"], quantity)
                    self.assertEqual(observation["payload"]["quality"], quality)
                    self.assertTrue(observation["context"]["location_is_fictional"])
                    self.assertTrue(observation["payload"]["uom"])
                    if quantity is None:
                        part = next(p for p in result["snapshot"]["inventory"]["parts"]
                                    if p["part_master_id"] == model["focus"][0])
                        self.assertIsNone(part["quantity"])
                    for action in ("recover", "reset"):
                        restored = inventory.apply_action(surface, mutated, action)
                        self.assertEqual(restored["stock"], model["stock"])
                        self.assertEqual(
                            inventory.frame(surface, restored, 2, "2026-09-10T12:01:00Z")["observations"][0]["payload"],
                            normal["observations"][0]["payload"])
                    for forbidden in ("scenario", "is_anomaly", "ground_truth", "threshold", "active_alerts"):
                        self.assertNotIn(forbidden, json.dumps(result["observations"]).lower())
                    json.dumps(result, allow_nan=False)
                self.assertEqual(json.dumps(model, sort_keys=True), original)
                self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})

    def test_invalid_actions_and_cross_domain_models(self):
        surface = "power-grid-inventory"
        model = inventory.initial_model(surface, 1)
        for action, scenario in (("start", None), ("inject", "bad"), ("reset", "stockout")):
            with self.assertRaises(ValueError):
                inventory.apply_action(surface, model, action, scenario)
        with self.assertRaises(ValueError):
            inventory.apply_action("upstream-inventory", model, "reset")
        for seed in (True, -1, "42", 2**31):
            with self.assertRaises(ValueError):
                inventory.initial_model(surface, seed)
        with self.assertRaises(ValueError):
            inventory.scenarios("power-grid")

    def test_parent_root_is_rejected_not_lexically_selected(self):
        root = inventory.dataset_root("upstream-inventory").parent
        with patch.dict(os.environ, {"ENERGY_V2_UPSTREAM_INVENTORY_ROOT": str(root)}):
            with self.assertRaisesRegex(ValueError, "explicit inventory dataset"):
                inventory.initial_model("upstream-inventory", 1)

    def test_routes_read_current_state_without_emitting(self):
        class Runtime:
            def __init__(self):
                self.models = {s: inventory.initial_model(s, 42) for s in INVENTORY_SURFACES}
                self.snapshots = {s: inventory.frame(s, m, 0, "2026-09-10T12:00:00Z")["snapshot"]
                                  for s, m in self.models.items()}

            def snapshot(self, surface):
                return self.snapshots[surface]

            def command(self, *args, **kwargs):
                raise AssertionError("GET must not command or emit")

        runtime = Runtime()
        app = Flask(__name__)
        with patch("energy_domains_v2.ui.render_portal", side_effect=lambda html, surface: html):
            inventory.register_routes(app, runtime)
        client = app.test_client()
        for surface in INVENTORY_SURFACES:
            domain = surface.removesuffix("-inventory")
            route = f"/{domain}/review/inventory"
            page = client.get(route)
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"--cp-bg:", page.data)
            with client.get(route + "/studio.js") as response:
                self.assertEqual(response.status_code, 200)
            self.assertEqual(client.post(route).status_code, 405)
            for choice in ("stockout", "observation_unavailable"):
                changed = inventory.apply_action(surface, runtime.models[surface], "inject", choice)
                runtime.snapshots[surface] = inventory.frame(
                    surface, changed, 1, "2026-09-10T12:00:30Z")["snapshot"]
                body = client.get("/api" + route + "/movement").get_json()
                self.assertEqual(body, runtime.snapshots[surface])
                self.assertEqual(client.get("/api" + route).get_json(), body["inventory"])

    def test_current_frame_javascript_null_zero_and_no_local_mutation(self):
        script = Path(inventory.__file__).parent / "inventory_assets" / "studio.js"
        model = inventory.initial_model("power-grid-inventory", 42)
        model = inventory.apply_action("power-grid-inventory", model, "inject", "observation_unavailable")
        payload = inventory.frame("power-grid-inventory", model, 0, "2026-09-10T12:00:00Z")["snapshot"]
        code = """
const assert = require('node:assert/strict');
const studio = require(process.argv[1]);
const payload = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
assert.equal(studio.quantityStatus(null), 'unknown');
assert.equal(studio.quantityStatus(0), 'out_of_stock');
assert.equal(studio.quantityStatus(9), 'low');
assert.equal(studio.quantityStatus(10), 'healthy');
const controller = studio.create(payload);
const before = controller.snapshot();
controller.play(); controller.step(); controller.reset();
assert.deepEqual(controller.snapshot(), before);
assert.equal(before.movement.frames.length, 1);
assert.equal(before.movement.focus[0].quantity, null);
"""
        subprocess.run(["node", "-e", code, str(script)], input=json.dumps(payload),
                       text=True, check=True, capture_output=True)

    def test_original_page_scripts_render_current_null_and_zero(self):
        code = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
async function boot(fixture) {
  const elements = new Map();
  class Element {
    constructor(id) {
      this.id = id; this.value = ''; this.textContent = ''; this.html = '';
      this.disabled = false; this.dataset = {}; this.attrs = {}; this.listeners = {};
      this.classList = {add() {}, remove() {}, contains() { return false; }};
    }
    set innerHTML(value) {
      this.html = value;
      for (const m of value.matchAll(/\bid="([^"]+)"/g)) node(m[1]);
    }
    get innerHTML() { return this.html; }
    addEventListener(type, listener) { this.listeners[type] = listener; }
    setAttribute(name, value) { this.attrs[name] = value; }
    getAttribute(name) { return this.attrs[name] ?? null; }
    hasAttribute(name) { return name in this.attrs; }
    querySelectorAll() { return []; }
    querySelector() { return null; }
    closest() { return null; }
    focus() {}
    scrollIntoView() {}
  }
  function node(id) {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  }
  const document = {activeElement: null, getElementById: node,
    querySelector: () => node('parts-body'), querySelectorAll: () => [],
    documentElement: {setAttribute() {}}};
  const context = vm.createContext({document, console, URLSearchParams,
    setInterval() { throw new Error('No V2 local playback'); }, clearInterval() {},
    fetch: async url => url.endsWith('basemap.json') ? {ok: false} : {
      ok: true, status: 200, json: async () => fixture.payload,
      text: async () => JSON.stringify(fixture.payload)
    }
  });
  context.window = context;
  context.addEventListener = () => {};
  context.matchMedia = () => ({matches: false});
  context.location = {search: ''};
  vm.runInContext(input.module, context);
  const scripts = [...fixture.page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)];
  vm.runInContext(scripts.filter(m => m[1].trim()).at(-1)[1], context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(node('error').textContent, '', fixture.surface);
  const root = node('root').innerHTML;
  assert(!/Could not load|Unable to load|Invalid inventory response/i.test(root), root);
  assert([...elements.values()].some(n => /<tr|<svg/.test(n.innerHTML)), fixture.surface);
}
(async () => { for (const fixture of input.fixtures) await boot(fixture); })()
  .catch(error => { console.error(error); process.exitCode = 1; });
"""
        fixtures = []
        for surface in INVENTORY_SURFACES:
            model = inventory.initial_model(surface, 42)
            domain = surface.removesuffix("-inventory")
            page = inventory._adapt_html(
                (inventory._asset_root(domain) / "inventory.html").read_text(encoding="utf-8"),
                surface)
            for scenario in ("stockout", "observation_unavailable"):
                changed = inventory.apply_action(surface, model, "inject", scenario)
                fixtures.append({"surface": surface, "page": page, "payload":
                    inventory.frame(surface, changed, 0, "2026-09-10T12:00:00Z")["snapshot"]})
        module = (Path(inventory.__file__).parent / "inventory_assets" / "studio.js").read_text()
        result = subprocess.run(["node", "-e", code], input=json.dumps(
            {"module": module, "fixtures": fixtures}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

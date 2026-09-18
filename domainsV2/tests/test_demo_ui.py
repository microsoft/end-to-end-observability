from html.parser import HTMLParser
import unittest

from energy_domains_v2.contracts import SURFACES
from energy_domains_v2.ui import render_portal, selector


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class DemoUiTests(unittest.TestCase):
    def test_landing_session_selects_nine_portals_and_caps_duration(self):
        html = selector()
        elements = Elements(html).elements
        choices = [attrs for tag, attrs in elements
                   if tag == "input" and attrs.get("name") == "v2-surface"]
        self.assertEqual({item["value"] for item in choices}, set(SURFACES))
        self.assertTrue(all("checked" in item for item in choices))
        duration = next(attrs for _, attrs in elements if attrs.get("id") == "v2-duration")
        self.assertEqual(duration["max"], "120")
        self.assertEqual(duration["value"], "120")
        self.assertEqual(html.count('id="v2-controls"'), 1)
        self.assertEqual(html.count('src="/v2/static/controls.js"'), 1)
        actions = {attrs["data-v2-action"] for _, attrs in elements if "data-v2-action" in attrs}
        self.assertEqual(actions, {"prepare", "stop", "retry_delivery"})

    def test_portals_share_session_controls_and_timed_interventions(self):
        for surface in SURFACES:
            with self.subTest(surface=surface):
                html = render_portal("<html><head><title>Domain</title></head><body></body></html>", surface)
                elements = Elements(html).elements
                panel = next(attrs for _, attrs in elements if attrs.get("id") == "v2-controls")
                self.assertEqual(panel["data-surface"], surface)
                buttons = [attrs for tag, attrs in elements
                           if tag == "button" and "data-v2-action" in attrs]
                self.assertEqual({attrs["data-v2-action"] for attrs in buttons},
                                 {"prepare", "stop", "retry_delivery", "inject", "recover"})
                self.assertTrue(all("disabled" in attrs for attrs in buttons))
                inspector = next(attrs for tag, attrs in elements
                                 if tag == "button" and attrs.get("id") == "v2-log-refresh")
                self.assertEqual(inspector["type"], "button")
                self.assertNotIn("data-v2-action", inspector)
                duration = next(attrs for _, attrs in elements if attrs.get("id") == "v2-anomaly-duration")
                self.assertEqual(duration["value"], "5")
                self.assertIn("last queued replay sample", html)
                self.assertIn("Refreshing does not extend deadlines", html)

    def test_invalid_surface_and_incomplete_document_fail(self):
        with self.assertRaises(ValueError):
            render_portal("<html></html>", "unknown")
        with self.assertRaises(ValueError):
            render_portal("<html></html>", "power-grid")


if __name__ == "__main__":
    unittest.main()

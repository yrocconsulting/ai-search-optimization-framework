"""Tests against an in-process fake of the WordPress REST API.

Run: python3 -m unittest discover -s tools/wordpress/tests -v
"""

import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))

import audit  # noqa: E402
import operations  # noqa: E402
import wpctl  # noqa: E402
from wp_client import WPClient, WPError, get_site  # noqa: E402

PASSWORD = "abcd efgh ijkl mnop"
HOME_HTML = """<html><head><title>Acme Plumbing</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"Acme"},
 {"@type":"Plumber","name":"Acme Plumbing","telephone":"+1-512-555-0100","sameAs":[]}]}</script>
</head><body><h1>Acme</h1></body></html>"""


def make_item(item_id, kind, slug, status="publish", title="Title", content="<p>Hello</p>", meta=True):
    return {
        "id": item_id, "type": kind, "status": status, "slug": slug,
        "link": f"BASE/{slug}/", "modified": "2026-10-01T00:00:00",
        "title": {"raw": title, "rendered": title},
        "content": {"raw": content, "rendered": content},
        "excerpt": {"raw": "", "rendered": ""},
        "featured_media": 0,
        "meta": {"_yoast_wpseo_title": "", "_yoast_wpseo_metadesc": "", "_yoast_wpseo_focuskw": ""} if meta else [],
        "yoast_head_json": {"title": title + " - Acme", "description": None},
    }


class FakeWP:
    def __init__(self):
        self.items = {}
        self.revisions = {}
        self.media = {7: {"id": 7, "source_url": "BASE/a.jpg", "alt_text": "", "title": {"raw": "a"}, "post": 10}}
        self.posts_log = []
        self.bridge = True
        self.add(make_item(10, "page", "services",
                           content="<p>We fix pipes.</p><img src='x.jpg'><a href='/contact/'>Contact</a>"))
        self.add(make_item(11, "page", "about", status="draft"))
        for i in range(150):
            self.add(make_item(1000 + i, "post", f"post-{i}", title=f"Post {i}"))
        self.revisions[10] = [{"id": 501, "modified": "2026-09-01", "title": {"raw": "Old title"},
                               "content": {"raw": "<p>Old content</p>"}, "excerpt": {"raw": ""}}]

    def add(self, item):
        self.items[item["id"]] = item

    def of_type(self, base):
        kind = {"pages": "page", "posts": "post"}[base]
        return [i for i in self.items.values() if i["type"] == kind]


class Handler(BaseHTTPRequestHandler):
    wp: FakeWP = None
    base = ""

    def log_message(self, *args):
        pass

    def send(self, code, body, headers=None, ctype="application/json"):
        raw = (json.dumps(body) if ctype == "application/json" else body).encode()
        raw = raw.replace(b"BASE", self.base.encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, str(v))
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authed(self):
        expected = "Basic " + base64.b64encode(f"claude:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") != expected:
            self.send(401, {"code": "incorrect_password", "message": "The password is incorrect."})
            return False
        return True

    def route(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if not path.startswith("/wp-json"):
            return None, path, q
        return path[len("/wp-json"):] or "/", path, q

    def do_GET(self):
        route, path, q = self.route()
        wp = self.wp
        if route is None:
            if path == "/":
                return self.send(200, HOME_HTML, ctype="text/html")
            slug = path.strip("/")
            for item in wp.items.values():
                if item["slug"] == slug:
                    html = f"<html><head><title>{item['title']['raw']} - Acme</title></head><body></body></html>"
                    return self.send(200, html, ctype="text/html")
            return self.send(404, "nope", ctype="text/html")
        if not self.authed():
            return
        namespaces = ["wp/v2", "yoast/v1"] + (["claude-seo-bridge/v1"] if wp.bridge else [])
        if route == "/":
            return self.send(200, {"name": "Acme Plumbing", "namespaces": namespaces})
        if route == "/claude-seo-bridge/v1/status":
            return self.send(200, {"version": "1.0.0", "seo_plugins": ["yoast"]})
        if route == "/wp/v2/users/me":
            return self.send(200, {"id": 2, "username": "claude", "name": "Claude", "roles": ["editor"]})
        if route == "/wp/v2/settings":
            return self.send(200, {"show_on_front": "page", "page_on_front": 10})
        if route == "/wp/v2/media":
            return self.send(200, list(wp.media.values()), {"X-WP-TotalPages": 1})
        parts = route.strip("/").split("/")  # wp, v2, base, [id], [revisions], [rid]
        base = parts[2]
        if len(parts) == 3:
            items = [self.view(i) for i in wp.of_type(base)]
            if "slug" in q:
                items = [i for i in items if i["slug"] == q["slug"]]
            if q.get("status", "publish") == "publish":
                items = [i for i in items if i["status"] == "publish"]
            per, page = int(q.get("per_page", 10)), int(q.get("page", 1))
            total_pages = max(1, -(-len(items) // per))
            return self.send(200, items[(page - 1) * per: page * per], {"X-WP-TotalPages": total_pages})
        item = wp.items.get(int(parts[3]))
        if not item or item["type"] != base.rstrip("s"):
            return self.send(404, {"code": "rest_post_invalid_id", "message": "Invalid post ID."})
        if len(parts) == 4:
            return self.send(200, self.view(item))
        revs = wp.revisions.get(item["id"], [])
        if len(parts) == 5:
            return self.send(200, revs)
        return self.send(200, next(r for r in revs if r["id"] == int(parts[5])))

    def view(self, item):
        if not self.wp.bridge:
            return dict(item, meta=[])
        return item

    def do_POST(self):
        route, _, _ = self.route()
        if not self.authed():
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        wp = self.wp
        wp.posts_log.append((route, body))
        parts = route.strip("/").split("/")
        base = parts[2]
        if base == "media":
            m = wp.media[int(parts[3])]
            m.update(body)
            return self.send(200, m)
        if len(parts) == 3:
            new_id = max(wp.items) + 1
            item = make_item(new_id, base.rstrip("s"), body.get("slug", f"new-{new_id}"),
                             status=body.get("status", "draft"), title=body.get("title", ""))
            wp.add(item)
            return self.send(201, item)
        item = wp.items[int(parts[3])]
        for key, value in body.items():
            if key == "meta":
                item["meta"].update(value)
            elif key in ("title", "content", "excerpt"):
                item[key] = {"raw": value, "rendered": value}
            else:
                item[key] = value
        return self.send(200, item)


class ConnectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        Handler.base = cls.base
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.sites_file = Path(cls.tmp.name) / "sites.json"
        cls.sites_file.write_text(json.dumps({"sites": {
            "acme": {"url": cls.base, "username": "claude", "password_env": "WP_TEST_PW", "allow_http": True},
            "wrongpw": {"url": cls.base, "username": "claude", "password_env": "WP_TEST_BAD", "allow_http": True},
        }}))
        os.environ["WP_SITES_FILE"] = str(cls.sites_file)
        os.environ["WP_TEST_PW"] = PASSWORD
        os.environ["WP_TEST_BAD"] = "wrong"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        Handler.wp = self.wp = FakeWP()
        self.client = WPClient(get_site("acme"))

    def test_check_reports_role_plugin_and_bridge(self):
        result = self.client.check()
        self.assertEqual(result["seo_plugin"], "yoast")
        self.assertEqual(result["seo_bridge"]["version"], "1.0.0")
        self.assertEqual(result["user"]["roles"], ["editor"])

    def test_sites_from_env_when_no_file(self):
        from wp_client import load_sites
        os.environ["WP_SITES_JSON"] = json.dumps({"sites": {"envsite": {
            "url": "https://example.com/", "username": "u", "password_env": "X"}}})
        try:
            sites = load_sites(Path(self.tmp.name) / "missing.json")
        finally:
            del os.environ["WP_SITES_JSON"]
        self.assertEqual(sites["envsite"].url, "https://example.com")

    def test_bad_password_gives_hint(self):
        with self.assertRaises(WPError) as ctx:
            WPClient(get_site("wrongpw")).check()
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("application password", str(ctx.exception))

    def test_resolve_by_id_slug_url_and_home(self):
        self.assertEqual(self.client.resolve("10")[1]["slug"], "services")
        self.assertEqual(self.client.resolve("services")[1]["id"], 10)
        self.assertEqual(self.client.resolve(f"{self.base}/services/")[1]["id"], 10)
        self.assertEqual(self.client.resolve("post-3")[0], "posts")
        self.assertEqual(self.client.resolve(self.base + "/")[1]["id"], 10)
        with self.assertRaises(WPError):
            self.client.resolve("does-not-exist")

    def test_pagination_respects_limit(self):
        self.assertEqual(len(self.client.list_content("posts", limit=120)), 120)
        self.assertEqual(len(self.client.list_content("posts", limit=500)), 150)

    def test_live_update_needs_confirm(self):
        result = operations.update_content(self.client, "services", {"title": "Plumbing Services"})
        self.assertFalse(result["applied"])
        self.assertIn("+Plumbing Services", result["preview"])
        self.assertEqual(self.wp.items[10]["title"]["raw"], "Title")
        result = operations.update_content(self.client, "services", {"title": "Plumbing Services"}, confirm=True)
        self.assertTrue(result["applied"])
        self.assertEqual(self.wp.items[10]["title"]["raw"], "Plumbing Services")

    def test_draft_update_applies_but_publishing_needs_confirm(self):
        self.assertTrue(operations.update_content(self.client, "about", {"title": "About us"})["applied"])
        self.assertFalse(operations.update_content(self.client, "about", {"status": "publish"})["applied"])
        self.assertEqual(self.wp.items[11]["status"], "draft")

    def test_unchanged_update_is_noop(self):
        result = operations.update_content(self.client, "services", {"title": "Title"}, confirm=True)
        self.assertFalse(result["applied"])
        self.assertEqual(self.wp.posts_log, [])

    def test_create_defaults_to_draft(self):
        result = operations.create_content(self.client, "posts", {"title": "New"})
        self.assertEqual(result["status"], "draft")
        self.assertFalse(operations.create_content(self.client, "posts", {"title": "X", "status": "publish"})["applied"])

    def test_set_seo_writes_meta_then_resaves(self):
        preview = operations.set_seo(self.client, "services", description="Fast, friendly plumbers in Austin.")
        self.assertFalse(preview["applied"])
        result = operations.set_seo(self.client, "services", description="Fast, friendly plumbers in Austin.",
                                    confirm=True)
        self.assertTrue(result["applied"])
        self.assertEqual(self.wp.items[10]["meta"]["_yoast_wpseo_metadesc"], "Fast, friendly plumbers in Austin.")
        self.assertEqual([body for _, body in self.wp.posts_log][-1], {})

    def test_set_seo_without_bridge_explains(self):
        self.wp.bridge = False
        with self.assertRaises(WPError) as ctx:
            operations.set_seo(self.client, "services", title="x", confirm=True)
        self.assertIn("Claude SEO Bridge", str(ctx.exception))

    def test_rollback(self):
        result = operations.rollback(self.client, "services", 501, confirm=True)
        self.assertTrue(result["applied"])
        self.assertEqual(self.wp.items[10]["content"]["raw"], "<p>Old content</p>")

    def test_media_alt(self):
        self.assertEqual(len(self.client.list_media(missing_alt=True)), 1)
        operations.set_alt(self.client, 7, "Plumber fixing a sink")
        self.assertEqual(self.client.list_media(missing_alt=True), [])

    def test_audit(self):
        report = audit.audit_site(self.client, limit=5)
        services = next(r for r in report["items"] if r["id"] == 10)
        messages = " ".join(m for _, m in services["issues"])
        self.assertIn("Missing meta description", messages)
        self.assertIn("without alt text", messages)
        self.assertNotIn("No internal links", messages)
        entity_msgs = " ".join(m for _, m in report["entity"]["issues"])
        self.assertIn("sameAs", entity_msgs)
        self.assertIn("address", entity_msgs)
        self.assertEqual(report["entity"]["entity"]["name"], "Acme Plumbing")
        markdown = audit.render_markdown(report)
        self.assertIn("# SEO & AI Search Audit: acme", markdown)
        self.assertIn("1 of 1 images are missing alt text", markdown)

    def test_cli_update_preview(self):
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write("<p>New body</p>")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = wpctl.main(["update", "acme", "services", "--content-file", f.name])
        os.unlink(f.name)
        self.assertEqual(code, 0)
        self.assertIn("Re-run with --yes", buf.getvalue())
        self.assertIn("+<p>New body</p>", buf.getvalue())

    def test_bridge_zip(self):
        target = Path(self.tmp.name) / "bridge.zip"
        with redirect_stdout(io.StringIO()):
            wpctl.main(["bridge-zip", "--out", str(target)])
        import zipfile
        self.assertIn("claude-seo-bridge/claude-seo-bridge.php", zipfile.ZipFile(target).namelist())

    def test_mcp_server_roundtrip(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "wp_get_seo", "arguments": {"site": "acme", "ref": "services"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "wp_update_content", "arguments": {"site": "acme", "ref": "services", "title": "X"}}},
        ]
        proc = subprocess.run([sys.executable, str(PKG / "mcp_server.py")],
                              input="\n".join(json.dumps(m) for m in msgs) + "\n",
                              capture_output=True, text=True, timeout=30, env=os.environ.copy())
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "wordpress-sites")
        self.assertIn("wp_audit_site", [t["name"] for t in replies[1]["result"]["tools"]])
        self.assertTrue(json.loads(replies[2]["result"]["content"][0]["text"])["editable"])
        update = json.loads(replies[3]["result"]["content"][0]["text"])
        self.assertFalse(update["applied"])
        self.assertEqual(self.wp.items[10]["title"]["raw"], "Title")


if __name__ == "__main__":
    unittest.main()

"""Minimal WordPress REST API client for managing many client sites.

Standard library only. Authenticates with WordPress Application Passwords
(Users -> Profile -> Application Passwords), so no hosting access is needed.

Site definitions live in sites.json (see sites.example.json). Passwords are
never stored there: each site names an environment variable that holds its
application password, which can come from the shell, a cloud environment
secret, or the git-ignored .env file next to this module.
"""

import base64
import difflib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SITES_FILE = HERE / "sites.json"
ENV_FILE = HERE / ".env"
USER_AGENT = "wp-claude-connector/1.0"
BRIDGE_NAMESPACE = "claude-seo-bridge/v1"

TYPE_ALIASES = {
    "post": "posts",
    "posts": "posts",
    "page": "pages",
    "pages": "pages",
    "media": "media",
}

# Post meta keys each SEO plugin uses for the per-page SEO fields.
# They only become writable over REST once the Claude SEO Bridge plugin is active.
SEO_META_KEYS = {
    "yoast": {
        "title": "_yoast_wpseo_title",
        "description": "_yoast_wpseo_metadesc",
        "focus_keyword": "_yoast_wpseo_focuskw",
    },
    "rankmath": {
        "title": "rank_math_title",
        "description": "rank_math_description",
        "focus_keyword": "rank_math_focus_keyword",
    },
    "seopress": {
        "title": "_seopress_titles_title",
        "description": "_seopress_titles_desc",
        "focus_keyword": "_seopress_analysis_target_kw",
    },
}

SEO_NAMESPACES = {"yoast/v1": "yoast", "rankmath/v1": "rankmath", "seopress/v1": "seopress"}

ERROR_HINTS = {
    401: "Authentication failed. Check the username and application password. "
    "Some hosts strip the Authorization header, and some security plugins "
    "(Wordfence, Solid Security) disable application passwords.",
    403: "The user is logged in but lacks permission. Editor is needed for "
    "content, Administrator for settings and the front-page lookup.",
    404: "Route not found. The REST API may be disabled, the content may not "
    "exist, or the site uses plain permalinks (set \"rest_route\": true).",
}


class WPError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def load_env_file(path=ENV_FILE):
    """Load KEY=VALUE lines into os.environ without overriding existing values."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass
class Site:
    key: str
    url: str
    username: str
    password_env: str
    name: str = ""
    seo_plugin: str = "auto"
    rest_route: bool = False
    allow_http: bool = False
    notes: str = ""

    @property
    def password(self):
        value = os.environ.get(self.password_env)
        if not value:
            raise WPError(
                f"Environment variable {self.password_env} is not set "
                f"(application password for site '{self.key}')."
            )
        return value

    def describe(self):
        return {
            "key": self.key,
            "name": self.name or self.key,
            "url": self.url,
            "username": self.username,
            "password_env": self.password_env,
            "password_set": bool(os.environ.get(self.password_env)),
            "seo_plugin": self.seo_plugin,
            "notes": self.notes,
        }


def sites_file_path():
    return Path(os.environ.get("WP_SITES_FILE", DEFAULT_SITES_FILE))


def load_sites(path=None):
    """Load the site registry from a file, or from the WP_SITES_JSON environment
    variable (useful in cloud sessions, where git-ignored files don't persist)."""
    load_env_file()
    path = Path(path) if path else sites_file_path()
    if path.exists():
        raw = json.loads(path.read_text())
    elif os.environ.get("WP_SITES_JSON"):
        try:
            raw = json.loads(os.environ["WP_SITES_JSON"])
        except ValueError as exc:
            raise WPError(f"WP_SITES_JSON is not valid JSON: {exc}") from None
    else:
        raise WPError(
            f"No site registry at {path} and WP_SITES_JSON is not set. "
            "Copy sites.example.json to sites.json and add your sites."
        )
    sites = {}
    for key, cfg in raw.get("sites", {}).items():
        missing = [f for f in ("url", "username", "password_env") if not cfg.get(f)]
        if missing:
            raise WPError(f"Site '{key}' is missing: {', '.join(missing)}")
        known = {f: cfg[f] for f in Site.__dataclass_fields__ if f in cfg and f != "key"}
        site = Site(key=key, **known)
        site.url = site.url.rstrip("/")
        if site.url.startswith("http://") and not site.allow_http:
            raise WPError(
                f"Site '{key}' uses http://. Application passwords require HTTPS "
                "(set \"allow_http\": true only for local test sites)."
            )
        sites[key] = site
    return sites


def get_site(key, path=None):
    sites = load_sites(path)
    if key not in sites:
        raise WPError(f"Unknown site '{key}'. Known sites: {', '.join(sorted(sites)) or 'none'}")
    return sites[key]


def rest_base(content_type):
    return TYPE_ALIASES.get(content_type, content_type)


def unified_diff(old, new, label):
    lines = difflib.unified_diff(
        (old or "").splitlines(), (new or "").splitlines(),
        fromfile=f"{label} (current)", tofile=f"{label} (proposed)", lineterm="",
    )
    return "\n".join(lines)


def detect_builder(item):
    """Name the page builder that owns this content, if any.

    Builder pages keep their real layout outside post_content, so editing the
    content field over REST may not change what visitors see.
    """
    raw = (item.get("content") or {}).get("raw") or ""
    rendered = (item.get("content") or {}).get("rendered") or ""
    if "elementor" in rendered or "elementor" in raw:
        return "elementor"
    if "[et_pb_" in raw:
        return "divi"
    if "[vc_" in raw:
        return "wpbakery"
    if "fl-builder" in rendered:
        return "beaver-builder"
    if "brxe-" in rendered:
        return "bricks"
    return None


class WPClient:
    def __init__(self, site, timeout=30):
        self.site = site
        self.timeout = timeout
        self._index = None

    # --- transport -------------------------------------------------------

    def _url(self, route, params=None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if self.site.rest_route:
            query = urllib.parse.urlencode({"rest_route": route, **params}, doseq=True)
            return f"{self.site.url}/?{query}"
        query = "?" + urllib.parse.urlencode(params, doseq=True) if params else ""
        return f"{self.site.url}/wp-json{route}{query}"

    def request(self, method, route, params=None, body=None, auth=True):
        """Return (decoded_json, headers)."""
        url = self._url(route, params)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if auth:
            token = base64.b64encode(f"{self.site.username}:{self.site.password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw, resp_headers, status = resp.read(), resp.headers, resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                err = json.loads(raw)
            except ValueError:
                err = {}
            message = err.get("message") or exc.reason
            hint = ERROR_HINTS.get(exc.code, "")
            raise WPError(
                f"{method} {route} failed ({exc.code} {err.get('code', '')}): {message}. {hint}".strip(),
                exc.code, err.get("code"),
            ) from None
        except urllib.error.URLError as exc:
            raise WPError(f"Could not reach {self.site.url}: {exc.reason}") from None
        try:
            return json.loads(raw), resp_headers
        except ValueError:
            raise WPError(
                f"{method} {route} returned non-JSON (HTTP {status}). The REST API may be "
                "blocked, or a maintenance/login page is being served instead."
            ) from None

    def get(self, route, **params):
        return self.request("GET", route, params)[0]

    def post(self, route, body):
        return self.request("POST", route, body=body)[0]

    def paginate(self, route, params=None, limit=100):
        params = dict(params or {})
        params["per_page"] = min(100, limit)
        results, page = [], 1
        while len(results) < limit:
            params["page"] = page
            data, headers = self.request("GET", route, params)
            results.extend(data)
            total_pages = int(headers.get("X-WP-TotalPages") or 1)
            if page >= total_pages or not data:
                break
            page += 1
        return results[:limit]

    def fetch_html(self, url, max_bytes=3_000_000):
        """Fetch a public page as a visitor would (no auth)."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read(max_bytes).decode(charset, errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise WPError(f"Could not fetch {url}: {exc}") from None

    # --- discovery -------------------------------------------------------

    def index(self):
        if self._index is None:
            # Authenticated, because some security plugins hide the REST API from visitors.
            self._index = self.request("GET", "/")[0]
        return self._index

    def namespaces(self):
        return self.index().get("namespaces", [])

    def seo_plugin(self):
        if self.site.seo_plugin != "auto":
            return self.site.seo_plugin
        for namespace, plugin in SEO_NAMESPACES.items():
            if namespace in self.namespaces():
                return plugin
        return "none"

    def bridge_status(self):
        if BRIDGE_NAMESPACE not in self.namespaces():
            return None
        return self.get(f"/{BRIDGE_NAMESPACE}/status")

    def whoami(self):
        me = self.get("/wp/v2/users/me", context="edit")
        return {"id": me.get("id"), "username": me.get("username"), "name": me.get("name"),
                "roles": me.get("roles", [])}

    def check(self):
        """Verify connectivity, credentials, and what this site supports."""
        index = self.index()
        result = {
            "site": self.site.key,
            "site_title": index.get("name"),
            "url": self.site.url,
            "rest_api": "ok",
            "seo_plugin": self.seo_plugin(),
            "seo_bridge": self.bridge_status(),
        }
        result["user"] = self.whoami()
        roles = result["user"]["roles"]
        if not {"administrator", "editor"} & set(roles):
            result["warning"] = f"User roles {roles} may be too limited to edit pages and other people's posts."
        if result["seo_plugin"] in SEO_META_KEYS and not result["seo_bridge"]:
            result["seo_note"] = ("SEO titles/descriptions are read-only until the Claude SEO Bridge "
                                  "plugin is installed (python3 tools/wordpress/wpctl.py bridge-zip).")
        return result

    # --- content ---------------------------------------------------------

    def list_content(self, content_type="pages", status="publish", search=None, limit=50):
        fields = "id,date,modified,slug,status,link,title,type"
        items = self.paginate(f"/wp/v2/{rest_base(content_type)}",
                              {"status": status, "search": search, "_fields": fields,
                               "context": "edit" if status != "publish" else None},
                              limit)
        return [{"id": i["id"], "title": _text(i.get("title")), "slug": i.get("slug"),
                 "status": i.get("status"), "modified": i.get("modified"), "link": i.get("link")}
                for i in items]

    def get_item(self, content_type, item_id):
        return self.get(f"/wp/v2/{rest_base(content_type)}/{item_id}", context="edit")

    def resolve(self, ref, content_type=None):
        """Find content by ID, slug, or URL. Returns (rest_base, item)."""
        types = [rest_base(content_type)] if content_type else ["pages", "posts"]
        ref = str(ref).strip()
        if ref.isdigit():
            for base in types:
                try:
                    return base, self.get_item(base, ref)
                except WPError as exc:
                    if exc.status != 404:
                        raise
            raise WPError(f"No {' or '.join(types)} with ID {ref}.")
        slug = ref
        if ref.startswith("http") or ref.startswith("/"):
            path = urllib.parse.urlparse(ref).path.strip("/")
            if not path:
                return types[0] if content_type else "pages", self._front_page()
            slug = path.split("/")[-1]
        for base in types:
            found = self.get(f"/wp/v2/{base}", slug=slug, status="any", context="edit")
            if found:
                return base, found[0]
        raise WPError(f"Nothing found for '{ref}' in {', '.join(types)}.")

    def _front_page(self):
        try:
            settings = self.get("/wp/v2/settings")
        except WPError as exc:
            raise WPError("Could not look up the front page (needs Administrator). Pass its ID instead.",
                          exc.status) from None
        if settings.get("show_on_front") != "page" or not settings.get("page_on_front"):
            raise WPError("The home page shows latest posts, not an editable page.")
        return self.get_item("pages", settings["page_on_front"])

    def summarize(self, base, item, include_content=True):
        out = {
            "id": item["id"], "type": base, "status": item.get("status"), "link": item.get("link"),
            "slug": item.get("slug"), "modified": item.get("modified"),
            "title": _raw(item.get("title")), "excerpt": _raw(item.get("excerpt")),
            "builder": detect_builder(item),
        }
        if include_content:
            out["content"] = _raw(item.get("content"))
        return out

    def preview_update(self, base, item, fields):
        """Return a readable diff of what an update would change."""
        parts = []
        for key, new in fields.items():
            current = item.get(key)
            old = _raw(current) if isinstance(current, dict) else current
            if old == new:
                continue
            if isinstance(new, str) and isinstance(old, (str, type(None))):
                parts.append(unified_diff(old, new, key) or f"{key}: (whitespace only)")
            else:
                parts.append(f"{key}: {json.dumps(old)} -> {json.dumps(new)}")
        return "\n\n".join(parts) or "(no changes)"

    def update_item(self, base, item_id, fields):
        return self.post(f"/wp/v2/{base}/{item_id}", fields)

    def create_item(self, content_type, fields):
        fields = dict(fields)
        fields.setdefault("status", "draft")
        return self.post(f"/wp/v2/{rest_base(content_type)}", fields)

    def revisions(self, base, item_id):
        revs = self.get(f"/wp/v2/{base}/{item_id}/revisions", context="edit",
                        _fields="id,date,modified,title,author")
        return [{"id": r["id"], "date": r.get("modified") or r.get("date"),
                 "title": _raw(r.get("title"))} for r in revs]

    def revision_fields(self, base, item_id, revision_id):
        rev = self.get(f"/wp/v2/{base}/{item_id}/revisions/{revision_id}", context="edit")
        return {"title": _raw(rev.get("title")), "content": _raw(rev.get("content")),
                "excerpt": _raw(rev.get("excerpt"))}

    # --- SEO -------------------------------------------------------------

    def get_seo(self, base, item):
        plugin = self.seo_plugin()
        keys = SEO_META_KEYS.get(plugin, {})
        meta = item.get("meta") or {}
        if isinstance(meta, list):  # WordPress returns [] when no meta is registered
            meta = {}
        editable = bool(keys) and all(k in meta for k in keys.values())
        head = item.get("yoast_head_json") or {}
        return {
            "id": item["id"], "link": item.get("link"), "plugin": plugin, "editable": editable,
            "title": meta.get(keys["title"]) if editable else None,
            "description": meta.get(keys["description"]) if editable else None,
            "focus_keyword": meta.get(keys["focus_keyword"]) if editable else None,
            "rendered_title": head.get("title"),
            "rendered_description": head.get("description"),
        }

    def seo_meta_fields(self, base, item, title=None, description=None, focus_keyword=None):
        seo = self.get_seo(base, item)
        if seo["plugin"] not in SEO_META_KEYS:
            raise WPError(f"No supported SEO plugin detected (found '{seo['plugin']}'). "
                          "Supported: Yoast, Rank Math, SEOPress.")
        if not seo["editable"]:
            raise WPError("SEO fields are not writable on this site yet. Install the Claude SEO Bridge "
                          "plugin (python3 tools/wordpress/wpctl.py bridge-zip) via Plugins -> Add New -> Upload.")
        keys = SEO_META_KEYS[seo["plugin"]]
        wanted = {"title": title, "description": description, "focus_keyword": focus_keyword}
        return {keys[name]: value for name, value in wanted.items() if value is not None}

    def set_seo(self, base, item_id, meta_fields):
        self.update_item(base, item_id, {"meta": meta_fields})
        # Save once more so SEO plugins that rebuild caches on save_post
        # (e.g. Yoast indexables) see the meta written by the first request.
        self.update_item(base, item_id, {})
        return self.get_seo(base, self.get_item(base, item_id))

    # --- media -----------------------------------------------------------

    def list_media(self, missing_alt=False, limit=100):
        items = self.paginate("/wp/v2/media", {"media_type": "image", "context": "edit",
                                               "_fields": "id,source_url,alt_text,title,post"}, limit)
        out = [{"id": m["id"], "url": m.get("source_url"), "alt_text": m.get("alt_text", ""),
                "title": _raw(m.get("title")), "attached_to": m.get("post")} for m in items]
        if missing_alt:
            out = [m for m in out if not (m["alt_text"] or "").strip()]
        return out

    def set_alt(self, media_id, alt_text):
        updated = self.post(f"/wp/v2/media/{media_id}", {"alt_text": alt_text})
        return {"id": updated["id"], "alt_text": updated.get("alt_text")}


def _raw(field):
    if isinstance(field, dict):
        return field.get("raw", field.get("rendered"))
    return field


def _text(field):
    value = field.get("rendered", "") if isinstance(field, dict) else (field or "")
    return re.sub(r"<[^>]+>", "", value)

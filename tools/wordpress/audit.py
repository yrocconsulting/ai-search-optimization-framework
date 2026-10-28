"""SEO and AI-search (entity) audit for a WordPress site.

Checks each published page/post for on-page basics, and the home page for the
entity signals this framework describes (Organization / LocalBusiness schema,
sameAs profiles, contact details). See overview/ and signals/ in this repo.
"""

import json
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser

from wp_client import WPError, detect_builder

TITLE_MAX = 60
DESC_MIN, DESC_MAX = 70, 160
THIN_WORDS = 300

ORG_TYPES = {
    "Organization", "Corporation", "LocalBusiness", "ProfessionalService", "Store",
    "Restaurant", "MedicalBusiness", "LegalService", "HomeAndConstructionBusiness",
    "AutomotiveBusiness", "FinancialService", "HealthAndBeautyBusiness", "RealEstateAgent",
    "Dentist", "Physician", "Attorney", "Plumber", "Electrician", "RoofingContractor",
    "HVACBusiness", "GeneralContractor", "NGO", "EducationalOrganization",
}
GENERIC_ORG_TYPES = {"Organization", "Corporation", "NGO", "EducationalOrganization"}


class PageParser(HTMLParser):
    """Collects the SEO-relevant parts of an HTML document or fragment."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.meta = {}
        self.canonical = None
        self.h1 = 0
        self.images = []
        self.links = []
        self.jsonld = []
        self.text = []
        self._in = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title" and self.title is None:
            self._in, self._buf = "title", []
        elif tag == "meta" and a.get("name"):
            self.meta[a["name"].lower()] = a.get("content", "")
        elif tag == "link" and "canonical" in (a.get("rel") or "").split():
            self.canonical = a.get("href")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.images.append(a)
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._in, self._buf = "jsonld", []
        elif tag in ("script", "style"):
            self._in = "skip"

    def handle_endtag(self, tag):
        if self._in == "title" and tag == "title":
            self.title = "".join(self._buf).strip()
            self._in = None
        elif self._in == "jsonld" and tag == "script":
            try:
                self.jsonld.append(json.loads("".join(self._buf)))
            except ValueError:
                pass
            self._in = None
        elif self._in == "skip" and tag in ("script", "style"):
            self._in = None

    def handle_data(self, data):
        if self._in in ("title", "jsonld"):
            self._buf.append(data)
        elif self._in is None:
            self.text.append(data)

    def word_count(self):
        return len(re.findall(r"\b\w[\w'-]*\b", " ".join(self.text)))


def parse(html):
    p = PageParser()
    p.feed(html or "")
    return p


def schema_nodes(jsonld_blocks):
    """Flatten JSON-LD blocks (including @graph) into a list of typed nodes."""
    nodes = []

    def walk(obj):
        if isinstance(obj, list):
            for o in obj:
                walk(o)
        elif isinstance(obj, dict):
            if "@graph" in obj:
                walk(obj["@graph"])
            if "@type" in obj:
                nodes.append(obj)

    walk(jsonld_blocks)
    return nodes


def node_types(node):
    t = node.get("@type")
    return set(t) if isinstance(t, list) else {t}


def audit_entity(client):
    """Inspect the home page's structured data for entity signals."""
    result = {"url": client.site.url + "/", "issues": []}
    try:
        page = parse(client.fetch_html(client.site.url + "/"))
    except WPError as exc:
        result["issues"].append(("high", f"Home page could not be fetched: {exc}"))
        return result
    nodes = schema_nodes(page.jsonld)
    result["schema_types"] = sorted({t for n in nodes for t in node_types(n) if t})
    orgs = [n for n in nodes if node_types(n) & ORG_TYPES or any(
        str(t).endswith("Business") for t in node_types(n))]
    if not orgs:
        result["issues"].append(("high", "No Organization or LocalBusiness schema on the home page; "
                                         "AI systems have no explicit entity definition to anchor to."))
        return result
    org = orgs[0]
    same_as = org.get("sameAs") or []
    same_as = [same_as] if isinstance(same_as, str) else same_as
    result["entity"] = {
        "type": sorted(t for t in node_types(org) if t),
        "name": org.get("name"),
        "url": org.get("url"),
        "logo": bool(org.get("logo")),
        "sameAs": same_as,
        "telephone": org.get("telephone"),
        "address": bool(org.get("address")),
    }
    if not org.get("name"):
        result["issues"].append(("high", "Organization schema has no name."))
    if not same_as:
        result["issues"].append(("medium", "No sameAs links (Google Business Profile, LinkedIn, social, "
                                           "directories) to corroborate the entity."))
    if not org.get("logo"):
        result["issues"].append(("low", "Organization schema has no logo."))
    is_local = any(t not in GENERIC_ORG_TYPES for t in node_types(org) if t)
    if not is_local:
        result["issues"].append(("low", "Schema type is generic Organization; use a LocalBusiness "
                                        "subtype if the client serves a local area."))
    elif not (org.get("address") and org.get("telephone")):
        result["issues"].append(("medium", "LocalBusiness schema is missing address or telephone "
                                           "(NAP consistency signal)."))
    return result


def audit_item(client, base, item, fetch_pages=True):
    host = urllib.parse.urlparse(client.site.url).netloc
    content = parse((item.get("content") or {}).get("rendered", ""))
    head = item.get("yoast_head_json") or {}
    title, description, robots = head.get("title"), head.get("description"), None
    if isinstance(head.get("robots"), dict):
        robots = head["robots"].get("index")
    if fetch_pages and item.get("link"):
        try:
            page = parse(client.fetch_html(item["link"]))
            title = page.title or title
            description = page.meta.get("description", description)
            robots = page.meta.get("robots", robots)
        except WPError:
            pass

    issues = []
    builder = detect_builder(item)
    if not title:
        issues.append(("high", "Missing SEO title."))
    elif len(title) > TITLE_MAX:
        issues.append(("low", f"SEO title is {len(title)} chars (aim for <= {TITLE_MAX})."))
    if not description:
        issues.append(("high", "Missing meta description."))
    elif not DESC_MIN <= len(description) <= DESC_MAX:
        issues.append(("low", f"Meta description is {len(description)} chars (aim for {DESC_MIN}-{DESC_MAX})."))
    if robots and "noindex" in str(robots):
        issues.append(("high", "Page is set to noindex."))
    words = content.word_count()
    if words < THIN_WORDS and not builder:
        issues.append(("medium", f"Thin content: {words} words."))
    if content.h1:
        issues.append(("low", f"{content.h1} H1 tag(s) inside the content; most themes already "
                              "output the title as H1."))
    no_alt = [i.get("src") for i in content.images if not (i.get("alt") or "").strip()]
    if no_alt:
        issues.append(("medium", f"{len(no_alt)} image(s) in the content without alt text."))
    internal = [h for h in content.links
                if urllib.parse.urlparse(h).netloc in ("", host) and not h.startswith(("#", "mailto:", "tel:"))]
    if not internal and not builder:
        issues.append(("medium", "No internal links in the content."))
    if base == "posts" and not item.get("featured_media"):
        issues.append(("low", "No featured image."))
    return {
        "id": item["id"], "type": base, "link": item.get("link"),
        "title": title, "description": description, "words": words,
        "builder": builder, "issues": issues,
    }


def audit_site(client, limit=100, types=("pages", "posts"), fetch_pages=True, media_limit=500):
    report = {
        "site": client.site.key,
        "url": client.site.url,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "seo_plugin": client.seo_plugin(),
        "entity": audit_entity(client),
        "items": [],
    }
    for base in types:
        items = client.paginate(f"/wp/v2/{base}", {"status": "publish"}, limit)
        report["items"].extend(audit_item(client, base, i, fetch_pages) for i in items)

    for field in ("title", "description"):
        groups = defaultdict(list)
        for r in report["items"]:
            if r[field]:
                groups[r[field].strip().lower()].append(r)
        for dupes in groups.values():
            if len(dupes) > 1:
                for r in dupes:
                    r["issues"].append(("medium", f"Duplicate {field} shared with {len(dupes) - 1} other page(s)."))

    try:
        media = client.list_media(limit=media_limit)
        report["media"] = {"images": len(media),
                           "missing_alt": sum(1 for m in media if not (m["alt_text"] or "").strip())}
    except WPError as exc:
        report["media"] = {"error": str(exc)}
    return report


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def render_markdown(report):
    lines = [f"# SEO & AI Search Audit: {report['site']}", "",
             f"- Site: {report['url']}", f"- Generated: {report['generated']}",
             f"- SEO plugin: {report['seo_plugin']}", f"- Pages/posts audited: {len(report['items'])}"]
    counts = defaultdict(int)
    for r in report["items"]:
        for sev, _ in r["issues"]:
            counts[sev] += 1
    for sev, _ in report["entity"]["issues"]:
        counts[sev] += 1
    lines.append(f"- Issues: {counts['high']} high, {counts['medium']} medium, {counts['low']} low")

    entity = report["entity"]
    lines += ["", "## Entity signals (home page)", ""]
    if entity.get("schema_types") is not None:
        lines.append(f"- Schema types found: {', '.join(entity['schema_types']) or 'none'}")
    if entity.get("entity"):
        e = entity["entity"]
        lines += [f"- Entity: {e['name']} ({', '.join(e['type'])})",
                  f"- sameAs profiles: {len(e['sameAs'])}",
                  f"- Address: {'yes' if e['address'] else 'no'}; Telephone: {e['telephone'] or 'no'}; "
                  f"Logo: {'yes' if e['logo'] else 'no'}"]
    for sev, msg in entity["issues"]:
        lines.append(f"- **{sev.upper()}**: {msg}")

    media = report.get("media", {})
    lines += ["", "## Media library", ""]
    if "error" in media:
        lines.append(f"- Could not read media: {media['error']}")
    else:
        lines.append(f"- {media['missing_alt']} of {media['images']} images are missing alt text.")

    builders = sorted({r["builder"] for r in report["items"] if r["builder"]})
    if builders:
        lines += ["", f"> Note: some pages are built with {', '.join(builders)}. Their layout is stored "
                      "outside the post content, so word-count and link checks are skipped for them and "
                      "content edits should be made in the builder."]

    lines += ["", "## Page issues", ""]
    flagged = sorted((r for r in report["items"] if r["issues"]),
                     key=lambda r: min(SEVERITY_ORDER[s] for s, _ in r["issues"]))
    if not flagged:
        lines.append("No page-level issues found.")
    for r in flagged:
        lines += [f"### {r['title'] or '(no title)'}",
                  f"{r['link']} · {r['type'][:-1]} #{r['id']} · {r['words']} words"
                  + (f" · {r['builder']}" if r["builder"] else ""), ""]
        for sev, msg in sorted(r["issues"], key=lambda i: SEVERITY_ORDER[i[0]]):
            lines.append(f"- **{sev.upper()}**: {msg}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

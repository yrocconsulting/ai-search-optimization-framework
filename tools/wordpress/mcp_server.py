#!/usr/bin/env python3
"""MCP server (stdio) exposing client WordPress sites as tools for Claude Code.

Registered in the repo's .mcp.json. Standard library only: speaks
newline-delimited JSON-RPC 2.0 as defined by the Model Context Protocol.
"""

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit  # noqa: E402
import operations  # noqa: E402
from wp_client import WPClient, WPError, get_site, load_sites  # noqa: E402

SERVER_INFO = {"name": "wordpress-sites", "version": "1.0.0"}
DEFAULT_PROTOCOL = "2025-06-18"

SITE = {"type": "string", "description": "Site key from sites.json (see wp_list_sites)."}
REF = {"type": "string", "description": "Post/page ID, slug, or full URL."}
TYPE = {"type": "string", "description": "Content type: pages, posts, or a custom post type's rest_base. "
                                         "Optional for ID/slug/URL lookups (pages then posts are tried)."}
CONFIRM = {"type": "boolean", "description": "Set true only after the user has approved the preview "
                                             "returned by a previous call. Required for live content."}


def schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


def _client(args):
    return WPClient(get_site(args["site"]))


def t_list_sites(args):
    return [s.describe() for s in load_sites().values()]


def t_check_site(args):
    return _client(args).check()


def t_list_content(args):
    return _client(args).list_content(args.get("type", "pages"), args.get("status", "publish"),
                                      args.get("search"), args.get("limit", 50))


def t_get_content(args):
    client = _client(args)
    base, item = client.resolve(args["ref"], args.get("type"))
    summary = client.summarize(base, item)
    if summary["builder"]:
        summary["warning"] = (f"Built with {summary['builder']}: editing the content field may not "
                              "change the visible page. Tell the user to edit it in the builder.")
    return summary


def t_update_content(args):
    fields = {k: args[k] for k in ("title", "content", "excerpt", "slug", "status") if k in args}
    fields.update(args.get("extra_fields") or {})
    if not fields:
        raise WPError("Provide at least one field to update.")
    return operations.update_content(_client(args), args["ref"], fields, args.get("type"),
                                     args.get("confirm", False))


def t_create_content(args):
    fields = {k: args[k] for k in ("title", "content", "excerpt", "slug", "status") if k in args}
    fields.update(args.get("extra_fields") or {})
    return operations.create_content(_client(args), args.get("type", "posts"), fields,
                                     args.get("confirm", False))


def t_get_seo(args):
    client = _client(args)
    base, item = client.resolve(args["ref"], args.get("type"))
    return client.get_seo(base, item)


def t_set_seo(args):
    return operations.set_seo(_client(args), args["ref"], args.get("title"), args.get("description"),
                              args.get("focus_keyword"), args.get("type"), args.get("confirm", False))


def t_list_media(args):
    return _client(args).list_media(args.get("missing_alt", False), args.get("limit", 100))


def t_set_alt(args):
    return operations.set_alt(_client(args), args["media_id"], args["alt_text"])


def t_revisions(args):
    client = _client(args)
    base, item = client.resolve(args["ref"], args.get("type"))
    return client.revisions(base, item["id"])


def t_rollback(args):
    return operations.rollback(_client(args), args["ref"], args["revision_id"], args.get("type"),
                               args.get("confirm", False))


def t_audit(args):
    report = audit.audit_site(_client(args), limit=args.get("limit", 100),
                              fetch_pages=args.get("fetch_pages", True))
    return audit.render_markdown(report)


TOOLS = {
    "wp_list_sites": (t_list_sites, "List configured client WordPress sites.", schema({})),
    "wp_check_site": (t_check_site, "Check a site's connection, user role, SEO plugin, and whether SEO "
                                    "fields are writable.", schema({"site": SITE}, ["site"])),
    "wp_list_content": (t_list_content, "List posts, pages, or custom post types.", schema({
        "site": SITE, "type": TYPE,
        "status": {"type": "string", "description": "publish (default), draft, pending, any."},
        "search": {"type": "string"}, "limit": {"type": "integer", "default": 50}}, ["site"])),
    "wp_get_content": (t_get_content, "Get one post/page including raw (editable) content.",
                       schema({"site": SITE, "ref": REF, "type": TYPE}, ["site", "ref"])),
    "wp_update_content": (t_update_content, "Update a post/page. Live content returns a preview diff and is "
                                            "only changed when called again with confirm=true after the user "
                                            "approves. Content must be the full raw content, not a fragment.",
                          schema({"site": SITE, "ref": REF, "type": TYPE, "title": {"type": "string"},
                                  "content": {"type": "string"}, "excerpt": {"type": "string"},
                                  "slug": {"type": "string"}, "status": {"type": "string"},
                                  "extra_fields": {"type": "object", "description": "Other REST fields."},
                                  "confirm": CONFIRM}, ["site", "ref"])),
    "wp_create_content": (t_create_content, "Create a post/page. Created as a draft unless a live status is "
                                            "given with confirm=true.",
                          schema({"site": SITE, "type": TYPE, "title": {"type": "string"},
                                  "content": {"type": "string"}, "excerpt": {"type": "string"},
                                  "slug": {"type": "string"}, "status": {"type": "string"},
                                  "extra_fields": {"type": "object"}, "confirm": CONFIRM}, ["site", "title"])),
    "wp_get_seo": (t_get_seo, "Get SEO title, meta description, and focus keyword for a post/page.",
                   schema({"site": SITE, "ref": REF, "type": TYPE}, ["site", "ref"])),
    "wp_set_seo": (t_set_seo, "Set SEO title/meta description/focus keyword (Yoast, Rank Math, SEOPress; "
                              "requires the Claude SEO Bridge plugin). Live content needs confirm=true.",
                   schema({"site": SITE, "ref": REF, "type": TYPE, "title": {"type": "string"},
                           "description": {"type": "string"}, "focus_keyword": {"type": "string"},
                           "confirm": CONFIRM}, ["site", "ref"])),
    "wp_list_media": (t_list_media, "List images in the media library.",
                      schema({"site": SITE, "missing_alt": {"type": "boolean"},
                              "limit": {"type": "integer", "default": 100}}, ["site"])),
    "wp_set_alt_text": (t_set_alt, "Set alt text on a media library image.",
                        schema({"site": SITE, "media_id": {"type": "integer"}, "alt_text": {"type": "string"}},
                               ["site", "media_id", "alt_text"])),
    "wp_list_revisions": (t_revisions, "List saved revisions of a post/page.",
                          schema({"site": SITE, "ref": REF, "type": TYPE}, ["site", "ref"])),
    "wp_rollback": (t_rollback, "Restore a post/page to a revision. Live content needs confirm=true.",
                    schema({"site": SITE, "ref": REF, "type": TYPE, "revision_id": {"type": "integer"},
                            "confirm": CONFIRM}, ["site", "ref", "revision_id"])),
    "wp_audit_site": (t_audit, "Run an SEO + AI-search entity audit and return a Markdown report.",
                      schema({"site": SITE, "limit": {"type": "integer", "default": 100,
                                                      "description": "Max items per content type."},
                              "fetch_pages": {"type": "boolean", "default": True}}, ["site"])),
}


def handle(msg):
    method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if msg_id is None:
        return None  # notification
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
                  "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in TOOLS.items()]}
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": f"Unknown tool {name}"}}
        try:
            data = TOOLS[name][0](params.get("arguments") or {})
            text = data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False)
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except WPError as exc:
            result = {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
        except Exception as exc:  # report, don't crash the server
            traceback.print_exc(file=sys.stderr)
            result = {"content": [{"type": "text", "text": f"Unexpected error: {exc!r}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

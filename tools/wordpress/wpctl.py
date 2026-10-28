#!/usr/bin/env python3
"""Command-line access to client WordPress sites.

Examples:
  python3 tools/wordpress/wpctl.py sites
  python3 tools/wordpress/wpctl.py check acme
  python3 tools/wordpress/wpctl.py list acme --type pages
  python3 tools/wordpress/wpctl.py get acme /services/ --save work/acme-services.html
  python3 tools/wordpress/wpctl.py update acme /services/ --content-file work/acme-services.html
  python3 tools/wordpress/wpctl.py seo-set acme /services/ --description "..." --yes
  python3 tools/wordpress/wpctl.py audit acme --out reports/acme-audit.md

Changes to live content print a preview first; re-run with --yes to apply.
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit  # noqa: E402
import operations  # noqa: E402
from wp_client import HERE, WPClient, WPError, get_site, load_sites  # noqa: E402

BRIDGE_DIR = HERE / "plugin" / "claude-seo-bridge"


def out(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def report_write(result):
    if result.get("preview"):
        print(result["preview"])
        print()
    if result.get("applied"):
        rest = {k: v for k, v in result.items() if k not in ("preview", "applied")}
        print("Applied:", json.dumps(rest, ensure_ascii=False))
    else:
        print(result.get("message", "Not applied."))
        if result.get("preview"):
            print("Re-run with --yes to apply.")


def client_for(args):
    return WPClient(get_site(args.site))


def cmd_sites(args):
    out([s.describe() for s in load_sites().values()])


def cmd_check(args):
    targets = list(load_sites()) if args.site == "all" else [args.site]
    for key in targets:
        try:
            out(WPClient(get_site(key)).check())
        except WPError as exc:
            out({"site": key, "error": str(exc)})


def cmd_list(args):
    out(client_for(args).list_content(args.type, args.status, args.search, args.limit))


def cmd_get(args):
    client = client_for(args)
    base, item = client.resolve(args.ref, args.type)
    summary = client.summarize(base, item)
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(summary["content"] or "")
        summary["content"] = f"(saved to {args.save})"
    if summary["builder"]:
        summary["warning"] = (f"Built with {summary['builder']}: the visible layout is stored outside "
                              "the content field, so editing content may not change the page.")
    out(summary)


def collect_fields(args):
    fields = {}
    if args.fields_json:
        fields.update(json.loads(Path(args.fields_json).read_text()))
    for name in ("title", "excerpt", "slug", "status"):
        value = getattr(args, name, None)
        if value is not None:
            fields[name] = value
    if getattr(args, "content_file", None):
        fields["content"] = Path(args.content_file).read_text()
    return fields


def cmd_update(args):
    fields = collect_fields(args)
    if not fields:
        raise WPError("Nothing to update. Pass --title, --content-file, --excerpt, --slug, --status or --fields-json.")
    report_write(operations.update_content(client_for(args), args.ref, fields, args.type, args.yes))


def cmd_create(args):
    fields = collect_fields(args)
    out(operations.create_content(client_for(args), args.type, fields, args.yes))


def cmd_seo_get(args):
    client = client_for(args)
    base, item = client.resolve(args.ref, args.type)
    out(client.get_seo(base, item))


def cmd_seo_set(args):
    report_write(operations.set_seo(client_for(args), args.ref, args.title, args.description,
                                    args.focus_keyword, args.type, args.yes))


def cmd_media(args):
    out(client_for(args).list_media(args.missing_alt, args.limit))


def cmd_set_alt(args):
    out(operations.set_alt(client_for(args), args.media_id, args.alt))


def cmd_revisions(args):
    client = client_for(args)
    base, item = client.resolve(args.ref, args.type)
    out(client.revisions(base, item["id"]))


def cmd_rollback(args):
    report_write(operations.rollback(client_for(args), args.ref, args.revision_id, args.type, args.yes))


def cmd_audit(args):
    client = client_for(args)
    report = audit.audit_site(client, limit=args.limit, fetch_pages=not args.no_fetch)
    markdown = audit.render_markdown(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(markdown)
        print(f"Report written to {args.out}")
    else:
        print(markdown)


def cmd_bridge_zip(args):
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(BRIDGE_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, Path("claude-seo-bridge") / path.relative_to(BRIDGE_DIR))
    print(f"Plugin zip written to {target}. Upload it in wp-admin: Plugins -> Add New -> Upload Plugin.")


def build_parser():
    p = argparse.ArgumentParser(description="Manage client WordPress sites over the REST API.")
    sub = p.add_subparsers(dest="command", required=True)

    def site_cmd(name, func, help_text, ref=False):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("site", help="site key from sites.json")
        if ref:
            sp.add_argument("ref", help="post/page ID, slug, or URL")
            sp.add_argument("--type", help="content type: pages, posts, or a custom rest_base")
        sp.set_defaults(func=func)
        return sp

    sub.add_parser("sites", help="list configured sites").set_defaults(func=cmd_sites)
    site_cmd("check", cmd_check, "verify connection and permissions ('all' checks every site)")

    sp = site_cmd("list", cmd_list, "list content")
    sp.add_argument("--type", default="pages")
    sp.add_argument("--status", default="publish", help="publish, draft, any, ...")
    sp.add_argument("--search")
    sp.add_argument("--limit", type=int, default=50)

    sp = site_cmd("get", cmd_get, "show one item with raw content", ref=True)
    sp.add_argument("--save", help="write raw content to this file for editing")

    def edit_args(sp):
        sp.add_argument("--title")
        sp.add_argument("--content-file", help="file containing the new raw content")
        sp.add_argument("--excerpt")
        sp.add_argument("--slug")
        sp.add_argument("--status")
        sp.add_argument("--fields-json", help="JSON file with any extra REST fields")
        sp.add_argument("--yes", action="store_true", help="apply changes to live content")

    edit_args(site_cmd("update", cmd_update, "update a post/page", ref=True))

    sp = sub.add_parser("create", help="create a post/page (draft by default)")
    sp.add_argument("site")
    sp.add_argument("--type", default="posts")
    edit_args(sp)
    sp.set_defaults(func=cmd_create)

    site_cmd("seo-get", cmd_seo_get, "show SEO title/description/focus keyword", ref=True)
    sp = site_cmd("seo-set", cmd_seo_set, "set SEO fields (needs the bridge plugin)", ref=True)
    sp.add_argument("--title")
    sp.add_argument("--description")
    sp.add_argument("--focus-keyword")
    sp.add_argument("--yes", action="store_true")

    sp = site_cmd("media", cmd_media, "list images in the media library")
    sp.add_argument("--missing-alt", action="store_true")
    sp.add_argument("--limit", type=int, default=100)

    sp = site_cmd("set-alt", cmd_set_alt, "set an image's alt text")
    sp.add_argument("media_id", type=int)
    sp.add_argument("alt")

    site_cmd("revisions", cmd_revisions, "list revisions of a post/page", ref=True)
    sp = site_cmd("rollback", cmd_rollback, "restore a revision", ref=True)
    sp.add_argument("revision_id", type=int)
    sp.add_argument("--yes", action="store_true")

    sp = site_cmd("audit", cmd_audit, "SEO + entity audit as Markdown")
    sp.add_argument("--limit", type=int, default=100, help="max items per content type")
    sp.add_argument("--no-fetch", action="store_true", help="skip fetching each public page")
    sp.add_argument("--out", help="write the report to this file")

    sp = sub.add_parser("bridge-zip", help="package the Claude SEO Bridge plugin for upload")
    sp.add_argument("--out", default="dist/claude-seo-bridge.zip")
    sp.set_defaults(func=cmd_bridge_zip)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except WPError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

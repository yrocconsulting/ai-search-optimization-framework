"""Guarded write operations shared by the CLI and the MCP server.

Changes to live content (published, scheduled, or private) are never applied
on the first call: the caller gets a diff back and must repeat the call with
confirm=True. Drafts are updated directly. WordPress keeps a revision for
every content change, so `rollback` can undo an edit.
"""

import json

from wp_client import WPError, rest_base

LIVE_STATUSES = {"publish", "future", "private"}


def _guard(item, preview, confirm, apply):
    live = item.get("status") in LIVE_STATUSES
    if preview == "(no changes)":
        return {"applied": False, "id": item["id"], "message": "Nothing to change."}
    if live and not confirm:
        return {
            "applied": False,
            "id": item["id"],
            "status": item.get("status"),
            "link": item.get("link"),
            "preview": preview,
            "message": "This content is live. Review the preview, then repeat with confirm to apply.",
        }
    result = apply()
    result.update({"applied": True, "preview": preview})
    return result


def update_content(client, ref, fields, content_type=None, confirm=False):
    base, item = client.resolve(ref, content_type)
    preview = client.preview_update(base, item, fields)
    if "status" in fields and fields["status"] != item.get("status"):
        preview += f"\n\nstatus: {item.get('status')} -> {fields['status']}"

    def apply():
        updated = client.update_item(base, item["id"], fields)
        return {"id": updated["id"], "status": updated.get("status"), "link": updated.get("link")}

    if item.get("status") not in LIVE_STATUSES and fields.get("status") in LIVE_STATUSES and not confirm:
        # Publishing a draft is also a live change.
        item = dict(item, status=fields["status"])
    return _guard(item, preview, confirm, apply)


def create_content(client, content_type, fields, confirm=False):
    status = fields.get("status", "draft")
    if status in LIVE_STATUSES and not confirm:
        return {"applied": False, "message": f"Creating with status '{status}' publishes immediately. "
                                             "Repeat with confirm, or omit status to create a draft."}
    created = client.create_item(content_type, fields)
    return {"applied": True, "id": created["id"], "status": created.get("status"),
            "link": created.get("link"), "type": rest_base(content_type)}


def set_seo(client, ref, title=None, description=None, focus_keyword=None, content_type=None, confirm=False):
    base, item = client.resolve(ref, content_type)
    current = client.get_seo(base, item)
    meta = client.seo_meta_fields(base, item, title, description, focus_keyword)
    lines = []
    for name, value in (("title", title), ("description", description), ("focus_keyword", focus_keyword)):
        if value is not None and value != current[name]:
            lines.append(f"{name}: {json.dumps(current[name])} -> {json.dumps(value)}")
    preview = "\n".join(lines) or "(no changes)"
    return _guard(item, preview, confirm, lambda: client.set_seo(base, item["id"], meta))


def rollback(client, ref, revision_id, content_type=None, confirm=False):
    base, item = client.resolve(ref, content_type)
    fields = client.revision_fields(base, item["id"], revision_id)
    preview = client.preview_update(base, item, fields)

    def apply():
        updated = client.update_item(base, item["id"], fields)
        return {"id": updated["id"], "restored_revision": revision_id, "link": updated.get("link")}

    return _guard(item, preview, confirm, apply)


def set_alt(client, media_id, alt_text):
    if not alt_text.strip():
        raise WPError("Alt text cannot be empty.")
    return client.set_alt(media_id, alt_text.strip())

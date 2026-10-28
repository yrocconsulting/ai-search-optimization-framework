---
name: wp-content-update
description: Safely change content, SEO titles/meta descriptions, or image alt text on a client WordPress site. Use for any edit, rewrite, bulk SEO fix, new draft, or rollback on a site in tools/wordpress/sites.json.
---

# Updating client WordPress content

Rules (these never change):
- **Every write to live content goes through preview -> user approval -> confirm.**
  Call the write tool without `confirm`, show the user the returned preview, and repeat
  with `confirm: true` only after they approve. For bulk jobs, show the full list of
  proposed changes in one table and get a single approval for that exact list.
- **New posts and pages are created as drafts** unless the user explicitly says to publish.
- **Respect the site's `notes`** (voice, off-limits pages, approval rules) from `wp_list_sites`.
- **Never invent facts** about the client: prices, credentials, service areas, reviews.
  Ask instead.

Steps:
1. Fetch the item with `wp_get_content` (ID, slug, or URL). If it returns a `builder`
   warning, don't edit `content`; tell the user that page's copy must be changed in the
   builder. Titles, slugs, excerpts, and SEO fields are still fine.
2. **Edit content:** start from the full `content` raw value and change only what's needed,
   keeping block comments (`<!-- wp:... -->`) intact. Send the complete new content to
   `wp_update_content`. It replaces the whole field, so never send a fragment.
3. **SEO fields:** use `wp_get_seo` / `wp_set_seo`. Aim for titles of at most 60 characters
   and descriptions of 70-160 characters, written for both people and AI answers: say what
   the business does, for whom, and where. If `editable` is false, explain that the
   Claude SEO Bridge plugin needs installing (see `tools/wordpress/README.md`).
4. **Alt text:** `wp_list_media` with `missing_alt`, then `wp_set_alt_text`. Describe the
   image specifically and include the location or service only when it's natural.
5. **After applying,** report what changed with links. If something went wrong, use
   `wp_list_revisions` and `wp_rollback`.

CLI equivalents live in `tools/wordpress/wpctl.py`. For content edits, use `get --save`,
edit the file, run `update --content-file` to preview, then add `--yes`.

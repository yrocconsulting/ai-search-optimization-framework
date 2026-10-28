---
name: wp-seo-audit
description: Audit a client WordPress site for SEO and AI-search entity signals and produce a prioritized action plan. Use when asked to audit, review, or check SEO on a client site registered in tools/wordpress/sites.json.
---

# WordPress SEO & AI Search Audit

1. **Confirm access.** Call `wp_check_site` (or `python3 tools/wordpress/wpctl.py check <site>`).
   Note the user's role, the SEO plugin, and whether the SEO bridge is installed, because
   that decides which fixes you can apply yourself.
2. **Run the audit.** Call `wp_audit_site` (or `wpctl.py audit <site> --out reports/<site>-audit-<YYYY-MM-DD>.md`).
   For large sites, start with `limit` 50.
3. **Read the site's `notes`** from `wp_list_sites` for brand voice and off-limits pages.
4. **Interpret the results** using this repo's framework (`overview/`, `signals/`):
   - Entity signals first: Organization/LocalBusiness schema, a consistent name, sameAs
     profiles, and address/phone. These drive how AI systems identify the business.
   - Then indexability (noindex), missing titles/descriptions, duplicates, and thin pages.
   - Then alt text, internal links, and H1 hygiene.
5. **Write the report** to `reports/<site>-audit-<YYYY-MM-DD>.md`, which is git-ignored.
   Put a short executive summary and a prioritized action list at the top, and mark
   each item **Claude can fix via API** or **Needs a human** (schema/plugin settings,
   page-builder pages, Google Business Profile, reviews).
6. **Offer to apply the API-fixable items.** Use the `wp-content-update` skill for that,
   and don't change anything during the audit itself.

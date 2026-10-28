# WordPress Connector for Claude Code

Lets Claude Code read, edit, and audit client WordPress sites using only a
WordPress login. No hosting, FTP, or database access is needed.

It gives you:

- **MCP tools** (`wp_list_content`, `wp_update_content`, `wp_set_seo`, `wp_audit_site`, ...),
  so you can just ask Claude: *"Rewrite the meta description on Acme's services page."*
- **A CLI** (`wpctl.py`) for the same operations from the terminal.
- **An SEO and AI-search audit** that checks on-page basics and the home page's
  entity signals (Organization/LocalBusiness schema, sameAs, NAP).
- **Claude SEO Bridge**, a small WordPress plugin that makes Yoast, Rank Math,
  or SEOPress titles and descriptions editable over the API.
- **Safety rails.** Live content is never changed without a preview and an
  explicit confirm. New content is created as a draft. Every edit can be rolled
  back from WordPress revisions. There is no delete command.

Everything is Python standard library: no `pip install` needed.

---

## 1. Prepare each client site (about 5 minutes, in wp-admin)

1. **Create a dedicated user** (Users -> Add New), e.g. `claude`, with the
   **Editor** role. Use Administrator only if Claude must manage plugins or settings.
   A separate user keeps an audit trail of what Claude changed.
2. **Create an application password.** Log in as that user and go to
   Users -> Profile -> Application Passwords. Name it `Claude Code` and click
   *Add New*. Copy the password; WordPress shows it only once.
   - Don't see the section? The site isn't on HTTPS, or a security plugin
     (Wordfence: *Login Security -> Settings*, Solid Security: *Settings -> Advanced*)
     or the host has disabled it.
3. **Optional, for SEO fields: install Claude SEO Bridge.** Run
   `python3 tools/wordpress/wpctl.py bridge-zip`, then in wp-admin go to
   Plugins -> Add New -> Upload Plugin, upload `dist/claude-seo-bridge.zip`, and activate.
   Installing plugins needs an Administrator login. If the site runs Yoast,
   Rank Math, or SEOPress, this makes the SEO title, meta description, and focus
   keyword writable over the REST API, for users who can edit that post only.

## 2. Register the site

```bash
cp tools/wordpress/sites.example.json tools/wordpress/sites.json
cp tools/wordpress/.env.example tools/wordpress/.env
```

Add one entry per client to `sites.json`, and put each application password in
`.env` under the variable named by `password_env`. Both files are git-ignored.

| Field | Meaning |
|---|---|
| `url` | Site root, `https://...` |
| `username` | The WordPress user that owns the application password |
| `password_env` | Environment variable holding the application password |
| `name`, `notes` | Shown to Claude: brand voice, no-go pages, approval rules |
| `seo_plugin` | `auto` (detect), `yoast`, `rankmath`, `seopress`, or `none` |
| `rest_route` | `true` if the site uses "Plain" permalinks (`/wp-json/` 404s) |

**In a Claude Code cloud session**, don't commit these files (git-ignored files
don't survive between sessions anyway). Instead, in the cloud environment's
settings, set each password as a secret/environment variable, and set
`WP_SITES_JSON` to the full contents of your `sites.json`. That's used whenever
no `sites.json` file exists. Also make sure the environment's network policy
allows your clients' domains.

Verify everything:

```bash
python3 tools/wordpress/wpctl.py check all
```

## 3. Use it

Open Claude Code in this repo. The `wordpress` MCP server in `.mcp.json` loads
automatically (approve it the first time). Then just ask, for example:

- "Run an SEO audit of acme and save it to reports/."
- "Write meta descriptions for every Acme service page that's missing one. Show me before applying."
- "Add alt text to all images in brightsmile's media library that don't have any."
- "Draft a 900-word blog post for acme about water heater maintenance."
- "Undo the last change to Acme's /services/ page."

Project skills in `.claude/skills/` (`wp-seo-audit`, `wp-content-update`,
`wp-add-site`) give Claude the step-by-step playbook for these jobs.

### CLI reference

```
wpctl.py sites                                  list configured sites
wpctl.py check <site|all>                       connection, role, SEO plugin, bridge
wpctl.py list <site> [--type pages] [--status publish|draft|any] [--search q]
wpctl.py get <site> <id|slug|url> [--save file.html]
wpctl.py update <site> <ref> [--title] [--content-file] [--excerpt] [--slug] [--status] [--yes]
wpctl.py create <site> --type posts --title "..." --content-file f.html   (draft)
wpctl.py seo-get <site> <ref>
wpctl.py seo-set <site> <ref> [--title] [--description] [--focus-keyword] [--yes]
wpctl.py media <site> [--missing-alt]
wpctl.py set-alt <site> <media_id> "alt text"
wpctl.py revisions <site> <ref>
wpctl.py rollback <site> <ref> <revision_id> [--yes]
wpctl.py audit <site> [--limit 100] [--no-fetch] [--out reports/site.md]
wpctl.py bridge-zip [--out dist/claude-seo-bridge.zip]
```

Without `--yes`, changes to live content only print a diff.

## Limitations

- **Page builders** (Elementor, Divi, WPBakery, Beaver, Bricks) keep the layout
  outside the normal content field. The connector detects them and warns. Edit
  those pages' body copy in the builder, but SEO fields, titles, slugs, and alt
  text still work.
- **All in One SEO** stores its data in its own table, so it isn't supported by
  the bridge.
- **Menus, widgets, theme settings, and plugin settings** are out of scope.
- **Security plugins, firewalls, and some hosts** block the REST API or strip
  the `Authorization` header. `check` reports the error with a hint.

## Development

```bash
python3 -m unittest discover -s tools/wordpress/tests -v
```

The tests run against an in-process fake of the WordPress REST API.

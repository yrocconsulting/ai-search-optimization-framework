# AI Search Optimization Framework

This repo has two parts:

- `overview/`, `signals/`: the public framework documentation (Markdown). Keep it
  educational and neutral, not marketing.
- `tools/wordpress/`: the connector for managing client WordPress sites (MCP server
  `wordpress` in `.mcp.json`, CLI `wpctl.py`). See `tools/wordpress/README.md`.

## Client site rules

- Client credentials (`tools/wordpress/sites.json`, `tools/wordpress/.env`) and client output
  (`reports/`, `work/`, `dist/`) are git-ignored. Never commit them, never print passwords,
  and never put client names or data into the public framework docs.
- Never change live content without showing the user the preview and getting approval
  first (`confirm` / `--yes`). Create new content as drafts.
- Follow each site's `notes` in `sites.json`.
- Use the project skills: `wp-seo-audit`, `wp-content-update`, `wp-add-site`.

## Tests

`python3 -m unittest discover -s tools/wordpress/tests`

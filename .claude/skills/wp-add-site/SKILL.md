---
name: wp-add-site
description: Onboard a new client WordPress site into the connector (sites.json, application password, SEO bridge). Use when the user wants to connect or add a client website.
---

# Adding a client site

1. Ask for the site URL, the WordPress username, a short key (e.g. `acme`), and any notes
   (brand voice, off-limits pages, whether changes must stay drafts).
2. Walk the user through creating a dedicated Editor user and an **application password**
   (Users -> Profile -> Application Passwords). Never ask for or store their normal
   login password.
3. Add the entry to `tools/wordpress/sites.json` (create it from `sites.example.json` if it
   doesn't exist). Use `password_env` = `WP_<KEY>_APP_PASSWORD`.
4. Tell the user to put the password in `tools/wordpress/.env` themselves, or in the cloud
   environment's secrets, rather than pasting it into chat. Never commit either file.
5. Run `python3 tools/wordpress/wpctl.py check <key>` and resolve problems using the hint
   in the error: 401 = credentials or the host stripping auth, 403 = role, 404 = REST
   disabled or plain permalinks (`"rest_route": true`).
6. If the check shows a supported SEO plugin without the bridge, run `wpctl.py bridge-zip`
   and have an Administrator upload `dist/claude-seo-bridge.zip` in Plugins -> Add New ->
   Upload Plugin. Re-run `check` to confirm.

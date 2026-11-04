---
name: buzz
description: Helps use the Buzz CLI to configure a server, authenticate, deploy static sites, and automate deployments. Use when working with Buzz, buzzstatic.dev, or the `buzz` command.
---

Buzz is a self-hosted static site server. There is no hosted Buzz service, so the user needs a server URL from whoever operates it.

Requires Node.js 22 or later. Check with `node --version` and `buzz --version` before starting. Install with `npm install --global @infomiho/buzz-cli`.

## Pick The Server

1. Run `buzz server --json`. It reads local state only and returns `default_server`, `selected_server` (with `source`: `flag`, `env`, or `default`), `authentication.source`, the saved `servers`, and `error`. If the command is unknown, the CLI is older: use `buzz config` to read the server and target it by URL only.
2. Use the server the user asked for. Map names like "work" or "personal" to `servers[].name` or a URL. Otherwise use `selected_server`, which already includes a `BUZZ_SERVER` override. Several saved servers alone are no reason to ask. Ask only when nothing is selected or the requested server is not known.
3. Pass that URL with `--server <url>` on every later command in the task. Don't change the default for a one-off task. `buzz server default` is a separate request.

## Sign In

When account identity matters, verify with `buzz whoami --server <url> --json`. A deployment token can't verify an account, so this isn't required before every deploy.

When a session is missing or rejected, run `buzz login --server <url> --no-browser` and give the user the approval URL and code. Only the user can approve it in their browser. If the error names `BUZZ_TOKEN` or `--token`, follow its tip instead, because signing in doesn't replace those overrides.

## Deploy

```bash
buzz deploy ./dist --site my-site --server <url>
```

Deploy the directory that contains `index.html`. Each deployment replaces the whole file set. A successful deployment writes the site name to a local `CNAME` file, which later deployments reuse. The file doesn't record the server, which is why `--server` stays explicit. Report the server and the URL the CLI prints.

Use `buzz deploy ./dist --private` to publish a site that only the owner and invited GitHub users can read.

Ask before running `buzz delete` or `buzz access public`. Deleting removes the site and making it public exposes every file.

For CI, use a site-scoped deployment token with `BUZZ_SERVER` and `BUZZ_TOKEN`. Never commit tokens or paste them into prompts.

See the [CLI reference](https://buzzstatic.dev/reference/cli/) and [official documentation](https://buzzstatic.dev/).

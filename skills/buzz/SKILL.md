---
name: buzz
description: Helps use the Buzz CLI to configure a server, authenticate, deploy static sites, and automate deployments. Use when working with Buzz, buzzstatic.dev, or the `buzz` command.
---

Buzz is a self-hosted static site server. There is no hosted Buzz service, so the user needs a server URL from whoever operates it.

Requires Node.js 22 or later. Check with `node --version` and `buzz --version` before starting.

```bash
npm install --global @infomiho/buzz-cli
buzz config server https://buzz.example.com
buzz login
buzz whoami
buzz deploy ./dist --site my-site
```

Deploy the directory that contains `index.html`. Each deployment replaces the whole file set. A successful deployment writes the site name to a local `CNAME` file, which later deployments reuse. Pass `--site` explicitly when the name matters.

Use `buzz deploy ./dist --private` to publish a site that only the owner and invited GitHub users can read.

Ask before running `buzz delete` or `buzz access public`. Deleting removes the site and making it public exposes every file.

For CI, use a site-scoped deployment token with `BUZZ_SERVER` and `BUZZ_TOKEN`. Never commit tokens or paste them into prompts.

See the [CLI reference](https://buzzstatic.dev/reference/cli/) and [official documentation](https://buzzstatic.dev/).

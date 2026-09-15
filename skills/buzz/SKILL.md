---
name: buzz
description: Helps use the Buzz CLI to configure a server, authenticate, deploy static sites, and automate deployments. Use when working with Buzz, buzzstatic.dev, or the `buzz` command.
---

Requires Node.js 22 or later and access to a Buzz server.

```bash
npm install --global @infomiho/buzz-cli
buzz config server https://buzz.example.com
buzz login
buzz deploy ./dist --site my-site
```

Deploy directories containing `index.html`.

For CI, use a site-scoped deployment token with `BUZZ_SERVER` and `BUZZ_TOKEN`. Never commit tokens.

See the [CLI reference](https://buzzstatic.dev/reference/cli/) and [official documentation](https://buzzstatic.dev/).

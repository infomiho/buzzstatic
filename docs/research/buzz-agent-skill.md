# Buzz agent skill research

Researched 2026-09-11 against Buzz's official documentation and source. The intended installable skill is `skills/buzz/SKILL.md`.

## Skill format

The `npx skills` CLI discovers directories containing a `SKILL.md` file. The file requires lowercase `name` and `description` YAML frontmatter. The `name` must match the parent directory.

Sources:

- [Agent Skills specification](https://agentskills.io/specification)
- [`npx skills` documentation](https://github.com/vercel-labs/skills#creating-skills)

## Buzz essentials

- Buzz is a self-hosted static-site server with a Node.js CLI.
- Users need access to a Buzz server. There is no default hosted Buzz service.
- Requirements are Node.js 22+, npm, a GitHub account, and a Buzz server URL.
- Install with `npm install --global @infomiho/buzz-cli`.
- Configure and authenticate with `buzz config server`, `buzz login`, and `buzz whoami`.
- Deploy a build directory with `buzz deploy ./dist --site my-site`.
- The deployment directory should contain a root `index.html`.
- Successful deployments write the site name to a project-local `CNAME` file.
- Redeployments replace the complete file set. Failed validation or publishing keeps the previous deployment live.
- Use session credentials interactively. Use a site-scoped deployment token for CI or unattended work.
- CI should provide `BUZZ_SERVER` and `BUZZ_TOKEN`, pin the CLI version, verify `dist/index.html`, deploy with an explicit `--site`, and verify the public URL.
- Never commit or expose deployment tokens. Buzz displays a token only once.

Sources:

- [Deploy your first site](https://buzzstatic.dev/getting-started/deploy-your-first-site/)
- [Deploy sites](https://buzzstatic.dev/guides/deploy-sites/)
- [Automate deployments](https://buzzstatic.dev/guides/automate-deployments/)
- [CLI reference](https://buzzstatic.dev/reference/cli/)
- [CLI configuration](https://buzzstatic.dev/reference/configuration/)

## Suggested skill behavior

Keep `SKILL.md` short and link to the official docs for detailed syntax. It should activate for requests to install, configure, deploy, manage, automate, or troubleshoot Buzz through the CLI. It should check for Node.js 22+ and the `buzz` command, prefer `BUZZ_TOKEN` for unattended work, use explicit site names for repeatable deployments, and warn before destructive or visibility-changing commands.

## Documentation discrepancy

The current documentation and CLI source use `--site`. The repository root README still shows the older `--subdomain` option. The skill should use `--site` and link to the live CLI reference.

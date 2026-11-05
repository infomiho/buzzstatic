---
title: Use Buzz With AI Agents
description: Install the Buzz skill so a coding agent can configure the CLI and deploy sites.
sidebar:
  order: 8
---

Use this guide to let a coding agent run the Buzz CLI for you. Buzz publishes an agent skill that tells the agent how to install the CLI, sign in, and deploy a site.

## Prerequisites

You need:

- Node.js 22 or later and npm.
- A coding agent that supports skills.
- A Buzz server URL, as described in [Deploy Your First Site](../../getting-started/deploy-your-first-site/).

## Install The Skill

Install the skill:

```bash
npx skills add infomiho/buzzstatic
```

The installer asks which of your agents to install it for. The [skills CLI](https://github.com/vercel-labs/skills) documents the other options.

## Deploy With The Agent

Ask the agent to deploy a build directory:

```text
Deploy ./dist to Buzz as my-site.
```

The skill instructs the agent to confirm Node.js 22 and the `buzz` command, then run `buzz server --json` to find the selected server. The agent uses the server you ask for, or the selected one, and passes its URL with `--server` on every later command, so a changed default can't redirect the task. When sign-in is needed, the agent runs `buzz login --server <url> --no-browser` and gives you the approval link and code. It deploys with an explicit site name and asks before running `buzz delete` or `buzz access public`.

Say which server to use when you have several, for example `Deploy ./dist to my work Buzz server as my-site.` See [Use Several Servers](../use-several-servers/).

## Keep Credentials Out Of Prompts

`buzz login` stores the session in `~/.buzz.config.json`. The agent runs commands against that session and does not need the credential itself.

For unattended work, provide a site-scoped deployment token through the `BUZZ_TOKEN` environment variable instead of pasting it into a prompt. [Automate Deployments](../../guides/automate-deployments/) explains how to create one.

## Next Steps

- [CLI Reference](../../reference/cli/) lists every command the agent can run.
- [Make A Site Private](../../guides/make-a-site-private/) explains the visibility change the agent asks about first.

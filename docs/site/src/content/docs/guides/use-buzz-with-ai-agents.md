---
title: Use Buzz With AI Agents
description: Install the Buzz skill so a coding agent can configure the CLI and deploy sites.
sidebar:
  order: 8
---

Use this guide to let a coding agent run the Buzz CLI for you. The Buzz repository publishes an agent skill that describes how to install the CLI, sign in, and deploy a site.

## Prerequisites

You need:

- Node.js 22 or later and npm.
- A coding agent that reads skills from a project or home directory.
- A Buzz server URL, as described in [Deploy Your First Site](../../getting-started/deploy-your-first-site/).

## Install The Skill

Install the skill for a project or for every project on your machine:

```bash
npx skills add infomiho/buzzstatic
```

The installer detects the agents on your machine and asks which ones to install for. See the [skills CLI](https://github.com/vercel-labs/skills) for other install options.

## Deploy With The Agent

Ask the agent to deploy a build directory:

```text
Deploy ./dist to Buzz as my-site.
```

The skill instructs the agent to confirm Node.js 22 and the `buzz` command, configure the server URL, run `buzz login` when no session exists, and deploy with an explicit site name. It also instructs the agent to ask before running `buzz delete` or `buzz access public`.

## Keep Credentials Out Of Prompts

`buzz login` stores the session in `~/.buzz.config.json`. The agent runs commands against that session and does not need the credential itself.

For unattended work, provide a site-scoped deployment token through the `BUZZ_TOKEN` environment variable instead of pasting it into a prompt. [Automate Deployments](../../guides/automate-deployments/) explains how to create one.

## Next Steps

- [CLI Reference](../../reference/cli/) lists every command the agent can run.
- [Make A Site Private](../../guides/make-a-site-private/) explains the visibility change the agent asks about first.

---
title: Use Several Servers
description: Name Buzz servers, sign in to each, and choose which one a command targets.
sidebar:
  order: 9
---

Use this guide when you deploy to more than one Buzz server, such as a personal server and a work server. Each server keeps its own saved session, so you can stay signed in to all of them.

## See Which Server Commands Use

```bash
buzz server
```

The overview lists your saved servers and whether each has a saved session. It also shows the saved default, the server the next command will use, and why that server was selected. It makes no network request, so `saved` means a local session exists, not that it is still valid. Verify the account with `buzz whoami --server <name-or-url>`.

`buzz server --json` prints the same information for scripts and agents.

## Add And Sign In To A Second Server

```bash
buzz server add personal https://buzz.personal.example --default
buzz server add work https://buzz.work.example
buzz login --server work
```

Adding a server and signing in never change the default. Use `--default` when adding, or change it later:

```bash
buzz server default work
```

Names are local labels for URLs. They don't select a GitHub account. The browser you approve the sign-in with decides the account. To approve as another GitHub account, open the approval page in a browser profile signed in to that account and enter the same code.

`buzz server remove work` removes only the name. The URL keeps its saved session, and stays the default if it was. Sign out of a server with `buzz logout --server <name-or-url>`.

## Target A Server For One Command

Every remote command accepts `--server` with a saved name or a full URL:

```bash
buzz deploy ./dist --site my-site --server personal
buzz list --server https://buzz.work.example
```

The CLI picks the server in this order:

1. `--server`, for one command.
2. `BUZZ_SERVER`, for every command in that environment.
3. The saved default.

An unknown name or an invalid URL is an error. The CLI never falls back to another server. With no default and no override, remote commands stop and ask you to add a server.

Before deploying or changing a site, the CLI prints the server, why it was selected, and which credential it uses. Delete and public-access confirmations name the server too.

## Environment Overrides

`BUZZ_SERVER` and `BUZZ_TOKEN` override the saved default and saved session wherever they are set. `buzz server` reports when they are active. After `buzz server default`, the CLI also tells you if `BUZZ_SERVER` keeps the current shell on another server.

When a credential is rejected, the error names the server and gives recovery for the credential that was actually used. For example, a rejected saved session suggests `buzz login --server work`, while a rejected `BUZZ_TOKEN` suggests updating or unsetting it.

## Existing Configurations

Configurations from older CLI versions keep working without signing in again. Their servers appear as unnamed URLs in `buzz server`. Name one at any time with `buzz server add <name> <url>`, which keeps its session and default.

An old configuration that relied on the implicit `http://localhost:8080` server keeps it as an explicit default. A new installation has no default.

## CI

Set `BUZZ_SERVER` to the full URL and `BUZZ_TOKEN` to a deployment token, as described in [Automate Deployments](../automate-deployments/). `buzz login` refuses to run in CI.

## The CNAME File Has No Server

The `CNAME` file stores only the site name. If you change the default or the override, the same project deploys to the newly selected server. The banner printed before the upload shows where it is going, but it doesn't stop the deployment. Pass `--server` in project scripts where deploying to the wrong server matters.

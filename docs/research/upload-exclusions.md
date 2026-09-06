# Upload exclusions: Railway CLI, Railpack, and Surge

Researched 2026-09-06 using official documentation, Context7, and shallow (`git clone --depth 1`) source checkouts. Only source was inspected; no fetched application code was executed. Branch heads are snapshots, not a claim about every released version.

## Recommendation for Buzz

Keep the current short exclusion list and apply it at every directory depth. Preserve legitimate dotfiles such as `.well-known`, generated output, and ordinary names that merely contain an excluded name. This fixes the reported leak and aligns the CLI with the browser uploader's existing path-component checks. A new ignore-file format, `.gitignore` inheritance, or blanket dotfile filtering would add behavior beyond this bug fix. The existing [CLI test](../../cli/src/lib.test.ts) explicitly requires `.well-known/acme-challenge`; the [browser uploader](../../server/src/server/static/deploy.js) already excludes `.git`, `node_modules`, `.vscode`, and `.idea` anywhere in a path.

Use archive-content regression tests for nested excluded directories, a nested `.git` **file** (as used by Git worktrees/submodules), existing `.env` exclusions, and retained site assets. Keep the default directory skip list for pruning, and use recursive ignore patterns for nested entries and their contents. Verify actual ZIP entries with the installed archiver version rather than assuming its glob syntax. This recommendation does not require a custom filesystem walker.

## Comparison

| Tool | Purpose and defaults | User configuration and nested matching | Dotfiles |
| --- | --- | --- | --- |
| Railway CLI | Uploads local source for a remote build; hard-excludes `.git` and `node_modules`. | Walks with `.gitignore` and `.railwayignore`; `--no-gitignore` disables the former. Hard exclusions check **every path component**, so nested copies are excluded too. | Explicitly allows hidden entries unless another rule excludes them. |
| Railpack | Builds an image from a source context. Without configured exclusions, the initial local context includes all files. | Root `.dockerignore` plus `railpack.json` `exclude`, merged into BuildKit exclusions; negation supported. Bare `node_modules` is root-only; `**/node_modules` matches every depth. | No blanket hidden-file exclusion in the initial local context. |
| Surge | Publishes a directory as static web content. Defaults: `.git`, `.*`, `*.*~`, `node_modules`, `bower_components`. | Root `.surgeignore` is added after defaults using the `ignore` package. Filters every relative file/directory path and prunes ignored directories. Slashless defaults therefore cover nested entries. Does not load `.gitignore` in the inspected publishing path. | `.*` excludes hidden files/directories, including `.well-known`, unless user rules reinclude them. |

Sources: [Railway CLI documentation](https://docs.railway.com/cli/up#file-handling), [Railway archive implementation](https://github.com/railwayapp/cli/blob/9d34685d9c048f1cb1906c4854f59c70c10c7628/src/controllers/upload.rs#L29-L75), [Railpack exclusion documentation](https://railpack.com/config/excluding-files/), [Railpack local context](https://github.com/railwayapp/railpack/blob/462069b876a5763c3b8be27ee1b2405ec6b5be97/buildkit/convert.go#L39-L54), [Surge publishing documentation](https://surge.sh/docs/cli/publishing#ignoring-files), [Surge defaults](https://github.com/surge-sh/ignore/blob/aa11bbd1096de6848b07c2b19ad4adc9154eb8e4/index.json), [Surge archive selection](https://github.com/sintaxi/surge-sdk/blob/a080ef6a138ab3b1472786fb04193a003f920e44/stream/lib/stream.js#L30-L84).

## Details that affect the decision

**Build context and public files have different requirements.** Railpack analyzes source and constructs an image; Railway CLI uploads that source first. Railpack's later `.dockerignore` filtering cannot prevent a file already being uploaded by Railway CLI. Likewise, Railpack's provider-specific image layer filters are separate from initial context selection: its Node provider excludes `node_modules` and `.yarn` from one build layer while adding an independently selected dependency layer. Buzz publishes the uploaded site's files, making Surge the closer product comparison. Automatically importing Git exclusions could omit generated content that users intentionally want to publish. [Railpack overview](https://railpack.com/), [Railway upload flow](https://github.com/railwayapp/cli/blob/9d34685d9c048f1cb1906c4854f59c70c10c7628/src/controllers/upload.rs#L88-L112), [Node image layers](https://github.com/railwayapp/railpack/blob/462069b876a5763c3b8be27ee1b2405ec6b5be97/core/providers/node/node.go#L221-L240). The implication for Buzz is a design recommendation, not a behavior asserted by these tools.

**Symlink behavior is not a safe template to copy.** Railway enables `follow_links(true)` while walking. Surge's actual upload walker uses `statSync`, so it traverses directory symlinks; its size display instead uses `lstatSync`, and neither inspected walker adds a root-containment check. Railpack's image-copy operation requests `FollowSymlinks: true` inside BuildKit; that does not establish unrestricted traversal of the host filesystem. These are distinct stages, and the inspected sources do not justify treating all three as equivalent. The nested-directory fix in Buzz should not introduce link following. [Railway walker](https://github.com/railwayapp/cli/blob/9d34685d9c048f1cb1906c4854f59c70c10c7628/src/controllers/upload.rs#L50-L73), [Surge upload walker](https://github.com/sintaxi/surge-sdk/blob/a080ef6a138ab3b1472786fb04193a003f920e44/stream/lib/stream.js#L30-L84), [Surge size walker](https://github.com/sintaxi/surge/blob/05dacc5099bc82ebb4e19a067d85791222e73843/lib/middleware/_shared/_size.js#L19-L55), [Railpack copy operation](https://github.com/railwayapp/railpack/blob/462069b876a5763c3b8be27ee1b2405ec6b5be97/buildkit/build_llb/layers.go#L96-L115).

**Client defaults prevent accidents; they are not server enforcement.** Buzz's browser also accepts existing ZIP files without rebuilding them through its folder filters. The recommendation above fixes generated CLI archives; it does not claim to enforce a universal prohibited-file policy on every upload. [Browser staging and upload](../../server/src/server/static/deploy.js).

## Reproducible source snapshots

Every checkout below reports `true` for `git rev-parse --is-shallow-repository`. The two additional Surge repositories own the actual uploader and default patterns used by its CLI. Inspected manifests identify `surge` 0.44.1, `surge-stream` 0.20.0, and `surge-ignore` 0.3.0.

| Repository | Commit | Local checkout |
| --- | --- | --- |
| [railwayapp/cli](https://github.com/railwayapp/cli) | `9d34685d9c048f1cb1906c4854f59c70c10c7628` | `/tmp/buzz-railway-cli` |
| [railwayapp/railpack](https://github.com/railwayapp/railpack) | `462069b876a5763c3b8be27ee1b2405ec6b5be97` | `/tmp/buzz-railpack` |
| [sintaxi/surge](https://github.com/sintaxi/surge) | `05dacc5099bc82ebb4e19a067d85791222e73843` | `/tmp/buzz-surge` |
| [sintaxi/surge-sdk](https://github.com/sintaxi/surge-sdk) | `a080ef6a138ab3b1472786fb04193a003f920e44` | `/tmp/buzz-surge-sdk` |
| [surge-sh/ignore](https://github.com/surge-sh/ignore) | `aa11bbd1096de6848b07c2b19ad4adc9154eb8e4` | `/tmp/buzz-surge-ignore` |

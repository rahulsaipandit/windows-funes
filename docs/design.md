# Funes & Puppyone: Agent Memory/Context Systems — Architecture Notes

**Last revised:** 2026-11-22

> **Status: evaluated; Funes is installed locally on Windows (see "Installation notes").** This document summarizes the architecture of two third-party agent memory/context systems — [huggingface/funes](https://github.com/huggingface/funes) and [puppyone-ai/puppyone](https://github.com/puppyone-ai/puppyone) — and compares them. Funes is wired into Claude Code; Puppyone is not. See "Concerns" below before adopting either.

## Overview

Funes is a durable, searchable memory system for AI coding agents. It indexes past agent sessions (Claude Code, Codex, pi, Hermes) so an agent can recall prior decisions, rationale, and findings across conversations — and optionally publish that memory as a shareable dataset on Hugging Face Hub.

Puppyone (documented in [Part 2](#part-2-puppyone) below) solves an adjacent but different problem: it is a hosted, permissioned "Context Drive" that gives one or more agents live, access-controlled access to external data sources and files — not a search index over past conversations.

Funes is a session-transcript search layer: it indexes raw transcripts for retrieval and does not summarize or curate them.

# Part 1: Funes

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│  Agent session transcripts (Claude Code, Codex, pi, Hermes)│
└───────────────────────────┬────────────────────────────────┘
                            │  the agent's integration converts each
                            │  session into a .funes.jsonl turns file
                  ┌─────────▼─────────┐
                  │  Spool            │  ~/.funes/spool/<agent>/
                  │  (turns files)    │  (funes never reads transcripts itself)
                  └─────────┬─────────┘
                            │  funes index
                  ┌─────────▼─────────┐
                  │  Chunker          │
                  └─────────┬─────────┘
                            │
                  ┌─────────▼─────────┐
                  │  Embedder         │  pinned local embedding model
                  │  (local model)    │
                  └─────────┬─────────┘
                            │
                  ┌─────────▼─────────┐
                  │  Lance dataset    │  local, columnar, append-only;
                  │  (local disk)     │  stored as written; vector + BM25
                  └─────────┬─────────┘
                            │  funes push
                  ┌─────────▼─────────┐
                  │  Redaction gate   │  TruffleHog scan: redacts secrets,
                  │  (push only)      │  fails closed without it
                  └─────────┬─────────┘
                            │
                  ┌─────────▼─────────┐
                  │  Hugging Face Hub │  optional: published as
                  │  (.parquet)       │  a shareable dataset
                  └───────────────────┘
```

### Storage & data format

- **Local backend:** [Lance](https://lancedb.github.io/lance/) — a columnar, append-only dataset format. "Everything downstream of parsing is source-agnostic," i.e. once a transcript is normalized into the generic turn/block shape, the pipeline no longer cares which agent produced it.
- **Location:** local disk by default.
- **Sharing:** `funes push <memory>` publishes the dataset as `.parquet` on Hugging Face Hub, for reuse across machines or by teammates.
- **Import:** accepts `.funes.jsonl` turns files conforming to a defined schema (see `docs/funes-jsonl.md`), so other agents and tools can feed transcripts in without a bespoke Funes-side adapter.
- **Indexing:** hybrid retrieval — vector search + BM25, fused — over chunked, embedded transcript turns. Subagent transcripts are included, not just the top-level session.

### Interfaces

- **CLI:**
  - `funes add <agent>` — wire the integration into a given agent: installs its hooks, which convert and index each turn, and registers the MCP server.
  - `funes recall "<query>"` — hybrid vector+BM25 search over indexed memory.
  - `funes ask <agent> "<query>"` — have an agent answer a question grounded in recalled memory.
  - `funes push <memory>` — publish local memory to Hugging Face Hub. Off by default: nothing is published unless you name a memory, and repos funes creates are private. funes supports Hugging Face Hub and local paths as memories, not a private GitHub repository.
- **MCP server:** the same `recall`/`get` operations are exposed as MCP tools, so an agent can call them mid-conversation instead of shelling out to the CLI.

### Security posture (as documented upstream)

- The local memory keeps sessions as written, credentials included; it is not encrypted.
- `funes push` scans with TruffleHog and redacts the secrets it finds before uploading. A block it can't redact in place is withheld, and the push fails closed without TruffleHog.
- `funes scrub` redacts secrets from rows already in the local memory.

### Build/install requirements

- Prebuilt binaries via a platform-detecting installer script, with SHA256SUMS for verification.
- Building from source requires a Rust toolchain and a protobuf compiler.

## Integration path (Claude Code)

Funes registers with Claude Code at user scope, so it covers every project:

1. Install the Funes CLI (verify checksums, or build from source — see "How to build (native Windows)").
2. Run `funes add claude` (`funes add claude local` binds no Hub memory explicitly) to install its plugin (per-turn indexing hooks) and register it as an MCP server.
3. Claude Code gains `recall`, `get`, `sessions`, `sketch`, `scan` and `status` as callable MCP tools during a session.

## Concerns

- **Data exposure / confidentiality.** Funes indexes full session transcripts and stores them as written, secrets included. Redaction happens only on push (and in `funes scrub`) and is scoped to credentials, not general proprietary content — review what gets indexed before enabling. Publishing is opt-in and local is the default: a memory is only pushed if you name one (`funes add claude <org>/<repo>`, or `funes push <memory>`), and dataset repos funes creates are **private** (an existing repo keeps whatever visibility it already has). Still treat `funes push` as a high-risk, explicit action — never wire it into automation for a repo with private material, and confirm the target dataset's visibility on the Hub before the first push.
- **Supply chain.** Prebuilt binaries are a trust decision; verify SHA256SUMS or build from source if that's a concern.
- **New MCP attack surface.** Wiring Funes in gives Claude Code an MCP server whose tool outputs (recalled transcript snippets) become context of a session, similar to any other MCP tool — output should be treated as data, not instructions, per standard MCP-content caution.
- **Maturity.** Community/HF-org project, not a first-party Anthropic or Hugging Face product; check license, issue tracker, and activity before relying on it for anything durable.


## Installation notes

Funes is installed into Claude Code with `funes add claude`. What to know on Windows:

- **Windows support.** funes runs an integration's `setup` through `sh`, because a `#!/bin/sh` file is not a program on Windows. The upstream integration is a shell script, so it needs Git for Windows' `sh` and `jq`. A native converter (for example in Rust, inside the fork) would be the cleaner long-term fix, but it is a bigger job than a patch.
- **Where does `funes.exe` go?** It's in `X:\Users\<username>\.local\bin`, a folder that is already on `PATH`. Hooks and the MCP entry call `funes` by name, so a missing or partial `PATH` stops them; a hook that can't find it logs `index ABORT: funes not found`.
- **`jq` and line endings.** Windows `jq.exe` writes CRLF, and the integration's converter expects LF, so its self-test fails. A small `jq` wrapper next to `jq-real.exe` that runs it with `-b` keeps `\n`. Alternatively the fork could ship its own copy of the integration with `jq -b` built in, installed with `funes add claude --from <dir>`.
- **Checksums.** The `jq` download is checked against the checksum file on its own GitHub release page. That catches a corrupted download, not a compromised release, because both come from the same page. `funes.exe` is built from the fork, so it has no checksum. funes verifies the integration archive against upstream's published checksum itself.
- **Scope.** `funes add claude` installs at user scope: the plugin `funes@huggingface` (the per-turn indexing hooks) and a stdio MCP server `funes` in the user config. It covers every folder opened in VS Code with Claude Code, with nothing to enable per project. Windows that were already open need Claude Code restarted to load it. To turn funes off for one project, use Claude Code's `/mcp` or `/plugin` menus; `funes remove claude` turns it off everywhere.
- **One shared memory.** Every project's sessions go into the same index, so a question in general words can return passages from other projects. `recall` takes `--repo <owner/name>` to narrow a search to one repo; it matches the repo's `owner/name`, so it won't match folders with no resolvable GitHub remote.
- **Security.** The setup and the scripts it installs make no network calls and use no `eval`, `sudo` or downloads; they write only to the funes home and the plugin folder, and run `claude plugin` and `claude mcp`. The converter reads every Claude transcript, which can contain secrets such as keys and pasted tokens, and the memory stores them as written and unencrypted, so anyone with access to the account can read it. Publishing is a separate step: Hub sync defaults to no, repos funes creates are private, and a push redacts secrets only with TruffleHog installed (it fails closed without it). The integration is code from `huggingface/funes-integrations` that runs with the user's rights, so review it before trusting it.

## Backfilling past sessions and indexing new ones

Both work, and both go through the spool:

- **Backfilling past history.** `funes add claude` converts the Claude Code sessions already on
  disk (under `~/.claude/projects`) into `.funes.jsonl` turns files in the spool, and
  `funes index --harness claude` indexes them. Indexing is incremental and idempotent, so a re-run
  doesn't redo sessions that are already stored. funes reads no agent's own transcripts directly;
  the integration's converter does.
- **Ongoing indexing.** The plugin's Stop hook converts the session in progress after each turn and
  indexes it, so new conversations are picked up as they happen. With a memory bound, session
  boundaries also publish.
- **Rebuilding.** A memory can only be rebuilt from the agent's own transcripts, and Claude Code
  removes old ones after its retention period (`cleanupPeriodDays`, 30 days by default). A session
  whose transcript is gone can't be rebuilt, so keep the old memory as an archive before replacing
  it.

## How to build (native Windows)

Upstream funes only ever shipped two BLAS seams — macOS (Accelerate) and Linux (faer) — and a
handful of Unix-only calls elsewhere. None of that is malicious or wrong, it's just never been
asked to run on Windows before. Rather than build a third BLAS seam, we route Windows onto the
existing `onnx` (fastembed/ort) backend, which already ships prebuilt Windows binaries, and patch
out the remaining Unix-only calls with `#[cfg(unix)]`/`#[cfg(windows)]` guards. Every change under
"Code changes made for Windows portability" below is a platform gate, not a behavior change for
macOS/Linux — with one deliberate exception, called out separately: the Hub-sync confirmation
prompts in `funes add` (`src/commands/add.rs`) now default to no (`[y/N]`) instead of yes
(`[Y/n]`), on every platform. That's a real behavior change, not a portability shim — it means
pressing Enter, or running `funes add` non-interactively, can no longer push memory to the Hub or
create a dataset by accident; syncing always requires an explicit "y"/"yes" now.

### Prerequisites

- Rust (native `x86_64-pc-windows-msvc` toolchain; already covered if you have `cargo`/`rustc` on
  PATH).
- `protoc` (the `lance*` crates' build scripts require it on every platform, not just Windows —
  install via `winget install Google.Protobuf` or your package manager of choice, then make sure
  `protoc.exe` is on PATH or set `PROTOC` to its full path).

### Build

```powershell
cargo build --release --no-default-features --features onnx
```

`--no-default-features --features onnx` is required on Windows: the default `blas` feature pulls
in `src/inference/blas.rs`, whose `mod seam` only has `macos`/`linux` arms — it fails to resolve
`seam::sgemm`/`seam::vexp`/etc. on Windows and was never going to compile there. Using `onnx`
instead sidesteps `blas.rs` entirely (it isn't compiled when the feature is off), at the cost of
using `fastembed`/`ort` for embedding/reranking instead of the from-scratch BLAS forward.

### Code changes made for Windows portability

Each is a `#[cfg(unix)]`/`#[cfg(windows)]` gate or a small fallback around code that has no Windows
equivalent, not a rewrite of shared logic. Against upstream the whole port is about 170 changed
lines across 14 files — small enough to re-apply when upstream's lines shift:

- **`Cargo.toml`, `build.rs`** — `libc` (used only for `geteuid`) is a Unix-only dependency, and
  `build.rs` gives MSVC binaries an 8 MB main-thread stack (the Windows default of 1 MB overflows
  while indexing).
- **`src/lib.rs`** — the compile-time guard now rejects platforms other than Linux, macOS and
  Windows.
- **`src/home.rs`, `src/hub.rs`, `src/scan.rs`** — read `%USERPROFILE%` where `$HOME` isn't set;
  `scan.rs` also looks for `trufflehog.exe`.
- **`src/agents/registry.rs`** — the integration registry's file-mode, ownership and symlink checks
  are Unix-only and skipped on Windows; an integration's `setup` runs through `sh`, with a hint to
  install Git for Windows when `sh` isn't on `PATH`.
- **`src/commands/update.rs`** — the `PermissionsExt` calls are Unix-only, and the self-update
  hand-over runs the new binary and passes its exit status on instead of using `exec`.
- **`src/traces/source.rs`, `src/traces/spool.rs`** — Windows has no inode in `std`, so a unit's
  signature is its length plus nanosecond mtime; the symlink tests are Unix-only.
- **`src/commands/add.rs`** — the deliberate behavior change described above (the Hub-sync prompts
  default to no).
- **Tests** — those that need POSIX modes, symlinks, ptys or shell stand-ins are `#[cfg(unix)]`.

Not touched, and out of scope:

- `src/inference/blas.rs` is not compiled at all under `--features onnx`.
- `update.rs`'s `ASSET` const still resolves to `None` on Windows (no prebuilt release binary
  published for it upstream yet), so `funes update` correctly falls back to its "no prebuilt
  binary, build from source" message rather than claiming an asset that doesn't exist.
- The integration's `setup` and hook scripts are shell scripts, so they need Git for Windows' `sh`
  (and `jq`). Rewriting them natively is a bigger, separate task from "make it compile.

### Verify

```powershell
cargo test --no-default-features --features onnx
```

### Wiring it into Claude Code (local only, no Hub sync)

Steps to add funes to Claude Code while keeping everything local:

1. Put the built binary on `PATH`: copy `funes.exe` into a folder that already is (for example
   `%USERPROFILE%\.local\bin`), and put Git for Windows' `usr\bin` ahead of System32 so `sh` is
   Git's, not the WSL launcher.
2. `funes add claude local` — pass `local` explicitly rather than a bare `funes add claude` so
   there's no ambiguity about binding a Hub memory. This:
   - installs the funes hooks plugin (`funes@huggingface` from the `huggingface` marketplace) so
     Claude Code indexes every turn — **local only**, since no memory was bound;
   - registers `funes` as a stdio MCP server (`funes mcp`) in `~/.claude.json`, giving the session
     `recall`/`get` and the other read tools;
   - converts the sessions already on disk into the spool; `funes index --harness claude` then
     indexes them (on the CPU, so a large history takes a long time).
   Requires `sh` and `jq` on `PATH` (Git for Windows ships `sh`).
3. **Restart VS Code** (every window) so the session picks up the new MCP server registration; an
   already-running session doesn't see it until it restarts. Launch it from the Start menu or
   taskbar: a VS Code started from a shell with a trimmed `PATH` can't find `funes`, and the MCP
   server then fails to connect.

One gotcha: **TruffleHog is needed only for pushing.** `funes index` does no secret scanning (the
local memory stores sessions as written); `funes push` is where secrets are redacted, and it
refuses to run without TruffleHog. That's fine for a local-only setup, since nothing indexed leaves
the machine, but install [TruffleHog](https://github.com/trufflesecurity/trufflehog) before ever
binding a memory and pushing.

Verify it worked:

```powershell
funes status   # memory path, chunk/session counts, last indexed time — no memory bound means local-only
```

### Verifying what's in memory and what recall returns

One memory (in the funes home: `~/.funes`, or `$FUNES_HOME`) holds every indexed session from
every project on the machine — it is **not** scoped per repo (see "Is the memory scoped by repo?"
below). These are the commands used to inspect it:

- **`funes status`** — quick health check: memory path, chunk/session counts, last-indexed time,
  and whether a memory is bound (local-only vs. synced to a Hub dataset).
- **`funes recall "<query>"`** — the core check: prints the ranked passages an agent would get for
  that query, with session/turn IDs and scores, so you can confirm the right memory surfaces
  before trusting it mid-task.
- **`funes get <session_id> [--from <seq>] [--to <seq>]`** — reassembles a hit's full turn (and
  surrounding turns) so you can confirm a passage really came from the source it claims to.
- **`funes sessions [--repo <owner/name>] [--since <date>] [--until <date>]`** — lists indexed
  sessions with project/dir and counts; `--repo` filters to one repo.
- **`funes sketch <session_id>`** — a digest/summary of one session.
- **`funes scan <session_id> "<literal>"`** — finds a literal string inside one session's
  transcript.

The most direct end-to-end test: ask the agent something that depends on a past decision in an
indexed project. It should reach for `recall` on its own mid-answer and cite the session it drew
from (same as the README's `docs/img/recall.gif` demo).

### Is the memory scoped by repo?

**No — by default one memory spans every project on the machine**, and `funes recall` searches all
of it unless you pass `--repo`. Confirmed on this install: the single local memory holds sessions
from many different repos and directories, so a query with no scoping flag can surface a hit from
any of them.

What repo-awareness does exist:

- Each indexed session stores a resolved `repo` field (`owner/name`, from the git checkout at index
  time; space-joined if a session touched more than one remote) and a `workdir` fallback when the
  checkout didn't resolve (`src/commands/recall.rs`, `SessionRow`/`SessionFilter`).
- **`funes sessions --repo <owner/name>`** filters the session *listing* to one repo, and
  **`funes recall --repo <owner/name>`** (the MCP `recall` tool's `repo` parameter) restricts a
  search to turns from that repo. Without it, recall is not confined to the current repo.
- The only isolation that keeps one memory's content out of a search entirely is at the memory
  level: point `--memory` at a different Hub dataset or a local path to read a separate, disjoint
  memory.

Practical implication: a single local (or Hub) memory is a cross-project memory by design — closer
to "everything this machine's agent has ever worked on" than "this repo's history." If cross-repo
recall leakage is a concern (e.g. a private-material repo alongside public ones), filter with
`--repo`, or keep separate memories per sensitivity tier.

# Part 2: Puppyone

## Overview

Puppyone bills itself as "context hosting for AI agents, with Git version control and file-level scoped permissions." Rather than indexing past agent conversations (Funes' approach), it ingests **live external data sources** — GitHub, Gmail, Google Drive/Docs/Sheets/Calendar, web pages, databases (Supabase), local uploads — normalizes them into agent-friendly formats (Markdown, JSON, raw files), and serves them to one or more agents through a permissioned, versioned "Context Drive."

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│  Data sources: GitHub, Gmail, Google Drive/Docs/Sheets/    │
│  Calendar, web pages, Supabase DBs, local file uploads     │
└───────────────────────────┬────────────────────────────────┘
                            │  ingest / OAuth connectors
                  ┌─────────▼─────────┐
                  │  Transform layer  │  → Markdown / JSON / raw files
                  └─────────┬─────────┘
                            │
                  ┌─────────▼────────────────────────────┐
                  │  Context Drive                       │
                  │  - Postgres (Supabase): metadata,    │
                  │    permissions, version history      │
                  │  - S3 / MinIO / LocalStack: object   │
                  │    storage for file content          │
                  │  - Redis (ARQ): task queue           │
                  │  File-level versioning, diff,        │
                  │  rollback, checkout/commit + locking │
                  └─────────┬────────────────────────────┘
                            │  File-Level Security (per-agent
                            │  file permissions enforced at
                            │  the filesystem layer)
        ┌───────────────────┼───────────────────┬──────────────────┬─────────────────────┐
        ▼                   ▼                   ▼                  ▼                     ▼
   MCP server           REST API              CLI            Sandbox (Docker/E2B)   Local folder sync
 (Cursor, Claude        (full read/       (`puppyone`        isolated containers      (bidirectional,
  Desktop, Windsurf,     write/query)      command)          w/ authorized files      OpenClaw protocol)
  Cline — auto-                                                mounted
  generated endpoints)
```

### Storage & data format

- **Primary database:** Supabase (Postgres) — metadata, permissions, version history, audit log.
- **Object storage:** AWS S3, MinIO, or LocalStack — actual file content.
- **Task queue:** Redis via ARQ, for async ingest/transform jobs.
- **Versioning:** file-level, with diff comparison, rollback, and a checkout/commit workflow with locking for concurrent edits.
- Content is not a search index over conversation history — it's a live, mutable, version-controlled filesystem-like store that source connectors keep updated.

### Interfaces ("Access Points")

- **MCP** — auto-generated MCP endpoints for Cursor, Claude Desktop, Windsurf, Cline.
- **REST API** — full programmatic read/write/query access.
- **CLI** — Node.js `puppyone` command; `puppyone auth login` supports cloud, local, or custom URLs.
- **Sandbox** — isolated Docker or E2B containers with only the authorized files mounted.
- **Local folder sync** — bidirectional sync via the "OpenClaw" protocol.

### Security posture (as documented upstream)

- **File-Level Security (FLS):** permissions enforced per agent at the filesystem layer — if an agent isn't authorized for a file, the file doesn't exist in its environment (not just access-denied).
- **Audit log:** every read/write operation recorded — who, what file, when.
- **Concurrency control:** version history with conflict detection, checkout/commit + locking.

### Build/install & auth requirements

- **Cloud:** hosted SaaS, sign up at puppyone.ai.
- **Self-hosted:** Docker Compose stack (FastAPI backend, Next.js/React/TypeScript frontend). Optional features need their own keys: agent chat needs `ANTHROPIC_API_KEY`, sandboxing needs `E2B_API_KEY`, plus OAuth app credentials for GitHub/Gmail/Drive connectors.
- **Auth:** Supabase JWT + Access Keys.

# Comparison: Funes vs. Puppyone

They overlap only at "give an agent memory/context via MCP" — the underlying problem, storage model, and trust footprint are quite different.

| | Funes | Puppyone |
|---|---|---|
| What it stores | Past agent session transcripts (retrospective) | Live external data sources + files, agent-scoped (prospective) |
| Storage model | Local disk: Lance columnar dataset | Hosted/self-hosted: Postgres + S3/MinIO + Redis |
| Sharing model | Off by default; explicit `push` to an HF Hub dataset (parquet), created private | Multi-agent/multi-user by default, via permissioned Context Drive |
| Access control | None — it's your own history | File-Level Security: per-agent permissions enforced at the filesystem layer, plus audit log |
| Versioning | Append-only dataset, no per-file diff/rollback | File-level versioning, diff, rollback, checkout/commit + locking |
| Retrieval | Hybrid vector + BM25 search over chunked/embedded turns | Direct file/object access via REST/MCP/CLI/sandbox mount |
| Redaction/security | Local memory stored as written; secrets redacted on push (TruffleHog), which fails closed without it | FLS (files an agent can't see don't exist to it) + full audit trail |
| Infra footprint | Single local binary, no network service required | A hosted service or a Docker Compose stack (DB + object store + queue) |
| Trust footprint | Local-first; only `push` leaves the machine | Data lives in a third-party-hosted (or self-hosted-but-networked) service by design |
| Best fit | "What did I decide last week and why" | "Let this agent see exactly these files/sources, safely, alongside other agents/teammates" |

## Implication for users that need a Shared Memory Feature

- **Funes** is the lower-commitment option: it stays local, requires no new hosted infrastructure, and adds recall over history that already exists on the machine — closer to "enhance what already happens locally" than "add a new system to trust."
- **Puppyone** is a much bigger commitment: it wants to become the permissioned data layer between private docs and potentially multiple agents/tools, routed through a third-party-hosted or self-hosted-but-networked service rather than staying on local disk. Its access-control and audit model is attractive for genuinely multi-agent/multi-teammate scenarios, but it is only worth the materially larger trust and infrastructure footprint if a collaboration problem actually exists.
- If a shared-context need does emerge later, Puppyone's File-Level Security + audit trail model is worth revisiting: it's designed for scoping which agent can see which private file, which Funes has no answer for at all.
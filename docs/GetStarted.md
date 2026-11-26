# Getting started on Windows

Build funes, wire it into Claude Code, index your sessions, and review them in a local page.
Commands are PowerShell unless noted. For what funes is, see the [README](../README.md).

## 1. Prerequisites

| Need | Why | Get it |
| --- | --- | --- |
| Rust (MSVC toolchain) | build funes | <https://rustup.rs>; the repo pins the version in `rust-toolchain.toml` |
| `protoc` | lance compiles protobuf at build time | `winget install Google.Protobuf`, or set `PROTOC` to its full path |
| Git for Windows | the Claude integration's `setup` and hooks are `sh` scripts | <https://git-scm.com/download/win> |
| `jq` | the integration converts Claude's transcripts with it | the `jq-windows-amd64.exe` release from <https://github.com/jqlang/jq/releases> (check it against the release's `sha256sum.txt`) |
| Claude Code CLI (`claude`) | `funes add claude` registers a plugin and an MCP server through it | already on PATH if Claude Code runs from a terminal |
| Python 3 | only for the sessions page (section 7) | standard library only, no packages |

`C:\Windows\System32\bash.exe` is the WSL launcher, not Git Bash. funes runs its integration with
`sh`, so Git's `usr\bin` must come before System32 on `PATH` when you run `funes add`.

## 2. Build

The default `blas` backend has no Windows implementation, so build with `onnx`:

```powershell
cargo build --release --no-default-features --features onnx
```

The binary is `target\release\funes.exe`.

## 3. Put funes and jq on PATH

Use a folder that is already on your `PATH` (`%USERPROFILE%\.local\bin` is, if `claude.exe` lives
there). Hooks and the MCP entry call `funes` by name; a hook that cannot find it skips indexing
and logs `index ABORT: funes not found` in `funes-sync.log`.

```powershell
$bin = "$env:USERPROFILE\.local\bin"
Copy-Item target\release\funes.exe $bin\funes.exe -Force
Copy-Item $HOME\Downloads\jq-windows-amd64.exe $bin\jq-real.exe
```

**jq writes CRLF on Windows**, and the integration's converter expects LF. Save this as `jq` (no
extension) next to `jq-real.exe`:

```sh
#!/bin/sh
exec "$(dirname "$0")/jq-real.exe" -b "$@"
```

Check it: `sh ~/.funes/agents/claude/claude-plugin/funes/test/run.sh` prints
`claude converter: ok` once the integration is installed (step 5).

### Updating funes

Rebuild and replace the binary whenever the source changes. Example: the build that pins the
fastembed cache to `FUNES_HOME` (fixes `.fastembed_cache` appearing in every project folder).

1. **Build** with the same flags as step 2: `cargo build --release --no-default-features --features onnx`.
2. **Test the new binary from `target\release`** before installing: `funes status` and a
   `funes recall "…"`. With the fix, `$FUNES_HOME\cache\fastembed` appears and no `.fastembed_cache`
   is created in the folder you ran it from.
3. **Back up, then install.** Windows lets you rename a running `funes.exe` but not overwrite it, so
   rename the installed one to `funes.exe.prev` (it is your rollback), then copy the new one in.
4. **Restart VS Code** (every window). Each window's `funes mcp` keeps running the old binary until
   it restarts.
5. **Verify:** open a folder you never used with funes, run a recall, and check that no
   `.fastembed_cache` was created there. Then delete the old `.fastembed_cache` folders and
   `funes.exe.prev`.

## 4. Choose where funes stores things

By default funes keeps its state in `~\.funes` and the embedding and reranker weights (about 1.4
GB) in the Hugging Face cache, `~\.cache\huggingface`. Nothing is hard-coded to a drive. To keep
them off the C: drive, set environment variables **before** you index:

```powershell
# D:\funes is only an example: use any folder on the drive you want
[Environment]::SetEnvironmentVariable("FUNES_HOME", "D:\funes", "User")          # funes's state and memory
[Environment]::SetEnvironmentVariable("HF_HUB_CACHE", "D:\funes\hf\hub", "User") # model weights
```

`HF_HUB_CACHE` moves only the model cache, so a token written by `hf auth login` is still found.
It applies to every Hugging Face tool you run, not just funes: they look in the new folder and
download their own models again. If that matters, skip `HF_HUB_CACHE` and leave the weights on C:.

The `onnx` build also keeps fastembed's embedder cache under `FUNES_HOME`, in `cache\fastembed`
(`D:\funes\cache\fastembed` above). Older builds left fastembed's own default, a `.fastembed_cache`
folder in the **current directory**, so every folder Claude Code opened (it starts `funes mcp` there)
got its own copy of the model. If you see `.fastembed_cache` folders in your projects, update funes
(see "Updating funes" under section 3) and delete them.

The agent registry stays at `~\.funes\agents` and the install id at `~\.funes\install-id`
whatever `FUNES_HOME` says. Restart Claude Code (every window) after changing either variable.

To move an existing memory, close Claude Code, make sure no `funes.exe` is running, copy the
`memory` folder plus `state.json` and `index-coverage.json` to the new home, check that
`funes status` shows the same counts, then re-run `funes add claude` so the hooks pick up the new
spool path.

**Cleaning up the old location.** Once `funes status` reads correctly from the new home and nothing
needs the old copy, these are safe to delete from the old folder: `state.json`,
`index-coverage.json`, `store.lock`, and the `cache`, `spool` and `integrations` folders. **Keep**
`agents` (the Claude integration is registered there) and `install-id`. Also safe to remove: a
`funes.exe` you replaced (for example a `funes.exe.old`) and the old copy of funes's English
embedding model under `~\.cache\huggingface` (`models--Xenova--bge-small-en-v1.5`; other tools'
models in that folder are not funes's, so leave them). Close Claude Code and make sure no
`funes.exe` is running first.

Do **not** delete the old `memory` folder if it holds sessions you cannot rebuild (see "Pruning
again later" in section 7). Move it to the new drive as an archive instead, and read it any time
with `funes recall "…" --memory <path to that memory folder>`.

## 5. Add funes to Claude Code

```powershell
# Git for Windows' sh first, wherever Git is installed: git.exe sits in <Git>\cmd
$git = Split-Path (Split-Path (Get-Command git).Source)
$env:PATH = "$git\usr\bin;$git\bin;$env:USERPROFILE\.local\bin;$env:PATH"
funes add claude
```

It installs at **user scope**, so it covers every project you open in VS Code or a terminal; there
is nothing to enable per project. It registers the `funes@huggingface` plugin (per-turn indexing
hooks) and a `funes` MCP server, and converts your existing Claude transcripts into a spool.
Restart Claude Code afterwards. Local only: nothing is sent to the Hub unless you bind a memory.

## 6. Index and check

```powershell
funes index --harness claude --yes     # embeds on the CPU; large histories take a long time
funes status                           # chunks, sessions, chunks awaiting embedding
funes sessions --limit 5               # newest sessions
funes recall "something you worked on" # ranked passages
claude mcp list                        # funes should show Connected
```

An unattended run must name its target, hence `--harness claude`. While chunks are still
"awaiting embedding", recall ranks them poorly; run the index again to continue where it stopped.

## 7. Review and prune sessions: the sessions page

funes cannot delete from a memory. The page lets you review sessions and build a **new** memory
from only the ones you keep, then swap it in.

```powershell
python tools\sessions-ui\server.py        # prints http://127.0.0.1:8765/
python tools\sessions-ui\server.py --port 9000 --funes target\release\funes.exe
```

Run it from the repo root (the folder you built funes in). `--port` and `--funes` are optional:
without `--funes` it uses `funes` from `PATH`, then `%USERPROFILE%\.local\bin\funes.exe`. It also
needs Git for Windows' `sh` (it finds it from `git` on `PATH`), the agent's integration installed
with `funes add`, and the agent's transcripts still on disk. Only Claude Code is supported today
(`--harness claude`, the default), because a rebuild converts the agent's own transcripts again.

To list sessions from a different memory than the live one, for example an old one you are
rebuilding from, add `--source <path to that memory folder>`; builds and swaps still use the live
home. The tool's own tests: `python -m unittest discover -s tools/sessions-ui -v`.

Open the printed address in a browser. Ctrl+C stops the server. It listens on 127.0.0.1 only, and
every call needs a token generated for that run.

**Reviewing.** A **checked** session goes into the new memory; an **unchecked** one is left out of
the rebuild. Nothing is deleted until you build and swap. Filter by text, project, date, or
checked/unchecked, and sort by date or turn count. Click a row for funes's digest of the session.
The "Select all shown" box above the table checks or unchecks every row currently shown, so filter
to a project first to act on just that one. "Uncheck sub-agents" unchecks the `agent-…` sessions that another
session spawned, and "Check everything" resets. Your choices are remembered in the browser, and
"Export lists" saves them as JSON.

### Prune and rebuild, step by step

1. **Get ready.** Close Claude Code in every window: each one runs a `funes mcp` process, and a
   build or swap refuses to run while any `funes.exe` is running. Make sure there is room for a
   second memory next to the live one. If the live memory holds sessions you can't rebuild (see
   "Pruning again later" below), copy it somewhere as an archive first.
2. **Start the page from a terminal**, not from inside Claude Code (it stops when that session
   ends), with `FUNES_HOME` set as Claude Code sees it. Open the printed address. The header says
   where the list comes from and where the new memory will be built; check that it is the drive you
   want.
3. **Uncheck what you want out** (see Reviewing above). Nothing changes yet.
4. **Click "Check what can be rebuilt."** It names every checked session whose Claude transcript is
   gone from `~\.claude\projects`. They can't be in a rebuild: click **Uncheck these N**, then check
   again. The build button stays disabled until this passes.
5. **Click "Export lists"** and keep the file. You need it if you ever re-run `funes add claude`
   (see below).
6. **Click "Build from checked sessions."** It converts the checked transcripts and indexes them
   into a new folder beside your home (`<home>-pruned`); your live memory is not touched. The
   progress panel shows where it is, and a large history can take hours.
7. **When it says Complete, swap.** Type `SWAP` and click the button. The current `memory`,
   `state.json` and `index-coverage.json` are renamed to `*.before-prune-<time>`, not deleted. If a
   rename fails, the swap rolls back and your memory is as it was. The page reports the new chunk
   and session counts.
8. **Reopen Claude Code and verify.** `funes status` should show the session count you expect and
   no chunks awaiting embedding. To confirm an excluded session is really gone,
   `funes get <its id> --from 0 --to 1` reports "no session" (and still reads it with
   `--memory <archive>`). Then run the health check in section 8.
9. **Delete the `before-prune` backups** once you are happy.

**Progress.** While a build runs, the panel above the buttons shows its state (Running, Complete,
Failed), a bar, the current stage (convert, read, embed, compact and search index), elapsed time,
and an estimate of the time left while embedding. The browser tab title also shows the percentage
and switches to "✔ Build complete" when it finishes, so you can leave the tab in the background.
The "Memory status" cards show chunks, sessions, and chunks still awaiting embedding for the live
memory, the archive and the memory being built; "All chunks embedded" means indexing is complete.
The same cards work without a build, to check any memory's index. Reload the page and it picks up
a running build where it was.

### Pruning again later

The page works the same on a memory you already pruned or rebuilt. Start it **without** `--source`
so it lists the live memory. Two things differ from a first prune:

- **The swap leaves a backup.** The live memory exists now, so the swap renames it (and the two
  state files) to `*.before-prune-<time>` in your funes home. Delete those yourself once you are
  happy; until then they take the space of a second memory.
- **Transcripts expire.** A rebuild re-converts Claude's original transcripts, and Claude Code
  removes old ones after its retention period (`cleanupPeriodDays`, 30 days by default). The page
  flags every checked session whose transcript is gone and blocks the build until you uncheck it,
  and an unchecked session is left out of the new memory for good. So a rebuild loses every session
  older than that cutoff. Keep the previous memory as an archive before you swap, and don't delete
  the `before-prune` backup until you have checked what was lost. Raising `cleanupPeriodDays` in
  Claude Code's settings makes transcripts last longer.

A rebuild re-embeds everything it keeps, on the CPU: about 90 minutes for 87,000 chunks at 14 to 18
chunks a second on the author's machine. Afterwards, check that the session count is what you
expect once the next turn has been indexed.

**Do not re-run `funes add claude` after pruning without cleaning up first.** Its setup converts
every transcript the memory doesn't hold into the spool, so the sessions you excluded land there
again and the next index brings them back (on the author's machine, 63 of the 64 files it created
were excluded sessions). If you have to re-run it:

1. On the page, before you swap, click **Export lists** and keep `funes-sessions.json` (the browser
   saves it to your Downloads folder by default).
2. Close Claude Code, then run `funes add claude` from a terminal (section 5).
3. Before Claude Code opens again, delete the excluded sessions' spool files:

   ```powershell
   $funesHome = if ($env:FUNES_HOME) { $env:FUNES_HOME } else { "$env:USERPROFILE\.funes" }
   $drop = (Get-Content "$env:USERPROFILE\Downloads\funes-sessions.json" | ConvertFrom-Json).drop
   $drop | Where-Object { $_ -match '^[A-Za-z0-9._-]+$' } |
     ForEach-Object { Remove-Item "$funesHome\spool\claude\$_.funes.jsonl" -ErrorAction SilentlyContinue }
   ```

4. Open Claude Code. The next turn indexes only what is meant to be there; `funes status` should
   still show the session count you expect.

## 8. Is funes working, and is it worth it?

Two different questions: whether the pipeline is healthy, and whether it earns its keep.

### What to expect

- **Claude can look up your past work.** Claude Code forgets everything between sessions. With
  funes it can search your past sessions, across every project, when something touches an earlier
  decision, bug or dead end, so you don't have to re-explain it.
- **It searches by meaning**, not just exact words, and returns the verbatim passages.
- **It's automatic.** A hook indexes each turn when it ends; nothing is saved by hand.
- **Everything stays on your machine** unless you bind a Hub memory.
- **You can read it yourself** with `funes recall`, or review what's stored on the sessions page.

And the limits:

- **Claude has to choose to use it.** The tools and instructions are in place, but there is no
  guarantee it reaches for `recall` every time.
- **It stores raw transcripts, not summaries.** Recalled passages can be long or noisy. Expect help
  with "what happened and why", not a replacement for a good `CLAUDE.md` for standing rules.
- The costs in latency, privacy and upkeep are in "Is it worth it?" below.

### Health check (one minute)

```powershell
funes status                                   # the memory and its counts
claude mcp list                                # funes should show Connected
Get-Content "$env:USERPROFILE\.funes\agents\claude\claude-plugin\funes\scripts\funes-sync.log" -Tail 6   # what the per-turn hook did last
funes recall "something you know you worked on" --oneline
```

| Check | Good looks like | If not |
| --- | --- | --- |
| `funes status` | the memory path is under your `FUNES_HOME`; chunk and session counts; **no** "awaiting embedding" line; "last indexed" within minutes of your last Claude turn | awaiting embedding: run `funes index --harness claude --yes` again. A note that the integration "does not match this version": run `funes add claude` |
| `claude mcp list` | `funes ... Connected` | restart VS Code from the Start menu (see Troubleshooting) |
| hook log | the newest lines end with `index[claude]: ok`, with a timestamp from your last turn | the log keeps old lines, so judge the newest; a newest `FAILED` is explained by the line above it. Claude runs the hook from the integration's own folder, so that is the log to read; a `funes-sync.log` under `~\.claude\plugins\cache` is a stale copy |
| recall | hits in about ten seconds, from sessions you recognize | no hits: check `funes status` for chunks awaiting embedding, and the `--memory` you are reading |

Growth test: note the chunk count in `funes status`, finish a Claude turn, and check again. It
should go up by a few chunks. If it doesn't, the hook is not indexing.

### Is Claude using it?

funes only helps when Claude chooses to call `recall`. Count the real calls in your Claude
transcripts (the pattern includes `"input"` so it skips the tool's definition, which also appears
in them):

```powershell
Get-ChildItem "$env:USERPROFILE\.claude\projects" -Recurse -Filter *.jsonl |
  Select-String -Pattern '"name":"mcp__funes__recall","input"' | Measure-Object | ForEach-Object Count
```

In a session you can also see it happen: Claude Code shows each `mcp__funes__recall` call. If the
count stays near zero after a couple of weeks of normal work, ask for it explicitly ("check funes
for what we decided about X") or add a line to your `CLAUDE.md` telling Claude to search past
sessions before re-deriving something.

### Known-answer test (does it find the right thing?)

Six questions tell you more than any benchmark:

1. Pick six things you know happened in past sessions: a decision, a bug fix, an approach that
   failed. Write each as the question you would ask later, and note which project holds the answer.
2. For each, run `funes recall "<question>" --oneline` (add `--repo <owner/name>` to confine it to
   that project). Is the right session among the top three hits?
3. Then ask Claude the same question in a fresh session. Did it call `recall`, and was the answer
   right?

| # | Question | Right session in the top 3? | Claude called recall? | Answer correct? |
| --- | --- | --- | --- | --- |
| 1 | | | | |

Four or more of six found is working well. Mostly misses is a reason to check before blaming
funes: chunks still awaiting embedding, a query phrased differently from the session's own words
(rephrase using words the session would use), the session left out when you pruned, or a date
filter (`--since` / `--until`) that excludes it.

### Is it worth it?

| Cost | What to expect | How to check |
| --- | --- | --- |
| Disk | the memory is a few hundred MB (about 385 MB at 87,000 chunks on the author's machine), plus about 1.4 GB of model weights | look at the size of `FUNES_HOME` |
| CPU | a few seconds after each turn to index it; hours for a first large index | timestamps in the hook log |
| Latency | about 8 seconds per recall on the author's CPU; deeper searches (`candidates`) cost more | time a `funes recall` |
| Context | each hit is a few hundred tokens and the default is 8, which Claude reads as part of the session | trim with `-k` or `--oneline` |
| Privacy | raw transcripts, secrets included, unencrypted | see Privacy and security below |
| Upkeep | this port is a fork, so every upstream update means re-applying the Windows changes | the size of that diff against upstream |

Against that, the benefit is that Claude can find an earlier decision, bug or dead end across all
your projects without you re-explaining it. It is worth keeping if the known-answer test passes
and you see Claude reach for `recall` on its own a few times a week. If it rarely does, or recall
mostly returns noise, the cost is not paying for itself yet. Before giving up, check whether your
pruning removed what you were looking for (an archived memory can be read with `--memory <path>`).

## 9. Does funes forget old sessions?

No. funes is deliberately **append-only**: every passage is stored once with a timestamp and is
never overwritten, summarized or expired. A session nobody has referenced in six months is indexed
exactly like one from yesterday. The reasoning is that a memory which decides what to forget can
silently discard the right answer, and an old passage is often the answer ("what did we try
before, and why did we move off it?").

- **Age doesn't change the ranking.** Recall ranks by how well a passage matches the question. It
  neither favors recent passages nor penalizes old ones. When something changed over time you may
  see both the old and the new passage, and you or Claude judge which is current. For recent
  material only, `recall` takes `--since` and `--until` (`YYYY-MM-DD`).
- **What does remove things:** pruning (section 7) leaves sessions out of a rebuilt memory, and
  `funes scrub` redacts secrets in place. Claude Code's own transcript cleanup does **not** remove
  anything from the funes memory, which holds its own copy of each indexed session. But a transcript
  that is gone can never be converted again, which is why a rebuild can't include it.
- **It only keeps what was indexed.** The hook captures each turn from the moment funes is
  installed; earlier history is there only if the first index covered it.
- **The index only grows.** Recall time rises slowly with the number of chunks, and funes compacts
  the memory as it indexes. On the author's machine, 87,000 chunks take about 385 MB. If it ever
  gets noisy or slow, pruning (section 7) is the way to trim it.

## 10. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `funes` is not recognized, or the MCP server shows "Connection closed" | `funes.exe` is not in a `PATH` folder (step 3), or Claude Code was not restarted |
| `funes-sync.log` says `index ABORT: funes not found` | same; the hook cannot see the binary |
| `%1 is not a valid Win32 application` from `funes add` | an old build that ran `setup` directly; rebuild (step 2) so it runs through `sh` |
| `the jq on PATH does not convert Claude Code's transcripts…` | jq is writing CRLF; add the `jq` wrapper (step 3) |
| `funes add` fails under WSL or cannot find `sh` | put Git's `usr\bin` first on `PATH` (step 5) |
| `automated funes index needs a target` | add `--harness claude` |
| `trufflehog not found` on `funes push` | publishing needs `trufflehog.exe` on `PATH` or `FUNES_TRUFFLEHOG`; local use does not |
| `funes status` shows old counts after a fresh install | indexing has not run; do step 6 |
| `.fastembed_cache` folders appear in project folders | a funes built before the cache was pinned; update it (step 3, "Updating funes"), then delete the folders |
| Build fails with stale-metadata or "can't find crate" errors | a cargo run was killed mid-build; delete `target\debug` and rebuild |

## Privacy and security

The converter reads every Claude transcript, and the memory stores them as written, including
anything sensitive pasted into a session. The local memory is not encrypted. Hub sync is off unless
you bind a memory (every prompt defaults to no), repos funes creates are private, and a push
redacts secrets only with TruffleHog installed. The integration is code from
`huggingface/funes-integrations` that runs with your user rights; review it before trusting it.

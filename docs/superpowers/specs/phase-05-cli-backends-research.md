# Subscription-login CLI backends for rpipe (research, 2026-11-21)

> **Phase 5** · Status: research note · Roadmap: [ROADMAP.md](../ROADMAP.md)

Legend: [OFFICIAL] = official docs fetched; [SECONDARY] = forum/blog/search snippet; [VERIFIED] = run on this machine.

## 1. Comparison table

| Criterion | Claude Code `claude -p` | Codex CLI `codex exec` | Antigravity `agy -p` | Gemini CLI `gemini -p` | Copilot CLI `copilot -p` | Cursor `cursor-agent -p` | Kiro CLI `kiro-cli chat --no-interactive` |
|---|---|---|---|---|---|---|---|
| 1 Headless / no TTY | Works [VERIFIED] | Documented headless; read-only sandbox default [OFFICIAL]; not installed here, untested | Historically broken in non-TTY. #76 closed "fixed in 1.1.1" (stdin no longer read when prompt is a flag; errors to stderr, nonzero exit). #318 closed as "obsolete" but users still report hangs on 1.2.0, 1.2.3 (Linux) and 1.4.10 (Windows): model finishes, print mode never completes. Local agy is 1.5.14; NOT verified (test denied, see section 4) [SECONDARY, GitHub issues] | Works, but see "consumer access moved" below | Works [OFFICIAL] | Works [OFFICIAL] | Works, needs API key [OFFICIAL] |
| 2 Structured output | `--output-format json --json-schema '<inline JSON>'`; result in `.structured_output` (and `.result` string); field absent if no valid output [OFFICIAL+VERIFIED]. Stdin prompt OK (cap 10 MB). `--system-prompt` replaces, `--append-system-prompt` adds | `--output-schema <file>` + `-o <file>` (last message to file; also printed to stdout). Prompt on stdin if no arg or `-` [OFFICIAL]. No system-prompt flag found (use `-c` config / put instructions in prompt) | `--json-schema <str\|file>` + `--output-format json`; result in `.structured_output`, `.response` is string [OFFICIAL]. Plain-text stdin is NOT documented for `-p`; prompt is an arg (Linux single-arg limit 128 KiB, a 30K-token prompt is ~100-130 KB = at/over limit). Use `--input-format stream-json` (NDJSON on stdin, `{"event":"user","message":{"content":"..."}}`). `-p` greedily eats the next token as the prompt: `agy -p --output-format json` fails (exit 2) [VERIFIED]. System prompt: no flag (`--agent` only) | `--output-format json` gives `{response, stats, error?}`; no schema flag [OFFICIAL] | No schema flag found; `--output-format json` (JSONL) [OFFICIAL]. Piped stdin ignored if `-p` given | `--output-format json`; no schema flag documented [OFFICIAL]; stdin undocumented | `--output-format stream-json` only; no schema; prompt on stdin supported [OFFICIAL] |
| 3 Safety | `--tools ""` removes ALL built-in tools [VERIFIED]; plus `--permission-mode dontAsk`, `--setting-sources ""`, `--no-session-persistence`, `--strict-mcp-config`. `--restricted` also exists. Run in empty tmp cwd | Default read-only sandbox; `--sandbox read-only`, `-C <empty dir>`, `--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`. Cannot fully remove tools; web search default "cached"; no documented off switch [OFFICIAL] | No "no tools" flag found. `--mode plan`, `--sandbox`, default soft-denies unapproved actions (run continues, exit 0, notice on stderr). Shell default = Ask. `permissions.allow` rules in `~/.gemini/antigravity-cli/settings.json`. Weaker guarantee than Claude [OFFICIAL] | n/a | `--available-tools` (allowlist, can be empty-ish), `--excluded-tools`, `--deny-tool`; unattended unapproved requests denied [OFFICIAL] | Unapproved actions rejected in print mode without `--force`; `permissions.deny`; sandbox [OFFICIAL] | `--trust-tools` allowlist; no trust = tool calls denied; `--sandbox` [OFFICIAL] |
| 4 Subscription auth | `claude auth status` (JSON; exit 0 logged in, exit 1 not logged in, both [VERIFIED]). Unauth in -p: error printed as result on stdout ("Not logged in · Please run /login") [OFFICIAL]. **Do not use `--bare`: it ignores OAuth/keychain, API key only** | Saved ChatGPT login reused by default [OFFICIAL]. `codex login status` exits 0 when logged in (non-zero value unspecified). OpenAI says API key is "the right way to authenticate automation"; ChatGPT-managed auth is "advanced workflow for enterprise and other trusted private automation" [OFFICIAL] | Cached credentials from interactive `agy` login; unauth headless "exits with an `authentication required` error instead of hanging" [OFFICIAL]. No login-status subcommand found in `--help` (use `agy models` or a canary) | Consumer access ended | `COPILOT_GITHUB_TOKEN`/`GH_TOKEN`/`GITHUB_TOKEN` (token, from a Copilot-entitled GitHub account) [OFFICIAL] | `CURSOR_API_KEY` or `login`; admins can disable headless [OFFICIAL] | Requires `KIRO_API_KEY`, available only to Kiro Pro/Pro+/Pro Max/Power subscribers [OFFICIAL]. Not an interactive-login flow |
| 5 Limit signalling | `-p json`: `is_error`, `api_error_status`, `result` text e.g. "You've hit your session limit" / "weekly limit"; stream-json emits `system/api_retry` with `error: rate_limit` [OFFICIAL]. Exact exit code for limit not verified | Reports of "You've hit your usage limit" (community). Event types `turn.failed`/`error` with `--json` [OFFICIAL]. Exit code undocumented | Docs: quota behavior NOT documented. Envelope `status` (SUCCESS/ERROR/...) + `error`; nonzero exit on failure since 1.1.1; "heavy automated batching may hit rate limits" [SECONDARY] | exit 1 general/API, 42 input, 53 turn limit | not documented | not documented | `runError` record in stream-json; exit codes page not read |
| 6 Model flag | `--model haiku\|sonnet\|opus\|full-name` [VERIFIED haiku -> claude-haiku-5-5]; `--fallback-model`, `--effort` | `-m/--model` | `--model <slug>` from `agy models` (Gemini 3.8 Flash, Claude Sonnet/Opus 5.5, GPT-OSS 120B) [VERIFIED list]; `--effort` | `-m` | `--model` | yes (page didn't list) | `--model`, `--effort` |
| 7 ToS for scripted personal use | Consumer Terms apply to Pro/Max. "Advertised usage limits ... assume ordinary, individual usage of Claude Code and the Agent SDK." OAuth "designed to support ordinary use of Claude Code"; forbids third-party developers routing requests through plan credentials on behalf of users or collecting/storing credentials [OFFICIAL]. A personal timer calling the unmodified binary on your own login is within the text; shipping rpipe to others is fine if each user runs their own login and rpipe never touches tokens. Billing: Anthropic announced then PAUSED (June 15, 2026) moving `claude -p`/SDK usage off subscription limits; currently still draws from plan limits, but this could return [OFFICIAL support article, ambiguous] | OpenAI frames ChatGPT-login automation as advanced/trusted-private; no explicit prohibition found. ToS text not read [OFFICIAL + gap] | FAQ: "`agy -p` is intended for direct scripting and CI workflows. Wrapping `agy` inside third-party coding agents or applications, or automating account rotation, is not supported under consumer plans." Staff forum reply: launching the unmodified binary as a local child process does not violate clause 6 if the orchestrator never reads/stores OAuth tokens; but "unattended autonomous polling loops" and wrapping in a third-party agent are listed as restricted [OFFICIAL FAQ + SECONDARY forum]. A nightly systemd job is grey: it is unattended scripting, not polling, but rpipe is arguably "another application" | n/a | GitHub terms, not read | not read | not read |
| 8 Install / OS | npm/native installer; Linux, macOS, Windows | npm/brew; Linux, macOS, Windows (WSL experimental) | `agy` binary; Linux/macOS/Windows | npm | npm; Linux/macOS/Windows | curl installer; Linux/macOS/Windows | Kiro CLI 2.0, Linux/macOS/Windows |

Dropped: **Gemini CLI** for subscription use. Its README: "Free individual access and Google AI Pro / Ultra (Google One) moved to Antigravity CLI on June 18, 2026"; Gemini CLI is now Enterprise Code Assist (and legacy API/Vertex keys) only [OFFICIAL]. Copilot, Cursor, Kiro: no JSON-schema flag, so output would be prompt-only JSON validated by Pydantic with retries; Kiro and Cursor lean on API keys. Amazon Q Developer CLI is superseded by Kiro CLI. Not researched further: obscure others.

## 2. Per-tool detail and adapter command lines

### Claude Code (recommended first)
```
cd "$(mktemp -d)"            # empty cwd
claude -p \
  --model haiku \                       # or sonnet
  --tools "" \                          # no Bash/Read/Write/Web
  --permission-mode dontAsk --permission-prompts none \
  --setting-sources "" --strict-mcp-config \
  --no-session-persistence \
  --system-prompt "$SYSTEM" \           # fully replaces default prompt
  --output-format json \
  --json-schema "$SCHEMA_JSON_STRING" \
  < prompt.txt > out.json               # prompt on stdin
```
Parse: `r=json.loads(stdout)`; require `not r["is_error"]` and `"structured_output" in r`; validate with Pydantic. `r["total_cost_usd"]`, `r["modelUsage"]`, `r["usage"]` for telemetry. Observed: structured output is delivered via an internal tool call (`stop_reason":"tool_use"`, `num_turns` 2), costs one extra turn. Login check: `claude auth status` (exit 0/1). Do NOT pass `--bare` (API key only). SIGTERM exits 143. Stdin cap 10 MB, plenty for 30K tokens. Caveat: `--system-prompt-snapshot` default on records the prompt per conversation; combined with `--no-session-persistence` irrelevant for one-shots. Untrusted text goes in the user prompt on stdin; with no tools there is no action surface (worst case: bad JSON content, so keep Pydantic validation and length limits).

### OpenAI Codex CLI (second)
```
cd "$(mktemp -d)"
codex exec --sandbox read-only --skip-git-repo-check --ephemeral \
  --ignore-user-config --ignore-rules -C "$PWD" -m <model> \
  --output-schema schema.json -o result.json - < prompt.txt
```
Result in `result.json` (last message, JSON text matching schema); stdout also has it. Use `--json` only if you need events/`turn.failed`. Caveats: cannot turn tools off (read-only sandbox + empty dir is the mitigation; prompt injection could still read files outside cwd if sandbox allows reads: `read-only` permits reading the filesystem, so personal files on disk are readable by the model, worth a note); web search cached by default; system prompt must go in the prompt; OpenAI recommends API keys for automation; exit codes and limit signalling undocumented, so adapter must treat nonzero exit or missing/invalid `result.json` as failure and sniff stderr for "usage limit". Not installed here, nothing verified locally. Note: `--output-schema` for OpenAI requires strict-mode-compatible schemas (additionalProperties:false, all properties required); [SECONDARY/prior knowledge].

### Antigravity `agy` (experimental, third)
```
cd "$(mktemp -d)"
printf '%s\n' "$NDJSON_USER_LINE" | agy --print --input-format stream-json \
  --output-format stream-json --json-schema "$SCHEMA" \
  --model gemini-3.8-flash-low --mode plan --sandbox \
  --print-timeout 300s --log-file /tmp/agy.log > out.ndjson
```
(Per docs, `stream-json` input requires `stream-json` output; final `result` event has the same shape as the json envelope, including `structured_output`.) Simple alternative for small prompts: `agy --print="$PROMPT" --output-format json --json-schema "$SCHEMA" ...` (note the `=`; `-p` swallows the next token). Always pass stdin=DEVNULL for the arg form, and wrap in an external `timeout` because `--print-timeout` has been reported not to bound some hangs (#318 comments). Risks: unresolved hang reports on current versions, no true tool-off flag, ToS grey area for third-party wrapping, quota behavior undocumented, no login-status command.

### Gemini CLI: not viable on consumer subscriptions since 2026-06-18 (see table). Exit codes if ever used: 0, 1, 42, 53.

### Copilot CLI / Cursor / Kiro (later, prompt-only JSON)
Copilot: `copilot -p "$PROMPT" -s --model <m> --available-tools "" --no-ask-user` (idea; flags from docs, not tested; prompt via arg, stdin ignored with -p). Cursor: `cursor-agent -p --output-format json`; no schema, API-key oriented. Kiro: `kiro-cli chat --no-interactive --output-format stream-json --model <id> < prompt`; needs a Kiro-issued API key.

## 3. Risks
- Anthropic billing policy: the "Agent SDK / claude -p credits" split was announced then paused (June 15, 2026 note); it could return, after which unattended `claude -p` might stop counting against the plan or require credits. Pipeline should detect `is_error` + "limit"/"credit" and stop cleanly.
- Anthropic and Google both prohibit pooling/rotating accounts and credential extraction. rpipe must never read OAuth tokens or store them; call the unmodified binary under the user's own login.
- 30 calls x ~30K tokens/night is "ordinary individual" scale but heavy on Pro; use haiku/flash-class for first-pass summaries.
- Linux per-argument limit 128 KiB (MAX_ARG_STRLEN; total ARG_MAX here 2,097,152): never pass 30K-token prompts as argv; use stdin (Claude, Codex, Kiro) or agy stream-json.
- Structured output is a request, not a hard guarantee in all tools: always validate with Pydantic, retry once, then record failure.
- Prompt injection: only Claude `--tools ""` gives a verifiably tool-less run.

## 4. Verified test results (this machine)

Claude Code 2.1.330, cwd `.../scratchpad/clitest/claude/` (empty except prompt file), stdin from pipe, stdout to file:
```
cat p.txt | timeout 120 claude -p --tools "" --output-format json \
  --json-schema '{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}' \
  --system-prompt "You return JSON only." --setting-sources "" --no-session-persistence \
  --permission-mode dontAsk --model haiku > out.json 2> err.txt
```
Exit 0, wall 1.76 s, stderr empty. stdout (trimmed): `"is_error":false,"subtype":"success","result":"{\"ok\":true}","structured_output":{"ok":true},"num_turns":2,"stop_reason":"tool_use","total_cost_usd":0.0002562,"modelUsage":{"claude-haiku-5-5":...},"terminal_reason":"completed","permission_denials":[]`. Works from non-TTY, no API key, uses plan login. Also: `claude auth status` exit 0 (logged in, claude.ai/pro); with `CLAUDE_CONFIG_DIR=<empty dir>` it prints `loggedIn:false` and exits 1.

agy 1.5.14: `agy models` listed 22 models (Gemini 4/3.8, Claude 5.5, GPT-OSS). First attempt `echo ... | agy -p --output-format json ...` exited 2 in 0.07 s with "-p took --output-format as its prompt" (no model call). My single real test call (stream-json stdin variant, `--mode plan`) was BLOCKED by the Claude Code auto-mode classifier ("Destructive or Unwanted Actions"), so agy headless/non-TTY behavior was NOT verified here; I did not retry or work around it. Needs the user's approval or running it themselves:
```
printf '%s\n' '{"event":"user","message":{"content":"Reply with exactly {\"ok\": true}"}}' | timeout 120 agy --print --input-format stream-json --output-format stream-json --json-schema '<schema>' --model gemini-3.8-flash-low --mode plan > out.json
```

## 5. Recommendation
1. Support **Claude Code** first: only tool that is verified here, has a real schema flag, true no-tools switch, stdin prompt, system-prompt replacement, a login-status command, JSON error fields, and a cheap model alias. Cost: one policy risk (plan billing for `-p` may change).
2. **Codex exec** second: documented `--output-schema`/`-o`, stdin, read-only sandbox; weaker on tool-off and error signalling, and OpenAI nudges automation to API keys.
3. **agy** as experimental/opt-in behind a health canary and external timeout, until the non-TTY hang is confirmed fixed on 1.5.14 and the consumer-plan wrapping language is clarified.
4. Skip Gemini CLI (consumer access ended), defer Copilot/Cursor/Kiro (no schema flag; API-key flows). Design the adapter interface (`run(system, user, schema) -> dict`, `check_login()`, `LimitReached` exception) so these can be added later with prompt-level JSON + validation retry.

## Sources (opened)
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/legal-and-compliance
- https://code.claude.com/docs/en/errors.md
- https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- https://learn.chatgpt.com/docs/non-interactive-mode (redirect from developers.openai.com/codex/noninteractive)
- https://learn.chatgpt.com/docs/developer-commands
- https://learn.chatgpt.com/docs/auth/ci-cd-auth
- https://github.com/openai/codex/blob/main/docs/exec.md
- https://antigravity.google/docs/cli/headless.md
- https://antigravity.google/docs/faq.md
- https://github.com/google-antigravity/antigravity-cli/issues/76
- https://github.com/google-antigravity/antigravity-cli/issues/318
- https://discuss.ai.google.dev/t/is-external-orchestration-of-antigravity-cli-headless-mode-supported-with-account-based-usage/183051
- https://discuss.ai.google.dev/t/is-it-allowed-for-an-open-source-tool-to-call-the-official-agy-cli-in-headless-mode/183429
- https://geminicli.com/docs/cli/headless.md
- https://github.com/google-gemini/gemini-cli
- https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically
- https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference
- https://cursor.com/docs/cli/headless
- https://kiro.dev/docs/cli/headless/
- Search-result only (secondary): forum threads on agy ToS (discuss.ai.google.dev, Staff quotes via search summary), Codex usage-limit community threads (community.openai.com), Claude Agent SDK credit coverage (dev.to, datacamp).
- Not available (404): https://antigravity.google/docs/cli/permissions.md

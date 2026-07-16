# VS Code Codex Diagnostic Report

Date: 2026-07-15

## Executive Summary

The Codex VS Code extension is installed, network-reachable, and able to start its local backend. The original VS Code profile held a stale Codex backend/session state that was not recoverable through extension reinstall alone.

The practical repair is complete: Codex was signed out and back in through a fresh OAuth flow, and a persistent isolated VS Code profile was created at `~/.config/Code-Codex`. That profile starts a fresh Codex backend with the renewed credentials and successfully completed an end-to-end no-operation prompt test.

The missing `rg` executable was a separate, non-fatal Codex doctor warning. It has been repaired with a user-local installation of ripgrep 15.1.0.

## Environment

| Component | Observed value |
| --- | --- |
| Operating system | Debian 13.0 (trixie), x86_64 |
| VS Code | 1.128.1, stable |
| Codex extension | `openai.chatgpt` 26.707.71524, Linux x64 |
| Codex CLI bundled with extension | 0.144.2 |
| Codex preview tested | 26.5707.71524, Linux x64 |
| Workspace used for controlled tests | `ghostfolio-custom` |

## What Was Verified

The Codex CLI diagnostic passed the following checks:

- ChatGPT authentication is configured and valid.
- Codex local SQLite state databases are healthy.
- The OpenAI websocket connection completed with HTTP 101.
- The active provider endpoint was reachable over HTTPS.
- The Codex extension package was complete and contained its expected native binaries.
- VS Code loaded and activated `openai.chatgpt` when the Codex view was opened.
- Codex spawned `app-server` and received its initialization response.

The extension log repeatedly contained the successful startup sequence:

```text
[CodexMcpConnection] Spawning codex app-server
[CodexMcpConnection] Initialize received id=1
```

This sequence was present in the original profile as far back as 2026-07-12, so the behavior predates the manual reinstallation.

## Root Cause And Working Fix

The original VS Code profile had a stale Codex extension-host/backend state. It continued to use an old in-memory OAuth session even after Codex was reauthenticated. When that stale backend next refreshed its available models, it reported:

```text
401 Unauthorized
auth error code: token_revoked
```

The native Chat Session UI then had no usable Codex model registration and logged the following symptoms:

```text
defaultModel=undefined
_currentLanguageModel is undefined
vscode-chat-session://local/...
```

At the same time, Codex repeatedly logged:

```text
[IpcClient] Received broadcast but no handler is configured method=client-status-changed
```

The original profile cannot safely reload an external OAuth change while its old app-server remains in memory. It must be fully restarted to create a fresh backend. A clean, persistent profile was tested with the refreshed OAuth session, where Codex initialized normally and returned `READY` to a prompt explicitly forbidding file reads, edits, and commands.

The available Codex preview build was also tested. It did not provide a separate fix for the native missing-model symptom, so the durable workaround is the working isolated profile and launcher described below.

### Restart Follow-Up

After a full ordinary VS Code restart, the original profile did create a fresh Codex backend with the renewed OAuth session. It still activated Codex through VS Code's native `onChatSession:openai-codex` route and remained stuck loading. Its dedicated-sidebar backend also continued to report a model-refresh timeout. This confirms that the ordinary profile's native Chat state remains unhealthy even after authentication is repaired.

## Items Ruled Out

- OpenAI network or websocket connectivity
- Corrupt Codex local databases
- Incomplete Codex extension installation
- A stale Codex VS Code state value; the stored `openai.chatgpt` value is an empty persisted-state container
- Project-specific configuration in either workspace
- Missing ripgrep as the cause of the permanent chat spinner

## Ripgrep Repair

Codex doctor originally reported:

```text
search command could not be verified
search command rg
search provider system
```

The extension already included a valid ripgrep binary. It was installed without sudo to:

```text
~/.local/bin/rg
```

Validation after installation:

```text
ripgrep 15.1.0 (rev af60c2de9d)
Codex doctor: search ripgrep 15.1.0 (system, `rg`)
```

The currently running GUI Code process was started with a PATH that did not contain `~/.local/bin`. Fully close VS Code and relaunch it to allow the extension host to inherit the repaired PATH. Launching once from a terminal is the most reliable option:

```bash
code
```

## Recommended Resolution For The Chat Spinner

Use the new persistent Codex profile immediately:

```bash
code-codex
```

It is also registered in the desktop application menu as **VS Code - Codex**.

It uses:

```text
VS Code user data: ~/.config/Code-Codex
Extensions: ~/.vscode/extensions
```

It shares the refreshed Codex OAuth login but isolates VS Code profile state. It was validated end-to-end with this response:

```text
READY
```

The ordinary VS Code profile is still configured to focus the dedicated Codex sidebar at startup. A future VS Code or Codex update may allow it to recover, but use **VS Code - Codex** for reliable access today.

If you want to retry the ordinary profile after a future update, fully close all ordinary VS Code windows and launch it again from a terminal:

```bash
code
```

This is required because its stale app-server process cannot adopt the refreshed OAuth token dynamically.

The bundled Codex CLI remains available as an alternative:

```bash
~/.vscode/extensions/openai.chatgpt-26.707.71524-linux-x64/bin/linux-x86_64/codex
```

## Upstream Bug Report Template

```text
Platform: Debian 13, Linux x64
VS Code: 1.128.1 stable
Codex extension: openai.chatgpt 26.707.71524
Preview tested: 26.5707.71524

The original VS Code profile starts Codex app-server and initializes it, but its
native chat session has no usable model and logs defaultModel=undefined and
_currentLanguageModel is undefined. After OAuth renewal, the old in-memory backend
reports 401 token_revoked for /backend-api/codex/models and cannot recover without
restarting VS Code. A clean persistent VS Code profile with the same extension and
renewed OAuth login works and returns a response through the Codex composer.
```

## Changes Made

- Installed `rg` at `~/.local/bin/rg` from the Codex extension's bundled ripgrep binary.
- Signed Codex out and completed a new ChatGPT OAuth login.
- Created the working persistent Codex profile at `~/.config/Code-Codex`.
- Added the `code-codex` launcher at `~/.local/bin/code-codex`.
- Added the **VS Code - Codex** desktop launcher at `~/.local/share/applications/code-codex.desktop`.
- Configured the ordinary VS Code profile to focus the dedicated Codex sidebar on startup.
- Added this diagnostic report.

No workspace source code or permanent credentials were exposed or manually edited.
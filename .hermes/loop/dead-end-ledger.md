# Dead End Ledger — GetAJob Production Loop

## [2026-06-24] - Claude Code through proxy times out on multi-file prompts
- **Attempted:** Run Claude Code `-p "..."` with long prompts covering multiple files
- **Why it failed:** The claude-code-proxy (OpenCode) has a ~600s timeout. Prompts that require reading 5+ files and modifying 3+ files hit this limit consistently.
- **Evidence:** Multiple timeout errors in earlier attempts (terminal exit code -15 or empty output after 600s)
- **Lesson:** Break work into the smallest possible increments. One file change per Claude Code call. Pre-read files with Read tool before asking Edit tool. Use shell heredoc for code-heavy prompts.

## [2026-06-24] - Raw `-p` argument with shell-special characters
- **Attempted:** `claude -p "self._tailoring: TailoringAgent | None = None"`
- **Why it failed:** Bash interprets `::` and `|` and `()` as shell syntax, not literal text
- **Evidence:** Bash errors: "command not found" on `::`, pipe errors on `|`
- **Lesson:** Write prompts to a temp file and use `cat prompt.txt | claude -p -` or `claude -p "$(cat prompt.txt)"`. For short prompt fragments, avoid `|`, `::`, `()`, `;`, and backticks.

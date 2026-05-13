# memoryd OpenClaw plugin

Captures every `agent_end` turn into the local memoryd data root, tagged
`source: openclaw`. Co-installed alongside the CC SessionEnd hook + Codex
Stop hook so all three clients share a single Markdown scope.

## Install (one-time)

```bash
cd /path/to/project-management-personal/scripts/openclaw-memoryd-plugin
openclaw plugins install --force .
```

Grant the two hook permissions OpenClaw requires:

```bash
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowConversationAccess true
openclaw config set plugins.entries.memoryd-openclaw.hooks.allowPromptInjection false
```

(We only need conversation read access; we do NOT inject prompts.)

## Verify

After your next OpenClaw turn ends, check:

```bash
ls ~/.local/share/memoryd/scopes/*/sessions/
cat ~/.local/share/memoryd/logs/openclaw-agent-end.log
```

The log should show `ok`; the markdown's frontmatter should include
`source: openclaw`.

## Run tests

```bash
npm test
```

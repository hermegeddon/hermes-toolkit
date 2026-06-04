---
name: hermes-skill-authoring
description: >-
  How to author and edit skills FOR Hermes Agent (NousResearch) — the Hermes
  SKILL.md frontmatter contract, body structure, conditional activation, env vars
  and credential files, template tokens, inline shell, media delivery, and
  publishing. Use this skill WHENEVER creating, editing, reviewing, or debugging a
  Hermes skill, or when a user says "write/make/fix a Hermes skill", "add a
  capability to my Hermes agent", or references SKILL.md in a Hermes context. Note
  the Hermes SKILL.md format is DIFFERENT from the Claude/Anthropic Agent Skills
  format — this skill covers the Hermes one.
---

# Authoring Hermes Skills

> **Two SKILL.md dialects — don't mix them.** The file *you are reading* is a
> **Claude/Anthropic Agent Skill** (frontmatter: just `name` + `description`).
> A **Hermes skill** is a different artifact with a richer frontmatter
> (`version`, `author`, `license`, `platforms`, `metadata.hermes`, …) consumed by
> the Hermes runtime. When you write a skill to install into Hermes, use the
> **Hermes** format documented below — not this file's format.

## Skill or tool?

Make a **skill** when the capability is instructions + shell + existing Hermes
tools (`terminal`, `web_extract`, `read_file`), wrapping a CLI/API, no baked-in
keys. Make a **tool** when it needs end-to-end key/auth integration, must execute
precise logic every time, or handles binary/streaming/realtime. Most extensions
are skills.

## Directory layout

```
my-skill/
├── SKILL.md            # required: the instructions
├── scripts/            # optional: helper scripts (parsers, API calls)
└── references/         # optional: docs loaded on demand
```

## SKILL.md frontmatter (Hermes)

```yaml
---
name: my-skill
description: Brief description (shown in skill search results)
version: 1.0.0
author: Your Name
license: MIT
platforms: [macos, linux]            # optional; omit = all platforms
metadata:
  hermes:
    tags: [Category, Subcategory, Keywords]
    related_skills: [other-skill]
    requires_toolsets: [web]         # hide unless this toolset is active
    requires_tools: [web_search]     # hide unless this tool exists
    fallback_for_toolsets: [browser] # hide WHEN this toolset is active
    fallback_for_tools: [browser_navigate]
    config:                          # non-secret settings -> config.yaml
      - key: my.setting
        description: "What it controls"
        default: "sensible-default"
        prompt: "Display prompt for setup"
required_environment_variables:      # secrets -> ~/.hermes/.env (never shown to model)
  - name: MY_API_KEY
    prompt: "Enter your API key"
    help: "Get one at https://example.com"
    required_for: "API access"
required_credential_files:           # OAuth token files, certs, service-account JSON
  - path: google_token.json
    description: "OAuth token (created by setup script)"
---
```

### Conditional activation (controls prompt visibility)

| Field | Skill is hidden when… |
|---|---|
| `requires_toolsets` | any listed toolset is **not** available |
| `requires_tools` | any listed tool is **not** available |
| `fallback_for_toolsets` | any listed toolset **is** available |
| `fallback_for_tools` | any listed tool **is** available |

Pattern: a `duckduckgo-search` skill with `fallback_for_tools: [web_search]` only
appears when the API-key-backed `web_search` is absent.

### Secrets vs config

- `required_environment_variables` → **secrets** in `~/.hermes/.env`; auto-passed
  through to `terminal`/`execute_code` sandboxes (incl. Docker/Modal) when set; the
  raw value is never exposed to the model. Missing values don't hide the skill —
  Hermes prompts for them at load time in the local CLI (gateway/messaging sessions
  show setup guidance instead of collecting secrets in-band).
- `metadata.hermes.config` → **non-secret** paths/preferences in `config.yaml` under
  `skills.config.<key>`; discovered via `hermes config migrate`; injected into the
  skill message at load as `[Skill config: my.setting = …]`.

## SKILL.md body — recommended structure

```markdown
# Skill Title
Brief intro.

## When to Use
Trigger conditions — when should the agent load this skill?

## Quick Reference
Table of the common commands / API calls.

## Procedure
Numbered steps the agent follows.

## Pitfalls
Known failure modes and how to handle them.

## Verification
How the agent confirms it worked.
```

### Body mechanics

- **Template tokens** substituted at load: `${HERMES_SKILL_DIR}` (absolute skill
  dir — use it to invoke bundled scripts with no path math) and
  `${HERMES_SESSION_ID}`. Disable globally with `skills.template_vars: false`.
  ```markdown
  To analyse the input, run:
      node ${HERMES_SKILL_DIR}/scripts/analyse.js <input>
  ```
- **Inline shell** ``!`cmd` `` injects a command's stdout into the message at load
  (e.g. `Current date: !`date -u +%Y-%m-%d``). **Off by default** and runs on the
  host without approval — only enable (`skills.inline_shell: true`) for trusted
  skill sources.
- **`[[as_document]]`** anywhere in a response makes the gateway deliver extracted
  media (hi-res screenshots, charts) as downloadable file attachments instead of
  lossy inline previews.

## Guidelines

- **No external deps** by default — prefer stdlib Python, `curl`, and existing
  Hermes tools. If a dep is unavoidable, document install steps in the skill.
- **Progressive disclosure** — most common workflow first; edge cases at the
  bottom; keeps token cost low for the common path.
- **Helper scripts** — for XML/JSON parsing or fiddly logic, ship a script in
  `scripts/` rather than asking the model to write a parser inline each time.

## Test it

```bash
hermes chat --toolsets skills -q "Use the X skill to do Y"
```

For systematic, repeatable verification (multiple prompts, pass/fail, regression
vs baseline), use the `hermes-eval-harness` skill instead of eyeballing one run.

## Where it lives / publishing

- Broadly useful → bundle in `skills/`. Official-but-niche → `optional-skills/`.
  Specialized/community → a Skills Hub (`agentskills.io`) or your own tap.
- Publish: `hermes skills publish skills/my-skill --to github --repo owner/repo`.
- Add a source: `hermes skills tap add owner/repo`; install: `hermes skills install`.
- Hub-installed skills are security-scanned (exfiltration, prompt-injection,
  destructive/shell-injection). Trust levels: `builtin` / `official` / `trusted`
  (openai, anthropics, huggingface skills) / `community`.

## Authoring checklist

1. Decide skill vs tool.
2. Write frontmatter; add `requires_*`/`fallback_*` so it only shows when relevant.
3. Declare secrets as `required_environment_variables`, non-secrets as `config`.
4. Body: When to Use → Quick Reference → Procedure → Pitfalls → Verification.
5. Put fiddly logic in `scripts/`; reference via `${HERMES_SKILL_DIR}`.
6. Smoke-test with `hermes chat --toolsets skills -q "…"`, then evaluate with the
   harness; iterate on failures.

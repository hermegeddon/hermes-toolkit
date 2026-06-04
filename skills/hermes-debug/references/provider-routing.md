# Provider / model routing failures (404s and friends)

A model call that 404s, errors, or routes to the wrong endpoint is almost always a
provider/profile misconfiguration, not a model outage. Work these in order.

## 1. Is the provider valid for the profile?

Each profile names a `provider` and a `model`. The provider must be a real,
configured provider. On this setup: the main model is `apex-fast:latest` via
`provider: custom` → Ollama at `http://192.168.1.28:11434/v1`; substitute your Ollama
host. A profile that names a provider that does not exist (typo, removed provider)
fails at routing time.

Check both config files (they drift — see the main skill): the gateway reads
`/opt/hermes/home/config.yaml`, the TUI/CLI reads
`/opt/hermes/home/.hermes/config.yaml`. A correct provider in one and a stale one in
the other produces "works in the TUI, 404s in Telegram" (or vice-versa).

## 2. deepseek profiles need `provider: openrouter`

deepseek models are not reachable through the default/custom provider here — a
deepseek profile must set `provider: openrouter` explicitly. A deepseek profile left
on the wrong provider 404s. This is the most common single-profile routing bug.

## 3. Credential-pool contamination

When multiple profiles draw API keys from a shared credential pool, a key meant for
provider A can be selected for provider B, producing auth failures or 404s that look
random and depend on which profile ran last. Symptom: intermittent provider errors
that don't correlate with the model you think you're calling. Fix: pin the credential
for the failing profile to its own provider/key rather than relying on pool
selection, and confirm no two providers share an ambiguous key entry.

## 4. Verify with the warm path

Reproduce against the **warm** surface you actually use (gateway/interactive), not a
cold one-shot CLI — cold runs re-init everything and can mask or change routing
behavior. For repeatable verification use the `hermes-eval-harness` skill rather than
eyeballing a single call.

## Quick decision

- 404 only on one surface  -> config drift between the two config files (step 2).
- 404 on a deepseek profile -> `provider: openrouter` missing (step 2 above).
- intermittent / model-doesn't-match -> credential-pool contamination (step 3).
- provider name unknown     -> typo / removed provider in the profile.

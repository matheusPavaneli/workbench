# Configuration

Configuration is central, not per-project. You set up an account once and every
repo that matches a rule picks it up with no further setup.

```
~/.workbench/
  contexts/
    work-acme.json     provider, base_url, project, git identity, preset
    personal.json
  rules.json           which repos map to which context
```

Override the location with `WORKBENCH_HOME`.

## Secrets never live in these files

A context names *where* its credential is, never the credential itself. Loading
a config file that contains a key named `token`, `pat`, `password`, `secret` or
`api_key` is a hard error — those files are committable, and a leaked token is
not worth the convenience.

- `auth.pat_env` — the name of an environment variable. Portable, the default.
- `auth.pat_keychain` — an OS keychain account, under service `workbench`.
  macOS (`security`) and Linux (`secret-tool`) only. Not supported on Windows;
  use `pat_env` there.

## Resolution ladder

First hit wins, and `wb ctx show` always reports which step won. There is no
heuristic fallback: if nothing matches, the command stops and asks.

1. `.workflow/config.json` in the repo — explicit, committable, no secrets.
   May override `project`, `preset` and `board` on top of the named context.
2. `WORKBENCH_CONTEXT` environment variable.
3. `~/.workbench/rules.json` — git remote rules first, then path prefixes.
   A remote is stronger evidence than where a clone happens to sit on disk.
4. Nothing matched — stop, list the known contexts, show how to bind one.

## Defining a context

```sh
# Work: Azure DevOps
wb ctx add work-acme --provider azure \
  --base-url https://dev.azure.com/acme --project Platform \
  --pat-env AZDO_PAT_ACME --preset scaleup \
  --git-name "Your Name" --git-email you@acme.com

# Personal: Jira Cloud
wb ctx add personal --provider jira \
  --base-url https://you.atlassian.net --project SAAS \
  --pat-env JIRA_TOKEN_ME --email you@example.com --preset solo-saas
```

Jira Cloud authenticates as `email:api_token`, so Jira contexts need `--email`.
Azure DevOps authenticates with an empty user and the PAT as the password.

Separate environment variables per context is the point: two accounts on the
same provider never collide.

## Binding repos to a context

```sh
wb ctx use personal                      # this repo only (.workflow/config.json)
wb ctx use work-acme --remember remote   # every repo on this host + org
wb ctx use personal  --remember path     # every repo under this directory
```

Then verify with one real request:

```sh
wb ctx test
```

## Exit codes

| Code | Meaning |
|-----:|---------|
| 0 | success |
| 2 | usage — unknown group, action or flag |
| 3 | configuration — missing or invalid context |
| 4 | authentication — missing, invalid or unscoped token |
| 5 | provider — tracker returned an error, or is unreachable |
| 6 | not found — ticket or artifact does not exist |

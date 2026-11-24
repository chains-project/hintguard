# secrets-print

Blocks commands that print a stored secret into the agent transcript: `secret-tool lookup|search`,
`keyring get`, `nmcli -s` / `--show-secrets`. Captured (`T=$(...)`), redirected (`> file`) or hashed
(`| sha256sum`, `| wc -c`) output is allowed. Gap: `echo "$(secret-tool lookup ...)"`.

Why: an agent debugging Wi-Fi ran `nmcli -s con show eduroam` and the password went to the model
provider with the transcript. `hintguard setup` installs this bundle into `~/.nah/guards/` and
enables it; its hint is `[secrets-print]` in `hintguard/hints.toml`.

# Security policy

## Credentials

**No secret is ever stored in this repository.**

- Deploy scripts read credentials **only from environment variables** or CI secrets
  (`scripts/deploy.py`, `scripts/fetch-games.py`, GitHub Actions `${{ secrets.* }}`).
- Secrets are never accepted as CLI arguments and never printed.
- `.env.example` contains variable **names only**; `.gitignore` excludes `.env`.
- The site itself needs **no credentials at all** — it is static, client-side only.

### Required variables (names only)

| Variable | Used by |
|----------|---------|
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Pages (`wrangler`) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT` | R2 upload (`scripts/deploy.py --target r2`) |
| `ITCH_IO_API_KEY` (optional) | catalog refresh |

GitHub Pages needs **no token**: the workflow uses the built-in `GITHUB_TOKEN` with
`pages: write` / `id-token: write` permissions.

## If a credential is exposed

Tokens pasted into chat, issues, tickets, screenshots or logs must be treated as
**compromised**. Rotate immediately:

1. **Cloudflare API token** — *My Profile ▸ API Tokens* → Revoke.
2. **Cloudflare R2 keys** — *R2 ▸ Manage R2 API Tokens* → Roll / delete keys.
3. **GitHub PATs** (classic `ghp_`, fine-grained `github_pat_`, OAuth `gho_`) —
   *Settings ▸ Developer settings ▸ Personal access tokens* → Delete.
4. **Neon** — *Console ▸ Settings ▸ API keys* → Revoke.
5. **Redis Cloud** — rotate the database password / ACL user.
6. **Any LLM/API provider** (e.g. Mistral) — revoke the API key.

Then re-issue with the **narrowest scope** needed (e.g. Pages deploy only, or a
single R2 bucket) and store them in a secret manager or CI secrets.

## Reporting

Do not open a public issue containing credentials. Rotate first, then report.

## Runtime security

The site ships `nosniff`, `SAMEORIGIN`, `Referrer-Policy`, `Permissions-Policy` and a
`default-src 'self'` Content-Security-Policy (see `_headers` for Cloudflare Pages and
`netlify.toml` for Netlify). There are no third-party scripts, fonts, cookies,
analytics or trackers.

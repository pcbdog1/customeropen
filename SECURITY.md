# Security Policy

## Sensitive data

Never commit API keys, SMTP credentials, `.env` files, production workbooks,
customer databases, outreach drafts, sent logs, contact exports, runtime state,
logs, or backups.

Use `.env.example` as the configuration template and keep real values only in a
local `.env` file. Before publishing changes, inspect the Git index and run a
secret scanner such as Gitleaks or TruffleHog. If a credential is committed,
revoke and rotate it immediately; deleting it from the latest commit is not
sufficient.

## Reporting

Report security issues privately to the repository owner. Do not include live
credentials, customer records, or personal data in an issue.

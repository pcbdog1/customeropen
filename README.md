# customeropen

`customeropen` is a Python workflow for discovering public B2B hardware leads,
recovering company-domain business contacts, qualifying prospects, drafting
PCB/PCBA outreach, and sending approved-by-policy messages with persistent
deduplication and audit logs.

The repository contains no customer database, credentials, sent history, or
production output. It is a framework: operators are responsible for lawful
data collection, outreach authorization, suppression lists, and local rules.

## Architecture

The lead pipeline has three layers:

1. **Local company domain pool**: imported or collected domains are stored in
   `outputs/company_domain_pool.xlsx` and processed before external providers.
2. **Optional search providers**: Brave, Tavily, and Exa discover company
   domains. Firecrawl can fetch public company pages when enabled.
3. **Optional email enrichment**: Hunter can supplement public business email
   discovery after a company has passed hardware qualification.

The daily loop sends previously eligible drafts first, performs bounded email
recovery, processes the local domain pool, optionally discovers more domains,
applies the final send safety check, updates Excel, records sent history, and
writes a daily report.

## Requirements

- Python 3.11 or newer
- macOS, Linux, or Windows for core CLI use
- macOS for the included launchd example and optional Mail.app fallback
- An SMTP account for portable sending

## Installation

```bash
git clone https://github.com/YOUR_ACCOUNT/customeropen.git
cd customeropen
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate the environment with `.venv\Scripts\activate`.

## Configuration

Edit `.env` locally. Never commit it.

```dotenv
BRAVE_SEARCH_API_KEY=
TAVILY_API_KEY=
EXA_API_KEY=
FIRECRAWL_API_KEY=
HUNTER_API_KEY=
SEARCH_PROVIDER=auto
EMAIL_ENRICHMENT_ENABLED=False
FIRECRAWL_ENABLED=False
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
OUTREACH_SENDER_NAME=
OUTREACH_COMPANY_NAME=
OUTREACH_COMPANY_WEBSITE=
```

Missing optional API keys do not stop the workflow. With no search API, the
system uses the local domain pool. Enable enrichment and Firecrawl only after
supplying their corresponding keys.

## Input Files

Copy the example files to untracked operational filenames before importing:

```bash
cp inputs/domain_seed.example.csv inputs/domain_seed.csv
cp inputs/email_seed.example.csv inputs/email_seed.csv
```

The example records are fictional and use `example.com` subdomains. Runtime
Excel files are created under `outputs/` and are ignored by Git.

## Commands

Import company domains from `inputs/domain_seed.csv` or
`inputs/domain_seed.xlsx`:

```bash
python main.py --mode import_domains
```

Collect company domains without looking up contacts or sending mail:

```bash
python main.py --mode collect_domains
python main.py --mode collect_domains --dry-run
```

Import an email seed file or directory. This creates leads and drafts but does
not send mail:

```bash
python main.py --mode import_email_seed --seed-file inputs/email_seed.csv
python main.py --mode import_email_seed --seed-dir inputs/email_seed_files
```

Run email recovery without sending. Omit `--dry-run` only when you intend to
write recovered public business contacts back to the workbook:

```bash
python main.py --mode email_recovery --dry-run
python main.py --mode email_recovery
```

Exercise the full pipeline without sending or writing formal sent history:

```bash
python main.py --mode dry_run
```

Perform a bounded live test. Review the workbook, `.env`, suppression data, and
final safety results first:

```bash
python main.py --mode daily_loop --test-send-limit 1 --test-lead-limit 0
```

Run the normal workflow:

```bash
python main.py --mode daily_loop
```

Inspect pool, queue, provider, lock, checkpoint, and recent-run status:

```bash
python main.py --mode status
```

## macOS launchd

The template in
`examples/launchd/com.customeropen.lead-automation.plist.example` invokes
`run_daily.sh` every 19,800 seconds (5.5 hours).

1. Replace every `__PROJECT_DIR__` with the absolute clone path.
2. Copy the result to
   `~/Library/LaunchAgents/com.customeropen.lead-automation.plist`.
3. Make the runner executable and validate the plist.
4. Bootstrap the agent for the current user.

```bash
chmod +x run_daily.sh
plutil -lint ~/Library/LaunchAgents/com.customeropen.lead-automation.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.customeropen.lead-automation.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.customeropen.lead-automation.plist
launchctl print gui/$(id -u)/com.customeropen.lead-automation
```

Logs are written to `logs/`. Scheduler and checkpoint state are written to
`state/`; both directories are excluded from Git.

## Safety and Compliance

- Collect only public business contact information from sources you are
  permitted to access. Do not bypass logins, CAPTCHAs, robots rules, or access
  controls.
- Verify that the company is a relevant hardware business and that the mailbox
  belongs to the same company domain.
- Keep the final send safety check enabled. It blocks directories, encyclopedias,
  blogs, government and education pages, unrelated companies, peers, private
  email providers, and incomplete source records.
- Respect GDPR, CAN-SPAM, PECR, local electronic marketing laws, and applicable
  platform terms. Maintain suppression and do-not-contact records.
- Every message must identify the sender, contain a valid opt-out mechanism,
  and stop future contact after an opt-out.
- Never treat a directory page as the target company website. Resolve and
  verify the official domain before contact recovery or sending.
- Start with dry runs and a one-message live test. Inspect generated drafts and
  logs before enabling unattended scheduling.

## Tests

```bash
pytest -q
```

See [SECURITY.md](SECURITY.md) before publishing a fork or sharing runtime
artifacts.

# NexScout

Your personal AI job-hunting agent. Sister app to NexReach, same philosophy:
**NexScout finds, filters, scores and drafts. You decide, you edit, you apply.**
It never submits a form, never sends a message and never stores job-site passwords.

```
 Remote OK, Remotive, ─┐
 We Work Remotely,     ├─► collect ─► dedupe ─► rule filter ─► Claude score ─► Claude draft ─► you
 Jobicy, Himalayas,    │   (no AI)
 HN Who is hiring    ──┘    (no AI)    (no AI)       (batched, capped)  (65+ only)
```

## What's inside

```
nexscout/
├─ config/                    Django settings + URLs
├─ scout/
│  ├─ models.py               ScoutSettings, Profile, Job, OutreachTarget, SourceState, ActivityLog, LLMCall
│  ├─ admin.py
│  ├─ views.py, forms.py, urls.py, context_processors.py, templatetags/
│  ├─ services/
│  │  ├─ sources/             base.py (pluggable registry) + remoteok.py, remotive.py, weworkremotely.py
│  │  ├─ collect.py           fetch → dedupe → filter → store
│  │  ├─ filters.py           location / work-authorisation / seniority / age rules
│  │  ├─ dedupe.py            same company + similar title
│  │  ├─ scam.py              scam warning rules
│  │  ├─ claude_client.py     Claude Code CLI wrapper (headless, Pro login, no API key)
│  │  ├─ llm_json.py          robust JSON extraction from replies
│  │  ├─ scoring.py           batched scoring + parser
│  │  ├─ drafting.py          cover notes, openers, cliché / number checks
│  │  ├─ outreach.py          direct outreach messages
│  │  └─ profile_seed.py      your CV, as data
│  ├─ management/commands/    fetch_jobs, score_jobs, draft_notes, run_scout, followups, seed_profile, check_claude
│  ├─ templates/scout/        dashboard (Django templates)
│  ├─ static/scout/           app.css (dark theme), app.js (vanilla)
│  └─ tests/                  48 tests + fixtures
├─ scripts/run_scout.bat      what Task Scheduler runs
├─ scripts/install_task.bat   one-click scheduled task
├─ requirements.txt, .env.example
```

---

## Setup on Windows (once)

Open a terminal (PowerShell or Command Prompt) in the folder where you unzipped NexScout.

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

Open `.env` in VS Code and set `SECRET_KEY` to any long random string. Leave `LLM_MODEL=claude-sonnet-5-5`.

> PowerShell says "running scripts is disabled"? Run once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again.
> In VS Code, pick `.venv` as the interpreter (Ctrl+Shift+P → *Python: Select Interpreter*).

---

## Phase 1 — Project, models, admin

Creates the database, your profile from the CV, and an admin login.

```bat
python manage.py migrate
python manage.py seed_profile
python manage.py createsuperuser
python manage.py runserver
```

Check: http://127.0.0.1:8000/admin/ → **Profiles** shows *Timothy Moyinoluwa Arifalo* with Nexivo, NexVote, BTS and NexReach; **Scout settings** shows the defaults (keywords, threshold 65, 14 days, caps).

## Phase 2 — Job sources + filtering (no Claude)

```bat
python manage.py fetch_jobs
```

You'll see one line per source: *kept · filtered · dupes · seen · off-topic*.

- Sources are official public feeds/APIs only: Remote OK (JSON API), Remotive (public API), We Work Remotely (RSS),
  Jobicy (public API), Himalayas (public API, asked for jobs open to Nigeria) and Hacker News "Who is hiring?"
  (via the public HN Search API). Each can be switched off in Settings.
  Requests identify themselves as `NexScout/1.0 (…; contact: your email)`. No LinkedIn, no scraping.
- Each source has a minimum gap between fetches: Remote OK and WWR 30 min, Jobicy 3h, HN 6h,
  **Remotive 6h** and **Himalayas 12h** (both refresh about daily and ask for few calls). Running it again early prints `not due`. `--force` overrides; use it sparingly.
- Off-topic jobs (no keyword match) are not stored. Jobs that fail a rule are stored as **Skipped** with the
  reason, so you can see *why* in Jobs → *Filtered out* and press **Restore anyway**.
- Changed your filters? `python manage.py fetch_jobs --refilter` re-checks unscored jobs (Settings does this for you).

## Phase 3 — Claude scoring + drafting

First confirm Claude Code works from Python:

```bat
claude --version
python manage.py check_claude
```

Expect `Claude answered (claude-sonnet-5-5): {"ok": true}`. Then:

```bat
python manage.py score_jobs --limit 10
python manage.py draft_notes --limit 3
```

How usage stays light:
- Only jobs that passed the rules are scored. **5 jobs per Claude call** (setting), descriptions trimmed.
- Daily caps: **40 scored, 10 drafts, 10 outreach messages** (settings). Hit the cap → it stops politely.
- If Claude reports your Pro usage limit, the run stops instead of retrying.
- Every call is logged (Activity page shows calls per day).
- `ANTHROPIC_API_KEY` is removed from Claude's environment, so it can only use your Pro login.

Drafts are checked automatically for: over 150 words, clichés ("passionate", "I am writing to express…"),
and **any number that isn't in your CV or the job listing** (a guard against invented facts).
Warnings show above the draft. Read every draft before using it.

## Phase 4 — Dashboard

```bat
python manage.py runserver
```

Open http://127.0.0.1:8000/

> Running NexReach too? Only one app can use port 8000. Start NexScout on its own port:
> `python manage.py runserver 8001` and open http://127.0.0.1:8001/

- **Overview** – new today, shortlisted, applied this week, interviews, follow-ups due, response rate; top jobs; Claude usage vs caps; source health; buttons for *Fetch now / Score 5 / Draft 3*.
- **Jobs** – search, filter (status, source, score, has draft, scam flags), sort; *Open / Filtered out / All*.
- **Job page** – full description, Claude's reason, matched and missing skills, Nigeria fit, scam warnings, editable cover note + opener with **Copy** buttons and live word count, *Open listing*, status buttons, notes, follow-up date, history.
- **Pipeline** – Shortlisted → Applied → Interviewing → Offer / Rejected / Ghosted columns, plus "ready to shortlist".
- **Settings** – keywords, sources, locations, seniority toggle, max years, max age, model, threshold, caps, batch sizes, follow-up days, and the **profile text Claude sees** (editable; *Rebuild from CV* restores it).
- **Activity** – everything the agent did, plus Claude calls per day.

Marking a job **Applied** sets a follow-up 7 days later. **I followed up** pushes the next reminder out.

Works on your phone too (same Wi-Fi): `python manage.py runserver 0.0.0.0:8000`, add your laptop's IP to
`ALLOWED_HOSTS` in `.env`, and set `SCOUT_REQUIRE_LOGIN=true` so others on the network can't open it.

## Phase 5 — Outreach, scheduling, tests

**Outreach:** Outreach → *Add a company* (name, website, contact, the LinkedIn URL you paste, a line on what they do).
Press **Draft N messages**. Copy, send it yourself from LinkedIn or email, then mark it *Sent*.
NexScout never opens those LinkedIn links.

**Full cycle + follow-ups:**

```bat
python manage.py run_scout --minutes 30
python manage.py followups
python manage.py followups --upcoming 3
```

`run_scout` = fetch → filter → score → draft, and stops starting new work at the time limit.
Useful flags: `--no-ai` (fetch only), `--score-limit 10`, `--draft-limit 3`, `--force-fetch`.
A lock file stops two runs overlapping.

**Tests:**

```bat
python manage.py test scout
```

Covers filters (location, authorisation, seniority, age, keywords), scam rules, dedupe, source parsers,
the scoring and draft parsers, the Claude CLI wrapper (API key stripping, limit errors), caps and pages.

---

## Autopilot: let NexScout run on its own

Double-click **`scripts\start_nexscout.bat`**. It starts two small minimised windows (autopilot and the dashboard)
and opens http://127.0.0.1:8001. Autopilot runs a full cycle every 60 minutes (change it in Settings → Autopilot);
each job board keeps its own polite gap and Claude keeps to your daily caps.

To have it start by itself every time you log in, run **`scripts\install_startup.bat`** once
(undo with `remove_startup.bat`). Stop it with `stop_nexscout.bat`. The header shows **Autopilot on/off**.
Logs: `logs\autopilot.log`, `logs\dashboard.log`.

## Applying by email

Some listings say "email your CV to jobs@company.com". NexScout spots the address (and any required subject line),
builds the email from your cover note, attaches your CV and sends it from your Gmail.

1. Put your CV in the project folder as `cv.pdf`.
2. Turn on 2-Step Verification for your Google account, then create an **App Password**
   (Google Account → Security → App passwords). Put it in `.env`:
   `EMAIL_HOST_USER=you@gmail.com` and `EMAIL_HOST_PASSWORD=xxxx xxxx xxxx xxxx`.
3. Restart NexScout. Settings → Applying by email should show **Gmail connected** and **CV found**.

On a job page, the **Apply** panel lets you edit the email and press **Send application**. The job is marked Applied
and a follow-up is set. Optional: **Send automatically** (Settings) lets autopilot send on its own, only for jobs with a
high score, confirmed open to Nigeria, no scam warnings, a clean draft and a company email address, never twice to the
same address, and never more than your daily limit. Jobs that use a company application form (Greenhouse, Lever, etc.)
still need you to apply on their site.

## Swipe, tailored CVs and autofill

- **Swipe** (top menu): your scored matches as cards, best first. Right = apply, left = skip, up = save.
  Works with drag, the buttons, or the arrow keys.
- **Apply** on an email job: drafts the cover note if needed, builds a tailored CV and sends it from your Gmail.
  A draft with warnings is held in **Apply → Needs your review** instead.
- **Apply** on a form job (Greenhouse, Lever, Ashby, Workable, ...): goes to **Apply → Ready to autofill**.
  Press **Autofill**: a browser window (your Chrome, or Edge) opens each form, uploads your tailored CV,
  fills your details and drafts answers to the form's own questions from your profile. Work authorisation,
  visa, salary, demographic and consent questions are left for you (amber). **NexScout never clicks Submit.**
  When you submit, it notices and marks the job Applied; close the tab to move to the next.
- **Tailored CV**: one page, built per job from your CV facts only. The most relevant projects and skills
  come first, and the summary is rewritten for the role but checked (no new numbers, no clichés).
  Download it from any job page. Files are kept in `cvs/` (not pushed to GitHub).
- **Company career sites**: Settings → Search lists companies to watch as `greenhouse:gitlab`,
  `lever:company`, `ashby:posthog`, one per line. With "Add companies automatically" on, any company whose
  job links to one of those systems is added for you, so the list grows with your search.

Autofill uses Playwright (installed by `pip install -r requirements.txt`). It drives your installed Chrome or
Edge; only if neither works, run `.venv\Scripts\playwright install chromium` once.

## Schedule it with Windows Task Scheduler (alternative to autopilot)

Claude Code uses your logged-in session, so the task must run **only while you're logged on**.

### Quick way
Double-click `scripts\install_task.bat` (or run it in a terminal). It creates a task called **NexScout**
that runs `scripts\run_scout.bat` every 4 hours from about 8am to midnight. Test it immediately:

```bat
schtasks /Run /TN "NexScout"
type logs\scout.log
```

### By hand (Task Scheduler app)
1. Start menu → **Task Scheduler** → *Create Task…* (not "Basic Task").
2. **General:** Name `NexScout`. Select **Run only when user is logged on**.
3. **Triggers:** New → Daily, 8:07 AM → tick *Repeat task every* **4 hours** for a duration of **16 hours**.
4. **Actions:** New → Start a program → Program: the full path to `scripts\run_scout.bat`
   (e.g. `C:\Users\Timothy\Projects\nexscout\scripts\run_scout.bat`). *Start in*: the nexscout folder.
5. **Conditions:** untick *Start only if on AC power* if you want it on battery.
6. **Settings:** tick *Stop the task if it runs longer than* **1 hour**; *If already running*: **Do not start a new instance**.

Every 4 hours is plenty: Remotive only refreshes about daily and caps keep Claude usage bounded.

**If the log says Claude isn't found:** Task Scheduler may not see your npm PATH. Run `where claude` in a
terminal and put that full path (usually ending in `claude.cmd`) in `.env` as `CLAUDE_CLI=…`.

---

## Adding another job source

1. Create `scout/services/sources/myboard.py`:

```python
from datetime import timedelta
from .base import JobSource, RawJob, register

@register
class MyBoardSource(JobSource):
    key = "myboard"
    label = "My Board"
    homepage = "https://example.com"
    min_interval = timedelta(hours=6)

    def fetch(self, keywords):
        data = self.get("https://example.com/api/jobs").json()
        return [RawJob(source=self.key, external_id=str(j["id"]), title=j["title"],
                       company=j["company"], url=j["url"], description=j["text"],
                       location=j.get("location", "")) for j in data]
```

2. Import it in `scout/services/sources/__init__.py`.
3. Add `source_myboard = models.BooleanField("My Board", default=True)` to `ScoutSettings`, then
   `python manage.py makemigrations scout && python manage.py migrate`.

Only add boards with an official API or feed whose terms allow automated access.

## Moving to PostgreSQL later

`pip install "psycopg[binary]"`, then in `.env` set `DB_ENGINE=postgres` and the `DB_*` values, and run `python manage.py migrate`.

## Notes

- Remote OK and Remotive ask you to credit them and link back. Every job keeps its original listing link
  and shows its source; *Open listing* goes to the board's page.
- Scam warnings are hints, not verdicts: payment requests, Telegram/WhatsApp-only contact, crypto pay,
  missing company names, unrealistic pay and personal-email applications are flagged, never auto-deleted.
- Your CV lives in `scout/services/profile_seed.py`. When you ship something new, update it there and run
  `python manage.py seed_profile --force` (or edit the profile text in Settings).

"""
Profile seed taken from Timothy's CV (Timothy-Arifalo-Backend-Engineer.pdf).

This is the source of truth for drafts: Claude is told to use ONLY these facts.
Edit in Settings (or here, then run `python manage.py seed_profile --force`).
"""

CV = {
    "full_name": "Timothy Moyinoluwa Arifalo",
    "headline": "Backend Engineer — Python / Django / Payments",
    "location_line": "Nigeria · Remote (WAT, UTC+1 — full overlap with UK/EU, 4–6 hours with US East)",
    "email": "arifalotimothy@gmail.com",
    "phone": "+234 707 224 0810",
    "github": "https://github.com/Timotee-dev",
    "portfolio": "https://nexoradev0.netlify.app",
    "summary": (
        "Backend engineer who has designed, built and run three production Django platforms end to end, "
        "used by real customers at two Nigerian institutions and a multi-vendor client business. Strongest in "
        "payments: Paystack, Flutterwave and Stripe integrations, split payments, webhooks and automated payouts. "
        "Comfortable owning the full lifecycle — data modelling, REST APIs, deployment, email delivery and fixing "
        "production incidents. Now building AI agents with LLMs, and looking for a remote backend or full-stack "
        "role on a product team."
    ),
    "skills": "\n".join([
        "Backend: Python, Django, Django REST Framework, REST API design, role-based auth, multi-tenancy",
        "Data: PostgreSQL (Supabase, Neon), Django ORM, data modelling",
        "Payments: Paystack (subaccounts, split payments, webhooks, Transfer API), Flutterwave, Stripe",
        "Infrastructure: Render, Netlify, Cloudinary, Brevo (SMTP + HTTP API), custom domains and DNS, Git/GitHub",
        "Frontend & mobile: JavaScript, HTML/CSS, React Native (Expo)",
        "AI: LLM-powered agents (Claude), lead research and scoring pipelines",
        "Other: PHP, Java",
    ]),
    "projects": [
        {
            "key": "nexivo",
            "name": "Nexivo",
            "stack": "Django, PostgreSQL, Paystack/Flutterwave/Stripe",
            "one_liner": "Event ticketing and booking SaaS",
            "tags": ["payments", "fintech", "paystack", "stripe", "webhooks", "payouts", "events", "ticketing", "saas", "mobile"],
            "facts": [
                "Ticketing platform used for live events at UNIMED and Adeyemi College of Education.",
                "Integrated three payment gateways, with Paystack split payments via subaccounts so hosts receive 90–95% automatically.",
                "Marketer referral programme with wallets and automated commission payouts through the Paystack Transfer API.",
                "QR tickets, PDF invoices and a no-login camera check-in page (jsQR) for event staff.",
                "Fixed production incidents: moved email to Brevo's HTTP API when the host blocked SMTP ports, and restored service after a database region misconfiguration.",
                "Building a React Native (Expo) mobile app on the same live backend.",
            ],
        },
        {
            "key": "nexvote",
            "name": "NexVote",
            "stack": "Django, DRF, Paystack webhooks",
            "one_liner": "Multi-tenant paid voting SaaS",
            "tags": ["multi-tenant", "saas", "webhooks", "real-time", "dashboards", "payments", "drf", "api"],
            "facts": [
                "One codebase where each organiser runs isolated paid contests and elections, used at UNIMED and Adeyemi College of Education.",
                "Processes paid votes in real time through Paystack webhooks, handling confirmation, vote counts and failed payments without manual work.",
                "Organiser dashboards show live votes, standings and revenue without database access; QR-coded ballots and automated PDF receipts for every vote.",
                "Ships new features after launch based on feedback from organisers running live campaigns.",
            ],
        },
        {
            "key": "bts",
            "name": "BTS — The Back to School Project",
            "stack": "DRF, PostgreSQL, vanilla JS",
            "one_liner": "Multi-vendor e-commerce for students",
            "tags": ["e-commerce", "marketplace", "multi-vendor", "api", "drf", "client", "frontend", "checkout"],
            "facts": [
                "Turned a client's written product spec into a live platform with multiple active vendors, owning every engineering decision and iterating through regular client feedback.",
                "Designed the DRF API for catalogues, curated and build-your-own packages, orders and vendor endpoints; vendors manage their own listings and fulfilment.",
                "Hand-built the frontend in vanilla HTML/CSS/JS with no framework; Paystack checkout and Brevo order notifications.",
            ],
        },
        {
            "key": "nexreach",
            "name": "NexReach",
            "stack": "Django, Claude, OpenStreetMap, Gmail",
            "one_liner": "AI outreach agent",
            "tags": ["ai", "llm", "agents", "automation", "scoring", "pipelines", "email", "claude"],
            "facts": [
                "Finds businesses by city and type, checks their websites for problems, and scores fit 0–100 with an LLM.",
                "Drafts personalised emails and follow-ups, classifies replies and handles bounces and opt-outs.",
                "Human-approval queue, daily send limits and a do-not-contact list.",
            ],
        },
    ],
    "also_built": (
        "NexSpace (academic social network for a university department, with OCR on uploads), "
        "NexNice (multi-member couples app), and an event registration form builder with Excel export."
    ),
    "education": "B.Sc. Computer Science — University of Medical Sciences (UNIMED), Ondo, Nigeria · In progress (300 level)",
    "availability": "Open to remote contract and part-time roles. No visa or relocation needed.",
    "looking_for": "Remote contract, part-time, or junior/mid backend roles, paid in USD, open to candidates in Nigeria.",
}


def build_prompt_profile(data=None):
    d = data or CV
    lines = [
        f"NAME: {d['full_name']}",
        f"HEADLINE: {d['headline']}",
        f"LOCATION: {d['location_line']}",
        f"LOOKING FOR: {d['looking_for']}",
        "",
        f"SUMMARY: {d['summary']}",
        "",
        "SKILLS:",
        d["skills"],
        "",
        "PROJECTS (independent work under the name Nexora, 2024–present):",
    ]
    for p in d["projects"]:
        lines.append(f"- {p['name']} [{p['key']}] — {p['one_liner']} ({p['stack']})")
        for f in p["facts"]:
            lines.append(f"    • {f}")
    lines += [
        f"ALSO BUILT: {d['also_built']}",
        "",
        f"EDUCATION: {d['education']}",
        f"AVAILABILITY: {d['availability']}",
        f"LINKS: {d['github']} · {d['portfolio']}",
    ]
    return "\n".join(lines)


def apply_cv_seed(profile, save=True):
    for field in ("full_name", "headline", "location_line", "email", "phone", "github",
                  "portfolio", "summary", "skills", "projects", "availability", "looking_for"):
        setattr(profile, field, CV[field])
    profile.prompt_profile = build_prompt_profile()
    if save:
        profile.save()
    return profile

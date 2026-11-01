"""
Rule-based scam warnings. NexScout never drops a job for these; it flags it so you look twice.
"""
import re

_PAYMENT = re.compile(
    r"\b(application|registration|training|onboarding|processing|placement|starter)\s+(fee|kit|cost|deposit)s?\b"
    r"|\bpay\s+(?:a|an|the|for)\s+(?:small\s+)?(?:fee|training|equipment|kit|deposit|certification)"
    r"|\bsend\s+(?:us\s+)?money\b|\brefundable\s+deposit\b|\bpurchase\s+(?:your\s+)?(?:own\s+)?equipment\s+(?:from|through)\s+us",
    re.I,
)
_CHAT_ONLY = re.compile(r"\b(telegram|whats\s?app|signal app|wechat)\b|t\.me/|wa\.me/|chat\.whatsapp\.com", re.I)
_CRYPTO_PAY = re.compile(
    r"\b(paid|payment|salary|pay|compensation)\b[^.\n]{0,40}\b(crypto|usdt|bitcoin|btc|eth|tether|stablecoins?)\b"
    r"|\b(crypto|usdt|bitcoin|btc)\b[^.\n]{0,25}\b(only|payment|salary)\b",
    re.I,
)
_TOO_GOOD = re.compile(
    r"\bearn\s+(?:up\s+to\s+)?\$\s?\d[\d,]*\s*(?:per|a|/)\s*(?:day|week)\b"
    r"|\bno\s+experience\s+(?:needed|required)\b[^.\n]{0,80}\$\s?\d"
    r"|\bget\s+paid\s+(?:daily|instantly)\b",
    re.I,
)
_PERSONAL_EMAIL = re.compile(r"[\w.+-]+@(gmail|yahoo|hotmail|outlook|proton(mail)?|aol)\.\w+", re.I)
_VAGUE_COMPANIES = {"", "confidential", "stealth", "stealth startup", "company", "client", "hiring",
                    "our client", "private", "anonymous", "n/a", "na", "undisclosed", "tbd", "recruiter"}

# Above this, a junior/mid remote role is suspicious (USD/year).
TOO_GOOD_SALARY_USD = 250_000


def scam_flags(company, title, description, salary_max=None, salary_text=""):
    flags = []
    text = f"{title or ''}\n{description or ''}"
    if _PAYMENT.search(text):
        flags.append("Asks you to pay (fee, deposit, kit or training)")
    if _CHAT_ONLY.search(text):
        flags.append("Contact via Telegram/WhatsApp-style chat")
    if _CRYPTO_PAY.search(text):
        flags.append("Pays in crypto")
    if (company or "").strip().lower() in _VAGUE_COMPANIES:
        flags.append("Company name missing or vague")
    if _TOO_GOOD.search(text) or (salary_max and salary_max >= TOO_GOOD_SALARY_USD):
        flags.append("Pay looks too good to be true")
    if _PERSONAL_EMAIL.search(description or ""):
        flags.append("Applications go to a personal email address")
    return flags

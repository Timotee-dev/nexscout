"""
Tailored CV PDF for one job.

Truth rule: nothing here is generated freely. The content is your profile (from your CV);
only the ORDER changes (most relevant projects and skills first) plus a short summary that
has passed the same checks as drafts (no new numbers, no clichés). If anything is missing,
it falls back to your original summary.
"""
import re
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from fpdf import FPDF

from scout.models import Profile

from .profile_seed import CV

CV_DIR = Path(settings.BASE_DIR) / "cvs"
ACCENT = (36, 83, 214)
INK = (15, 23, 41)
MUTED = (100, 108, 122)

FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
ASCII_MAP = {"–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "•": "-", "·": "|", "→": "->", "…": "..."}


class _PDF(FPDF):
    def __init__(self):
        super().__init__(format="A4")
        self.set_margins(16, 14, 16)
        self.set_auto_page_break(True, margin=14)
        self.unicode = False
        for regular, bold in FONT_CANDIDATES:
            if Path(regular).is_file() and Path(bold).is_file():
                self.add_font("Body", "", regular)
                self.add_font("Body", "B", bold)
                self.unicode = True
                break
        self.family = "Body" if self.unicode else "Helvetica"

    def t(self, text):
        text = text or ""
        if self.unicode:
            return text
        for a, b in ASCII_MAP.items():
            text = text.replace(a, b)
        return text.encode("latin-1", "replace").decode("latin-1")

    def font(self, size, bold=False, color=INK):
        self.set_font(self.family, "B" if bold else "", size)
        self.set_text_color(*color)

    def section(self, title):
        self.ln(2.2)
        self.font(9.8, True, ACCENT)
        self.cell(0, 6, self.t(title.upper()), new_x="LMARGIN", new_y="NEXT")
        y = self.get_y()
        self.set_draw_color(205, 214, 235)
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(2)

    def para(self, text, size=9.3, color=INK, h=4.5):
        self.font(size, False, color)
        self.multi_cell(0, h, self.t(text), new_x="LMARGIN", new_y="NEXT", align="L")

    def bullet(self, text):
        self.font(9.1)
        x = self.l_margin
        self.set_x(x + 1.5)
        self.cell(4, 4.35, self.t("-" if not self.unicode else "•"))
        self.multi_cell(0, 4.35, self.t(text), new_x="LMARGIN", new_y="NEXT", align="L")


def _ordered_projects(profile, job):
    projects = list(profile.projects or [])
    order = [k for k in (job.cv_project_order or []) if k]
    if job.chosen_project and job.chosen_project not in order:
        order.insert(0, job.chosen_project)
    rank = {k: i for i, k in enumerate(order)}
    return sorted(projects, key=lambda p: rank.get(p.get("key"), len(rank) + projects.index(p)))


def _ordered_skills(profile, job):
    lines = [ln.strip() for ln in (profile.skills or "").splitlines() if ln.strip()]
    matched = [m.lower() for m in job.matched_skills or []]
    def hits(line):
        low = line.lower()
        return sum(1 for m in matched if m and m in low)
    return sorted(lines, key=lambda ln: -hits(ln))


def cv_filename(job, profile=None):
    profile = profile or Profile.get_solo()
    name = "-".join((profile.full_name or "CV").split()[::max(1, len((profile.full_name or "x").split()) - 1)])
    company = slugify(job.company or "role")[:30] or "role"
    return f"{name}-CV-{company}.pdf"


def build_cv(job, profile=None):
    """Render the tailored CV PDF and store its path on the job. Returns the Path."""
    profile = profile or Profile.get_solo()
    pdf = _PDF()
    pdf.add_page()

    # Header
    pdf.font(20, True)
    pdf.cell(0, 9, pdf.t(profile.full_name or CV["full_name"]), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.font(11, True, ACCENT)
    pdf.cell(0, 6, pdf.t(profile.headline or CV["headline"]), new_x="LMARGIN", new_y="NEXT", align="L")
    contact = " | ".join(x for x in [profile.email, profile.phone, profile.github.replace("https://", ""),
                                     profile.portfolio.replace("https://", "")] if x)
    pdf.font(8.8, False, MUTED)
    pdf.multi_cell(0, 4.4, pdf.t(contact), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.multi_cell(0, 4.4, pdf.t(profile.location_line or CV["location_line"]), new_x="LMARGIN", new_y="NEXT", align="L")

    # Summary (tailored if it passed checks, else your own)
    pdf.section("Summary")
    pdf.para(job.cv_summary or profile.summary or CV["summary"])

    # Skills, most relevant first
    pdf.section("Skills")
    for line in _ordered_skills(profile, job):
        head, _, rest = line.partition(":")
        pdf.font(9.1, True)
        if rest:
            pdf.cell(pdf.get_string_width(pdf.t(head + ": ")) + 0.5, 4.5, pdf.t(head + ":"))
            pdf.font(9.1)
            pdf.multi_cell(0, 4.5, pdf.t(rest.strip()), new_x="LMARGIN", new_y="NEXT", align="L")
        else:
            pdf.font(9.4)
            pdf.multi_cell(0, 4.8, pdf.t(line), new_x="LMARGIN", new_y="NEXT", align="L")

    # Projects, most relevant first; the top two get every fact
    pdf.section("Projects  (independent work as Nexora, 2024 - present)")
    for i, p in enumerate(_ordered_projects(profile, job)):
        pdf.ln(0.8)
        pdf.font(10.2, True)
        pdf.cell(pdf.get_string_width(pdf.t(p.get("name", ""))) + 2, 5.4, pdf.t(p.get("name", "")))
        pdf.font(9.2, False, MUTED)
        pdf.multi_cell(0, 5.4, pdf.t(f"{p.get('one_liner', '')}  |  {p.get('stack', '')}"), new_x="LMARGIN", new_y="NEXT", align="L")
        facts = p.get("facts") or []
        for fact in facts[: (None, 4, 2, 2)[min(i, 3)]]:
            pdf.bullet(fact)

    if CV.get("also_built"):
        pdf.ln(0.8)
        pdf.para(f"Also built: {CV['also_built']}", size=9, color=MUTED, h=4.4)

    pdf.section("Education & availability")
    pdf.para(CV["education"])
    pdf.para(profile.availability or CV["availability"])

    # One folder per job so the file you upload is simply "Firstname-Lastname-CV-company.pdf"
    folder = CV_DIR / str(job.pk)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.pdf"):
        old.unlink(missing_ok=True)
    path = folder / cv_filename(job, profile)
    pdf.output(str(path))
    job.cv_file = str(path)
    job.cv_built_at = timezone.now()
    job.save(update_fields=["cv_file", "cv_built_at", "updated_at"])
    return path


def cv_for(job):
    """Path of the CV to send for this job: tailored if it exists, else your cv.pdf, else None."""
    if job.cv_file and Path(job.cv_file).is_file():
        return Path(job.cv_file)
    p = Path(settings.CV_PATH)
    return p if p.is_file() else None

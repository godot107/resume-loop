#!/usr/bin/env python3
"""Score a resume against a job description and surface keyword gaps.

This is the deterministic half of a human-in-the-loop tailoring cycle: it
computes a similarity score and a list of missing keywords so you (with Claude
in the loop) can edit the resume YAML, rebuild, and re-score until the number
satisfies you. It never edits anything itself.

The headline number is the cosine similarity between the resume and the job
description as sublinear term-frequency vectors over their shared vocabulary
(stopwords removed). 0% = no overlap, 100% = identical wording. Alongside it,
the keyword report lists the most important JD terms and whether each already
appears in the resume — those misses are your edit targets.

To stop synonyms and word forms from counting as misses, tokens are folded
through data/skill_aliases.yaml (e.g. ML -> machine learning, Postgres ->
PostgreSQL) and conservatively singularized before scoring.

For the soft matches a bag-of-words can't see (paraphrase, "led" == "managed"),
pass --digest: it pairs each JD requirement with its best lexical resume bullet
so Claude, in this session, can judge true coverage and suggest edits.

The job description is plain text: paste the posting into a file and point
--jd at it (or pipe it on stdin).

Examples:
  python match.py --data data/resume.yaml --jd data/jd_deloitte_ds.txt
  python match.py --job-id deloitte_ds          # resume_<id>.yaml + jd_<id>.txt
  pbpaste | python match.py --data data/resume_ml.yaml   # JD from stdin
  python match.py --job-id deloitte_ds --top 40 --show-present
  python match.py --job-id deloitte_ds --digest          # semantic prep for Claude
"""

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

DATA = Path(__file__).parent / "data"

# Common English + resume/JD boilerplate words that carry no signal. Kept inline
# (rather than a dependency like NLTK) so the scoring stays transparent.
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "for", "of",
    "to", "in", "on", "at", "by", "with", "from", "as", "is", "are", "was",
    "were", "be", "been", "being", "this", "that", "these", "those", "it",
    "its", "we", "you", "your", "our", "us", "they", "their", "them", "he",
    "she", "his", "her", "i", "me", "my", "will", "would", "should", "can",
    "could", "may", "might", "must", "shall", "do", "does", "did", "have",
    "has", "had", "not", "no", "so", "than", "too", "very", "just", "about",
    "into", "over", "under", "out", "up", "down", "off", "all", "any", "each",
    "more", "most", "other", "some", "such", "only", "own", "same", "who",
    "what", "which", "when", "where", "why", "how", "there", "here", "both",
    "few", "between", "across", "within", "per", "via", "etc", "e.g", "i.e",
    # JD boilerplate
    "job", "role", "position", "candidate", "candidates", "applicant", "work",
    "working", "experience", "years", "year", "team", "teams", "company",
    "ability", "strong", "excellent", "good", "including", "include",
    "includes", "preferred", "required", "requirements", "responsibilities",
    "responsible", "skills", "knowledge", "understanding", "ideal", "looking",
    "join", "help", "new", "well", "using", "use", "used", "plus", "must",
    "etc.", "you'll", "we're", "you're", "they're",
}

# Token = a word that may contain inner +, #, ., - (so c++, c#, node.js, ci-cd
# survive). Leading/trailing punctuation is stripped after the match.
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")
# Boundaries that a two-word phrase should never span (sentence/line breaks,
# list separators). Bigrams are only formed within one segment.
SEGMENT_RE = re.compile(r"[.;:!?,/()\[\]\n]+")

ALIASES_FILE = DATA / "skill_aliases.yaml"

# Tokens the conservative lemmatizer must never touch — words that look like
# plurals/verbs but aren't, and would be corrupted by suffix stripping
# (aws -> aw, kinesis -> kinesi, analysis -> analysi). The alias vocabulary is
# added to this set at load time so canonical/variant terms stay stable too.
MANUAL_PROTECT = {
    "aws", "kinesis", "analysis", "series", "kubernetes", "devops", "ios",
    "kafka", "business", "status", "bias", "axis", "lens", "less", "css",
}


def load_aliases() -> tuple[dict[str, list[str]], set[str]]:
    """Load data/skill_aliases.yaml into a variant -> canonical-tokens expander.

    Returns (expand, vocab):
      expand maps each single-token variant (e.g. "ml") to the canonical's token
      list (e.g. ["machine", "learning"]). A canonical written verbatim in text
      already tokenizes to that same list, so both sides line up. vocab is every
      token taking part in aliasing — these are protected from the lemmatizer.
    """
    expand: dict[str, list[str]] = {}
    vocab: set[str] = set()
    if ALIASES_FILE.exists():
        data = yaml.safe_load(ALIASES_FILE.read_text()) or {}
        for canonical, variants in (data.get("aliases") or {}).items():
            canon_tokens = str(canonical).lower().split()
            vocab.update(canon_tokens)
            for v in variants or []:
                vt = str(v).lower().strip()
                if vt:
                    expand[vt] = canon_tokens
                    vocab.add(vt)
    return expand, vocab


EXPAND, ALIAS_VOCAB = load_aliases()
PROTECTED = MANUAL_PROTECT | ALIAS_VOCAB

# Plural-looking endings that are not plurals, so a trailing "s" is left alone.
_PLURAL_KEEP = ("ss", "us", "is", "as", "os", "ics", "ews", "ius")


def lemmatize(tok: str) -> str:
    """Best-effort, conservative singularizer so plural variants match.

    Folds only the common plural endings (models->model, libraries->library,
    processes->process) — the safe, high-value case. Verb tense is deliberately
    left alone: naive -ing/-ed stripping corrupts e-stem verbs (created->creat,
    managed->manag) so they no longer match the JD's base form, doing net harm.
    Verb-form and true-synonym equivalence is handled by the --digest pass
    (Claude in-session) instead. Guarded by min length and the PROTECTED set.
    """
    if tok in PROTECTED or len(tok) <= 4:
        return tok
    if tok.endswith("ies") and len(tok) >= 6:
        return tok[:-3] + "y"
    if tok.endswith(("ses", "xes", "ches", "shes", "zes")) and len(tok) >= 5:
        return tok[:-2]
    if tok.endswith("s") and not tok.endswith(_PLURAL_KEEP):
        return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    """Lowercase, split into content tokens, alias-fold, and lemmatize.

    A recognized synonym (e.g. "ml") is expanded to the canonical token stream
    (["machine", "learning"]); everything else is conservatively lemmatized so
    plural/tense variants still match. Stopwords and number-led noise are dropped.
    """
    tokens = []
    for raw in TOKEN_RE.findall(text.lower()):
        tok = raw.strip(".-")
        # Drop stopwords, single chars, and anything starting with a digit
        # (years, counts, "3+") — skills lead with a letter (c++, node.js).
        if len(tok) < 2 or tok in STOPWORDS or tok[0].isdigit():
            continue
        if tok in EXPAND:               # synonym -> canonical token(s)
            tokens.extend(EXPAND[tok])
        else:
            tokens.append(lemmatize(tok))
    return tokens


def segments(text: str) -> list[list[str]]:
    """Tokenize each sentence/line separately so bigrams can't cross breaks."""
    return [tokenize(seg) for seg in SEGMENT_RE.split(text)]


def bigrams(segs: list[list[str]]) -> list[str]:
    return [f"{a} {b}" for seg in segs for a, b in zip(seg, seg[1:])]


def flatten(segs: list[list[str]]) -> list[str]:
    return [t for seg in segs for t in seg]


def resume_text(data_path: Path) -> str:
    """Flatten every string value in the resume YAML into one blob of text."""
    if not data_path.exists():
        sys.exit(f"Resume data file not found: {data_path}")
    data = yaml.safe_load(data_path.read_text())

    chunks: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            chunks.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return " ".join(chunks)


def cosine(a_tokens: list[str], b_tokens: list[str]) -> float:
    """Cosine similarity of two token lists as sublinear-TF vectors.

    Weight per term is 1 + log(count), which dampens the effect of a word that
    happens to repeat many times so a single dominant term can't inflate the
    score. Returns a value in [0, 1].
    """
    a_counts, b_counts = Counter(a_tokens), Counter(b_tokens)

    def weight(counts: Counter) -> dict[str, float]:
        return {t: 1 + math.log(c) for t, c in counts.items()}

    a_vec, b_vec = weight(a_counts), weight(b_counts)
    shared = set(a_vec) & set(b_vec)
    dot = sum(a_vec[t] * b_vec[t] for t in shared)
    norm_a = math.sqrt(sum(w * w for w in a_vec.values()))
    norm_b = math.sqrt(sum(w * w for w in b_vec.values()))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def keyword_report(resume_segs: list[list[str]], jd_segs: list[list[str]], top: int):
    """Rank the JD's most salient terms and mark which the resume covers.

    Returns (present, missing) lists of (term, jd_frequency) tuples. Bigrams are
    weighted slightly above unigrams because a two-word phrase ("machine
    learning") is a more specific, higher-value match than either word alone.
    A bigram counts as present if its exact pair appears in the resume, or if
    both of its words appear individually somewhere in the resume.
    """
    resume_unigrams = set(flatten(resume_segs))
    resume_bigrams = set(bigrams(resume_segs))

    uni_freq = Counter(flatten(jd_segs))
    bi_freq = Counter(bigrams(jd_segs))

    scored: dict[str, float] = {}
    for term, f in uni_freq.items():
        scored[term] = float(f)
    for term, f in bi_freq.items():
        if f >= 2:  # only repeated phrases are worth surfacing as their own item
            scored[term] = f * 1.5

    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:top]

    present, missing = [], []
    for term, score in ranked:
        if " " in term:
            a, b = term.split(" ", 1)
            covered = term in resume_bigrams or (a in resume_unigrams and b in resume_unigrams)
        else:
            covered = term in resume_unigrams
        (present if covered else missing).append((term, int(score)))
    return present, missing


def bar(pct: float, width: int = 30) -> str:
    filled = round(pct / 100 * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


# --- Semantic digest (Claude-in-session review) -------------------------------
# match.py can't judge true synonymy or paraphrase ("led a team" == "managed
# engineers"). Instead of a local embedding model, --digest extracts the JD's
# requirement lines and the resume's bullets, pairs each requirement with its
# best *lexical* resume match, and sorts weakest-first. Claude (in this session)
# then reads that and overlays the semantic judgment + concrete edit suggestions.

# Lines that are section headers or legal boilerplate, not real requirements.
_BOILERPLATE_RE = re.compile(
    r"equal opportunity|regardless of|reasonable accommodation|"
    r"e-verify|background check|visa sponsorship|benefits include",
    re.IGNORECASE,
)
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•–·]|\d+[.)])\s*")


def jd_requirements(raw: str) -> list[str]:
    """Split the raw JD into requirement-sized units, preserving original text.

    Splits on lines and sentence boundaries, strips bullet markers, and drops
    headers (short lines ending in ':') and obvious boilerplate. A unit is kept
    only if it carries at least 3 content tokens.
    """
    units: list[str] = []
    for line in raw.splitlines():
        line = _BULLET_PREFIX_RE.sub("", line.strip())
        if not line:
            continue
        # A line may pack several sentences; split so each requirement stands alone.
        for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
            piece = piece.strip()
            if not piece or _BOILERPLATE_RE.search(piece):
                continue
            if piece.endswith(":") and len(piece.split()) <= 4:  # section header
                continue
            if len(tokenize(piece)) < 3:
                continue
            units.append(piece)
    return units


def resume_units(data_path: Path) -> list[tuple[str, str]]:
    """Extract (context-label, text) units from the resume YAML.

    Pulls each experience/project bullet (labeled by role title / project name)
    and each skills line (labeled by category) — the matchable content a JD
    requirement would map onto.
    """
    data = yaml.safe_load(data_path.read_text()) or {}
    units: list[tuple[str, str]] = []
    for role in data.get("experience", []) or []:
        label = str(role.get("title", "experience")).strip()
        for bullet in role.get("bullets", []) or []:
            units.append((label, str(bullet)))
    for proj in data.get("projects", []) or []:
        label = str(proj.get("name", "project")).strip()
        for bullet in proj.get("bullets", []) or []:
            units.append((label, str(bullet)))
    for group in data.get("skills", []) or []:
        label = f"skills: {group.get('category', '')}".strip()
        items = group.get("items")
        if items:
            units.append((label, str(items)))
    return units


def _truncate(text: str, width: int = 88) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def print_digest(jd_units: list[str], runits: list[tuple[str, str]]) -> None:
    """Print each JD requirement with its best lexical resume match, weakest first."""
    print("\n" + "=" * 60)
    print("SEMANTIC PREP  (for Claude in-session review)")
    print("  JD requirements sorted weakest-coverage-first; [n] = best lexical")
    print("  match (0–1) to any resume bullet. Claude judges true/synonym")
    print("  coverage from here and proposes truthful edits.")
    print("=" * 60)

    if not jd_units:
        print("  (no requirement-sized lines found in the JD)")
        return
    if not runits:
        print("  (no bullets found in the resume YAML)")
        return

    unit_tokens = [tokenize(text) for _, text in runits]
    rows = []
    for req in jd_units:
        req_tokens = tokenize(req)
        best_i, best_score = 0, -1.0
        for i, toks in enumerate(unit_tokens):
            s = cosine(req_tokens, toks)
            if s > best_score:
                best_i, best_score = i, s
        rows.append((best_score, req, runits[best_i]))

    for score, req, (label, text) in sorted(rows, key=lambda r: r[0]):
        print(f"\n  [{score:.2f}] JD: {_truncate(req)}")
        print(f"         best ({_truncate(label, 36)}): {_truncate(text)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data", help="Resume YAML (default: data/resume.yaml, or resume_<job-id>.yaml)")
    parser.add_argument("--jd", help="Job-description text file (default: data/jd_<job-id>.txt, or stdin)")
    parser.add_argument("--job-id", help="Shortcut: resume_<id>.yaml + jd_<id>.txt under data/")
    parser.add_argument("--top", type=int, default=25, help="Number of top JD keywords to analyze (default: 25)")
    parser.add_argument("--show-present", action="store_true", help="Also list the keywords already covered")
    parser.add_argument("--digest", action="store_true",
                        help="Also emit a SEMANTIC PREP block (JD requirements vs best resume "
                             "bullet, weakest first) for Claude in-session review")
    args = parser.parse_args()

    # Resolve the resume path.
    if args.data:
        data_path = Path(args.data)
    elif args.job_id:
        data_path = DATA / f"resume_{args.job_id}.yaml"
    else:
        data_path = DATA / "resume.yaml"

    # Resolve the JD text: explicit file, per-job file, or stdin.
    if args.jd:
        jd_path = Path(args.jd)
        if not jd_path.exists():
            sys.exit(f"Job-description file not found: {jd_path}")
        jd_raw = jd_path.read_text()
        jd_label = str(jd_path)
    elif args.job_id and (DATA / f"jd_{args.job_id}.txt").exists():
        jd_path = DATA / f"jd_{args.job_id}.txt"
        jd_raw = jd_path.read_text()
        jd_label = str(jd_path)
    elif not sys.stdin.isatty():
        jd_raw = sys.stdin.read()
        jd_label = "(stdin)"
    else:
        sys.exit(
            "No job description given. Pass --jd <file>, use --job-id (expects "
            "data/jd_<id>.txt), or pipe the posting on stdin."
        )

    if not jd_raw.strip():
        sys.exit("Job description is empty.")

    resume_segs = segments(resume_text(data_path))
    jd_segs = segments(jd_raw)

    score = cosine(flatten(resume_segs), flatten(jd_segs)) * 100
    present, missing = keyword_report(resume_segs, jd_segs, args.top)
    analyzed = len(present) + len(missing)
    coverage = (len(present) / analyzed * 100) if analyzed else 0.0

    print(f"\nResume : {data_path}")
    print(f"JD     : {jd_label}")
    print("=" * 60)
    print(f"Match score (cosine)  {score:5.1f}%  {bar(score)}")
    print(f"Keyword coverage      {coverage:5.1f}%  {bar(coverage)}  "
          f"({len(present)}/{analyzed} of top {args.top} JD terms)")
    print("=" * 60)

    if missing:
        print(f"\nMISSING — top JD keywords not in your resume ({len(missing)}):")
        print("  (add the ones that are genuinely true of you, then rebuild & re-score)")
        for term, freq in missing:
            print(f"   - {term:<28} (appears {freq}x in JD)")
    else:
        print("\nNo missing keywords among the top terms — strong coverage.")

    if args.show_present and present:
        print(f"\nPRESENT — already covered ({len(present)}):")
        for term, freq in present:
            print(f"   + {term:<28} (appears {freq}x in JD)")

    if args.digest:
        print_digest(jd_requirements(jd_raw), resume_units(data_path))

    print()


if __name__ == "__main__":
    main()

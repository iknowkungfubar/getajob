"""Anti-AI Detection Guardrails for the GetAJob Tailoring Engine.

Analyses generated text for patterns that are telltale signs of LLM-generated
content and suggests replacements to make the text read as naturally human.

Key features:
- **Blocklist scanning**: Flags overused AI phrases (clichés, formulaic
  transitions, hedging language).
- **Sentence-length variety analysis**: Measures standard deviation of sentence
  lengths - AI writing tends toward uniform length.
- **Vocabulary richness scoring**: Compares unique-word ratio against
  human-written baselines.
- **Suggestions**: Provides concrete replacements for flagged phrases.
"""

from __future__ import annotations as _annotations

import re
import string
from dataclasses import dataclass, field

import structlog

__all__: list[str] = [
    "AnalysisResult",
    "AntiAIDetector",
]

logger = structlog.get_logger(__name__)

# ── Blocklist of overused AI phrases ──────────────────────────────────────────

_AI_CLICHE_BLOCKLIST: dict[str, str] = {
    # Openers
    "in today's fast-paced digital landscape": "in the current technology landscape",
    "in today's fast-paced world": "in a fast-moving industry",
    "in today's ever-evolving technological landscape": "as technology evolves",
    "in today's competitive job market": "in this competitive field",
    "i am writing to express my interest": "i am interested",
    "i am writing to apply": "i am applying",
    "i am excited to submit": "i welcome the chance",
    "i am thrilled to apply": "i welcome the chance",
    "i am writing to express my keen interest": "i am interested",
    "please accept this letter as expression of": "",

    # Transitional / hedging
    "it is with great enthusiasm": "",
    "i am confident that my skills and experience": "my experience",
    "i believe that my skills": "my skills",
    "i am confident that i": "i am",
    "i am certain that my": "my",
    "i would welcome the opportunity": "i would welcome",
    "i would be thrilled to": "i would like to",
    "i am eager to bring": "i can bring",

    # Closer
    "thank you for your time and consideration": "thank you for your consideration",
    "thank you for considering my application": "thank you for your time",
    "i look forward to hearing from you": "i hope to hear from you",
    "i look forward to the opportunity": "i hope for the chance",
    "please do not hesitate to contact me": "feel free to reach out",
    "i welcome the opportunity to discuss": "i would welcome a conversation",

    # Generic filler
    "the attached resume": "my resume",
    "please find attached": "i have attached",
    "as you can see from my resume": "my resume shows",
    "as mentioned in my resume": "beyond my resume",
    "as detailed in my attached resume": "my resume details",
    "i possess strong": "i have strong",
    "a proven track record": "a record",
    "think outside the box": "find creative solutions",
    "synergy": "collaboration",
    "leverage": "use",
    "utilize": "use",
    "dynamic": "",
    "results-driven": "",
    "detail-oriented": "",
    "highly motivated": "",
}

# Additional phrases that are flagged but not auto-replaced.
_AI_PHRASE_BLOCKLIST: set[str] = {
    "passionate about",
    "team player",
    "go-getter",
    "self-starter",
    "ninja",
    "guru",
    "rockstar",
    "deep dive",
    "circle back",
    "touch base",
    "reach out",
    "hit the ground running",
    "move the needle",
    "boil the ocean",
    "low-hanging fruit",
    "best of breed",
    "best-in-class",
    "cutting-edge",
    "bleeding-edge",
    "state-of-the-art",
    "world-class",
    "game-changer",
    "thought leader",
}


@dataclass
class AnalysisResult:
    """Result of scanning text for AI-like patterns."""

    score: float = 0.0
    """Overall AI-likeness score (0.0 = very human, 1.0 = very AI-like)."""

    flagged_phrases: list[str] = field(default_factory=list)
    """Phrases from the blocklist that were found in the text."""

    suggestions: list[tuple[str, str] | str] = field(default_factory=list)
    """Suggested replacements.

    Each entry is either:
    - A ``(original, replacement)`` tuple for auto-replaceable phrases.
    - A plain ``str`` for flagged phrases that have no replacement.
    """

    sentence_length_std: float = 0.0
    """Standard deviation of sentence lengths.  Human writing typically has
    higher variance (>8), AI writing tends toward uniform lengths (<6)."""

    vocabulary_richness: float = 0.0
    """Type-token ratio (unique words / total words).  >0.5 is typical for
    human writing; <0.45 suggests repetitive AI patterns."""

    format_warnings: list[str] = field(default_factory=list)
    """Warnings about formatting patterns (e.g. excessive bullet use)."""


# ── Detector ──────────────────────────────────────────────────────────────────


class AntiAIDetector:
    """Scan text for AI-like writing patterns.

    Usage::

        detector = AntiAIDetector()
        result = detector.scan_text("I am writing to express my interest...")
        if result.score > 0.3:
            print("High AI-likeness detected")
            print(result.suggestions)
    """

    def __init__(self, *, threshold: float = 0.3) -> None:
        self._threshold = threshold

    # ── Public API ─────────────────────────────────────────────────────────────

    def scan_text(self, text: str) -> AnalysisResult:
        """Analyse *text* for AI-like writing patterns.

        Args:
            text: The generated text to analyse (resume, cover letter, etc.).

        Returns:
            An :class:`AnalysisResult` with the analysis.
        """
        if not text or not text.strip():
            return AnalysisResult(score=0.0)

        # Run all analyses and collect scores.
        blocklist_score, flagged, suggestions = self._scan_blocklist(text)
        sent_std, _sent_warnings = self._analyse_sentence_length(text)
        vocab_score = self._vocabulary_richness(text)
        format_warnings = self._analyse_format(text)

        # Weighted composite score.
        # Blocklist violations are strong signals (weight 0.5).
        # Sentence uniformity and low vocabulary are weaker signals (weight 0.25 each).
        score = 0.5 * blocklist_score + 0.25 * sent_std + 0.25 * vocab_score
        score = max(0.0, min(1.0, score))

        return AnalysisResult(
            score=round(score, 4),
            flagged_phrases=flagged,
            suggestions=suggestions,
            sentence_length_std=sent_std,
            vocabulary_richness=vocab_score,
            format_warnings=format_warnings,
        )

    # ── Blocklist scan ─────────────────────────────────────────────────────────

    def _scan_blocklist(self, text: str) -> tuple[float, list[str], list[tuple[str, str] | str]]:
        """Scan text for blocklisted AI phrases.

        Returns:
            Tuple of ``(score, flagged_phrases, suggestions)``.
        """
        text_lower = text.lower()
        flagged: list[str] = []
        suggestions: list[tuple[str, str] | str] = []

        # Scan the replaceable blocklist (longest entries first to avoid
        # sub-string matches replacing partial matches).
        for phrase, replacement in sorted(_AI_CLICHE_BLOCKLIST.items(), key=lambda x: -len(x[0])):
            if phrase in text_lower:
                flagged.append(phrase)
                if replacement:
                    suggestions.append((phrase, replacement))
                else:
                    suggestions.append(phrase)

        # Scan the flag-only blocklist.
        for phrase in sorted(_AI_PHRASE_BLOCKLIST, key=len, reverse=True):
            pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
            if pattern.search(text_lower):
                flagged.append(phrase)
                suggestions.append(phrase)

        # Deduplicate flagged phrases while preserving order.
        seen: set[str] = set()
        unique_flagged: list[str] = []
        for p in flagged:
            if p not in seen:
                seen.add(p)
                unique_flagged.append(p)

        # Score: raw count normalised by text length.
        count = len(unique_flagged)
        # Heuristic: >3 phrases per 500 words is a strong signal.
        word_count = len(text.split())
        score = min(1.0, count / max(1, word_count / 150))

        return score, unique_flagged, suggestions

    # ── Sentence-length analysis ───────────────────────────────────────────────

    def _analyse_sentence_length(self, text: str) -> tuple[float, list[str]]:
        """Analyse sentence-length variance.

        AI-generated text tends to have uniform sentence length (low stddev).
        Human writing mixes short, medium, and long sentences (higher stddev).

        Returns:
            Tuple of ``(normalised_score, warnings)`` where *normalised_score*
            is 0.0 (human-like variance) to 1.0 (uniform - AI-like).
        """
        import statistics

        sentences = self._split_sentences(text)
        if len(sentences) < 3:
            return 0.0, []

        lengths = [len(s.split()) for s in sentences]
        stddev = statistics.stdev(lengths) if len(lengths) > 1 else 0.0

        warnings: list[str] = []
        if stddev < 4.0:
            warnings.append("Sentence lengths are very uniform - rewrite with more variety")
        elif stddev < 6.0:
            warnings.append("Sentence length variance is low - mix in shorter and longer sentences")

        # Normalise: stddev of 2 → 1.0 (very uniform), 12+ → 0.0 (human-like).
        normalised = max(0.0, min(1.0, 1.0 - (stddev - 2.0) / 10.0))

        return normalised, warnings

    # ── Vocabulary richness ─────────────────────────────────────────────────────

    def _vocabulary_richness(self, text: str) -> float:
        """Compute vocabulary richness as an AI-likeness score.

        Uses type-token ratio (TTR): unique words / total words.
        Very high TTR (>0.7) or very low TTR (<0.35) both suggest AI writing.
        Human writing typically falls between 0.45 and 0.65.

        Returns:
            A score from 0.0 (human-like) to 1.0 (AI-like).
        """
        words = text.lower().split()
        if len(words) < 20:
            return 0.0

        # Remove punctuation for fair comparison.
        translator = str.maketrans("", "", string.punctuation)
        cleaned = [w.translate(translator) for w in words if w.translate(translator)]

        if not cleaned:
            return 0.0

        unique = len(set(cleaned))
        total = len(cleaned)
        ttr = unique / total

        # Score: distance from the ideal human range (0.45-0.65).
        if 0.45 <= ttr <= 0.65:
            return 0.0

        if ttr < 0.35:
            return 1.0  # Very repetitive.
        if ttr < 0.45:
            return (0.45 - ttr) / 0.1  # Slightly repetitive.

        # TTR > 0.65 - unusually varied vocabulary.
        return min(1.0, (ttr - 0.65) / 0.15)

    # ── Format analysis ─────────────────────────────────────────────────────────

    def _analyse_format(self, text: str) -> list[str]:
        """Check for formatting patterns typical of AI-generated content.

        Returns:
            A list of warning strings (may be empty).
        """
        warnings: list[str] = []
        lines = text.split("\n")

        # Check for excessive bullet points.
        bullet_lines = sum(1 for line in lines if line.strip().startswith(("- ", "* ", "•")))
        total_content_lines = sum(1 for line in lines if line.strip())
        if total_content_lines > 5 and bullet_lines / total_content_lines > 0.7:
            warnings.append("Excessive bullet-point usage - intersperse with paragraph text")

        # Check for uniform paragraph length.
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) > 2:
            para_lengths = [len(p.split()) for p in paragraphs]
            if all(30 <= pl <= 60 for pl in para_lengths):
                warnings.append("All paragraphs are similar length - vary paragraph structure")

        # Check for certain markdown patterns that look templated.
        if re.search(r"\*\*Summary\*\*|\*\*Professional Summary\*\*", text):
            pass  # This is normal for resumes.

        return warnings

    # ── Utility ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences using a simple heuristic.

        Args:
            text: The input text.

        Returns:
            A list of sentence strings.
        """
        # Handle common abbreviations to avoid false splits.
        text = re.sub(r"\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|Inc|Corp|Ltd|Co)\.", r"\1<ABBR>", text)
        # Split on sentence-ending punctuation.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        # Restore abbreviations.
        sentences = [s.replace("<ABBR>", ".") for s in sentences]
        return [s.strip() for s in sentences if s.strip()]

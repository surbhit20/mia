import re
import string

from rapidfuzz import fuzz

def _normalize(text: str) -> str:
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()

class WakeWordMatcher:
    """Fuzzy wake-word detection over a sliding window of the transcript.

    Scoring uses fuzz.ratio, NOT fuzz.partial_ratio. partial_ratio finds the
    best-matching substring and ignores everything around it, so a longer word
    that happens to contain the right letters scores as high as the real
    phrase: "t(he mi)gration" hit 77 against "hey mia" and woke mia in the
    middle of a live meeting. No threshold fixes that -- a genuine attempt
    misheard as "hemia" scored 75, BELOW the false positive. fuzz.ratio
    compares whole strings, so extra characters cost score, which separates
    real attempts (80-100) from incidental matches (29-60) with room to spare.
    """

    def __init__(self, wake_word: str, threshold: float = 0.75):
        self._wake_word = _normalize(wake_word)
        self._window_size = len(self._wake_word.split())
        self._threshold_pct = threshold * 100

    def matches(self, text: str) -> bool:
        words = _normalize(text).split()
        if len(words) < self._window_size:
            return fuzz.ratio(" ".join(words), self._wake_word) >= self._threshold_pct
        for i in range(len(words) - self._window_size + 1):
            window = " ".join(words[i : i + self._window_size])
            if fuzz.ratio(window, self._wake_word) >= self._threshold_pct:
                return True
        return False

    def strip_wake_phrase(self, text: str) -> str:
        """`text`, normalized, with the first matching wake-word window removed.

        Lets the caller tell a bare trigger ("hey mia" and nothing else, or a
        near-miss on background speech) from a real command Claude simply
        couldn't act on: only the former should be answered with silence.
        Returns the normalized text unchanged when no window matches.
        """
        words = _normalize(text).split()
        for i in range(max(len(words) - self._window_size + 1, 1)):
            window = " ".join(words[i : i + self._window_size])
            if fuzz.ratio(window, self._wake_word) >= self._threshold_pct:
                return " ".join(words[:i] + words[i + self._window_size :])
        return " ".join(words)


def is_self_echo(transcript: str, spoken_text: str, threshold: float = 0.75) -> bool:
    """True if `transcript` looks like a fragment of `spoken_text` -- used to
    filter out mia's own TTS looping back through capture rather than being
    treated as a barge-in attempt or a new command. On the local BlackHole
    path used by demo_standalone.py, this loopback is guaranteed: BlackHole
    routes injected audio back into what mia captures, by design. On the
    Recall.ai path, whether Recall's audio_mixed_raw stream includes mia's
    own spoken output at all is unverified as of this branch -- so this
    filter is load-bearing if that stream does echo mia's output, and a
    harmless no-op if it doesn't. Reuses the same fuzz.partial_ratio approach
    as WakeWordMatcher: it finds the best-matching substring of the longer
    string against the shorter one, which is exactly "does this incoming
    fragment look like part of what's currently playing"."""
    return fuzz.partial_ratio(_normalize(transcript), _normalize(spoken_text)) >= threshold * 100

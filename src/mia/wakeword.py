import re
import string

from rapidfuzz import fuzz

def _normalize(text: str) -> str:
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()

class WakeWordMatcher:
    def __init__(self, wake_word: str, threshold: float = 0.75):
        self._wake_word = _normalize(wake_word)
        self._window_size = len(self._wake_word.split())
        self._threshold_pct = threshold * 100

    def matches(self, text: str) -> bool:
        words = _normalize(text).split()
        if len(words) < self._window_size:
            return fuzz.partial_ratio(" ".join(words), self._wake_word) >= self._threshold_pct
        for i in range(len(words) - self._window_size + 1):
            window = " ".join(words[i : i + self._window_size])
            if fuzz.partial_ratio(window, self._wake_word) >= self._threshold_pct:
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
            if fuzz.partial_ratio(window, self._wake_word) >= self._threshold_pct:
                return " ".join(words[:i] + words[i + self._window_size :])
        return " ".join(words)

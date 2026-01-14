"""
Real-time transcript analysis for mention markets.

Mention markets bet on whether a specific word/phrase will be said during an event
(press conference, speech, interview, etc.).

Strategy:
1. Stream live transcript (or poll frequently)
2. Track cumulative word counts and context
3. Calculate P(word mentioned | words so far, time remaining, topic drift)
4. React to price movements - are they justified by transcript changes?

Key insight from traders: Think in conditional probabilities.
P(word | transcript so far) changes as the event progresses.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable
from collections import Counter
import math


@dataclass
class TranscriptState:
    """Current state of a live transcript."""
    text: str = ""
    word_count: int = 0
    word_frequencies: Counter = field(default_factory=Counter)
    start_time: Optional[datetime] = None
    last_update: Optional[datetime] = None

    # Estimated event parameters
    expected_duration_minutes: float = 60
    expected_total_words: int = 6000  # ~100 words/min for 60 min

    def update(self, new_text: str):
        """Update transcript with new text."""
        self.text = new_text
        words = self._tokenize(new_text)
        self.word_count = len(words)
        self.word_frequencies = Counter(w.lower() for w in words)
        self.last_update = datetime.utcnow()

    def _tokenize(self, text: str) -> list[str]:
        """Simple word tokenization."""
        return re.findall(r'\b\w+\b', text.lower())

    @property
    def progress(self) -> float:
        """Estimated progress through event (0-1)."""
        if self.expected_total_words <= 0:
            return 0
        return min(1.0, self.word_count / self.expected_total_words)

    @property
    def words_remaining(self) -> int:
        """Estimated words remaining."""
        return max(0, self.expected_total_words - self.word_count)


@dataclass
class MentionTarget:
    """A word/phrase we're tracking for mention."""
    phrase: str
    market_id: str
    current_price: float  # YES price in cents

    # Historical stats (if available)
    base_rate_per_event: Optional[float] = None  # How often said in similar events
    typical_position: Optional[float] = None  # Usually said at what % through event

    def mentioned_count(self, state: TranscriptState) -> int:
        """Count how many times phrase appears in transcript."""
        pattern = re.compile(re.escape(self.phrase.lower()), re.IGNORECASE)
        return len(pattern.findall(state.text))

    def is_mentioned(self, state: TranscriptState) -> bool:
        """Check if phrase has been mentioned at all."""
        return self.mentioned_count(state) > 0


class MentionProbabilityModel:
    """
    Model for P(word mentioned by end | transcript so far).

    Uses a simple survival analysis approach:
    - If word hasn't been said yet, probability decreases as event progresses
    - If word is typically said early and we're late, probability drops faster
    - Topic drift affects probability (if we're off-topic, less likely)
    """

    def __init__(self):
        # Topic-related words that suggest we're on track
        self.topic_indicators: dict[str, list[str]] = {}

    def add_topic_indicators(self, phrase: str, related_words: list[str]):
        """Add words that indicate we're on-topic for a phrase."""
        self.topic_indicators[phrase.lower()] = [w.lower() for w in related_words]

    def topic_relevance(self, target: MentionTarget, state: TranscriptState) -> float:
        """
        Estimate how on-topic the conversation is for the target phrase.
        Returns 0-1 score.
        """
        indicators = self.topic_indicators.get(target.phrase.lower(), [])
        if not indicators:
            return 0.5  # No info, assume neutral

        # Count how many topic indicators have appeared
        hits = sum(1 for w in indicators if state.word_frequencies.get(w, 0) > 0)
        return hits / len(indicators) if indicators else 0.5

    def survival_probability(
        self,
        target: MentionTarget,
        state: TranscriptState
    ) -> float:
        """
        Calculate P(mentioned by end | not mentioned yet, progress).

        Uses exponential decay model with topic adjustment.
        """
        if target.is_mentioned(state):
            return 1.0  # Already mentioned

        progress = state.progress
        words_remaining = state.words_remaining

        # Base rate: probability per word that it gets mentioned
        if target.base_rate_per_event is not None:
            # If we know the base rate, use it
            # P(at least once) = 1 - (1 - p_per_word)^words_remaining
            p_per_word = target.base_rate_per_event / state.expected_total_words
            base_prob = 1 - (1 - p_per_word) ** words_remaining
        else:
            # Assume uniform: if not said by progress p, remaining prob is 1-p
            base_prob = 1 - progress

        # Adjust for typical position
        if target.typical_position is not None:
            # If phrase is typically said early and we're past that point
            if progress > target.typical_position:
                # Reduce probability more aggressively
                late_factor = 1 - (progress - target.typical_position)
                base_prob *= max(0.1, late_factor)

        # Adjust for topic relevance
        topic_score = self.topic_relevance(target, state)
        # High topic relevance boosts probability, low reduces it
        topic_adjustment = 0.5 + topic_score  # 0.5 to 1.5

        adjusted_prob = base_prob * topic_adjustment
        return min(1.0, max(0.0, adjusted_prob))

    def fair_price(self, target: MentionTarget, state: TranscriptState) -> float:
        """Calculate fair YES price in cents."""
        prob = self.survival_probability(target, state)
        return prob * 100

    def edge(self, target: MentionTarget, state: TranscriptState) -> float:
        """Calculate edge (fair - market) in percentage points."""
        fair = self.fair_price(target, state)
        return fair - target.current_price

    def is_price_justified(
        self,
        target: MentionTarget,
        state: TranscriptState,
        price_change: float,
        transcript_changed: bool
    ) -> dict:
        """
        Analyze whether a price movement is justified by transcript changes.

        Returns analysis dict with recommendation.
        """
        fair = self.fair_price(target, state)
        edge = fair - target.current_price

        result = {
            "target": target.phrase,
            "mentioned": target.is_mentioned(state),
            "progress": state.progress,
            "fair_price": fair,
            "market_price": target.current_price,
            "edge": edge,
            "price_change": price_change,
            "transcript_changed": transcript_changed
        }

        if target.is_mentioned(state):
            # Already mentioned - should be near 100
            if target.current_price < 95:
                result["signal"] = "BUY_YES"
                result["reasoning"] = "Phrase mentioned but price not at 100"
            else:
                result["signal"] = "HOLD"
                result["reasoning"] = "Correctly priced after mention"
        elif price_change > 5 and not transcript_changed:
            # Price jumped but transcript didn't change
            result["signal"] = "INVESTIGATE"
            result["reasoning"] = "Price moved without transcript change - insider info?"
        elif abs(edge) > 10:
            if edge > 0:
                result["signal"] = "BUY_YES"
                result["reasoning"] = f"Underpriced by {edge:.1f}%"
            else:
                result["signal"] = "BUY_NO"
                result["reasoning"] = f"Overpriced by {-edge:.1f}%"
        else:
            result["signal"] = "HOLD"
            result["reasoning"] = "Price roughly fair"

        return result


class LiveTranscriptTracker:
    """
    Track live transcripts and mention market opportunities.

    Usage:
        tracker = LiveTranscriptTracker()
        tracker.add_target(MentionTarget("tariff", "TARIFF-MARKET", 45))

        # As transcript updates come in:
        tracker.update_transcript("The president began by discussing...")
        analysis = tracker.analyze_all()
    """

    def __init__(self):
        self.state = TranscriptState()
        self.targets: list[MentionTarget] = []
        self.model = MentionProbabilityModel()
        self.price_history: dict[str, list[tuple[datetime, float]]] = {}
        self._last_transcript = ""

    def add_target(self, target: MentionTarget):
        """Add a mention target to track."""
        self.targets.append(target)
        self.price_history[target.market_id] = [(datetime.utcnow(), target.current_price)]

    def update_transcript(self, text: str):
        """Update with new transcript text."""
        self._last_transcript = self.state.text
        self.state.update(text)

    def update_price(self, market_id: str, price: float):
        """Update market price for a target."""
        for target in self.targets:
            if target.market_id == market_id:
                old_price = target.current_price
                target.current_price = price
                self.price_history[market_id].append((datetime.utcnow(), price))
                return old_price
        return None

    def analyze_all(self) -> list[dict]:
        """Analyze all targets and return recommendations."""
        results = []
        transcript_changed = self.state.text != self._last_transcript

        for target in self.targets:
            history = self.price_history.get(target.market_id, [])
            price_change = 0
            if len(history) >= 2:
                price_change = history[-1][1] - history[-2][1]

            analysis = self.model.is_price_justified(
                target, self.state, price_change, transcript_changed
            )
            results.append(analysis)

        return results

    def get_alerts(self, min_edge: float = 10) -> list[dict]:
        """Get only actionable alerts (significant edge)."""
        all_analysis = self.analyze_all()
        return [a for a in all_analysis if abs(a["edge"]) >= min_edge]


# Example usage for integration with live streams
class TranscriptSource:
    """Base class for transcript sources."""

    async def stream(self) -> str:
        """Yield transcript updates as they come in."""
        raise NotImplementedError


class YouTubeLiveTranscript(TranscriptSource):
    """
    Get live captions from YouTube streams.

    Note: Requires youtube-transcript-api or similar.
    Live captions have ~3-5 second delay.
    """

    def __init__(self, video_id: str):
        self.video_id = video_id

    async def stream(self) -> str:
        # Placeholder - would need actual YouTube API integration
        # Options:
        # 1. youtube-transcript-api (for VOD, not live)
        # 2. pytube + caption parsing
        # 3. Whisper on audio stream for real-time
        raise NotImplementedError(
            "YouTube live transcription requires additional setup. "
            "Consider using Whisper for real-time audio transcription."
        )


class WhisperLiveTranscript(TranscriptSource):
    """
    Real-time transcription using Whisper.

    Can work with any audio source (YouTube, Twitch, direct streams).
    """

    def __init__(self, audio_source: str, model_size: str = "base"):
        self.audio_source = audio_source
        self.model_size = model_size

    async def stream(self) -> str:
        # Placeholder - would integrate with whisper.cpp or faster-whisper
        # for streaming transcription
        raise NotImplementedError(
            "Whisper live transcription requires whisper.cpp or faster-whisper. "
            "See: https://github.com/ggerganov/whisper.cpp"
        )

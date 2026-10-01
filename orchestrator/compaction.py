"""
Deterministic context compaction — no LLM, no API calls.

ADK's default events summarizer (LlmEventSummarizer) calls a model to compact
history, which would add requests and worsen the free-tier rate limit. This
summarizer instead compacts a window of events into a single event WITHOUT a
model call: it keeps each event's text (so downstream agents don't lose
information) and truncates only the bulky tool chatter (raw google_search
results, long tool responses). It's wired onto the App in agent.py via
EventsCompactionConfig.
"""
from typing import Optional

from google.adk.apps.base_events_summarizer import BaseEventsSummarizer
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types

_MAX_PART_CHARS = 2000  # cap verbose text/tool outputs


class TruncatingSummarizer(BaseEventsSummarizer):
    """Compacts events deterministically (text preserved, tool noise trimmed)."""

    async def maybe_summarize_events(
        self, *, events: list[Event]
    ) -> Optional[Event]:
        if not events:
            return None

        lines: list[str] = []
        for e in events:
            if not (e.content and e.content.parts):
                continue
            for part in e.content.parts:
                if getattr(part, "text", None):
                    text = part.text
                    if len(text) > _MAX_PART_CHARS:
                        text = (text[:_MAX_PART_CHARS]
                                + f"... [truncated {len(text) - _MAX_PART_CHARS} chars]")
                    lines.append(f"{e.author}: {text}")
                elif getattr(part, "function_call", None):
                    lines.append(f"{e.author} called tool {part.function_call.name}")
                elif getattr(part, "function_response", None):
                    lines.append(f"(tool response to {e.author} omitted)")

        if not lines:
            return None

        compacted_content = types.Content(
            role="model",
            parts=[types.Part(text="[Compacted earlier context]\n" + "\n".join(lines))],
        )
        compaction = EventCompaction(
            start_timestamp=events[0].timestamp,
            end_timestamp=events[-1].timestamp,
            compacted_content=compacted_content,
        )
        return Event(
            author="user",
            actions=EventActions(compaction=compaction),
            invocation_id=Event.new_id(),
        )

"""Turn a finished meeting's transcript into the body of a summary doc."""

from mia.llm import ToolCallResult

# An hour of meeting is on the order of 10k input tokens, and the output
# carries a prose summary plus a checklist. dispatch_command's 256 is sized
# for a single spoken sentence and is nowhere near enough here.
_MAX_TOKENS = 4000

_SYSTEM = (
    "You write concise post-meeting summaries as HTML fragments. "
    "Output only HTML -- no markdown, no code fences, no commentary before "
    "or after. Use <h1> for the title, <p> for prose, <h2>Action Items</h2> "
    "for the checklist, and a <ul> of <li> items beneath it."
)


def _format_actions(actions_taken: list[ToolCallResult]) -> str:
    # Only turns that actually ran a tool are completions. dispatch_command
    # returns tool_name=None for turns where mia merely spoke -- a reply, or a
    # clarifying question -- and feeding those in as "already completed" is how
    # a question becomes a ticked item in the user's doc.
    executed = [action for action in actions_taken if action.tool_name is not None]
    if not executed:
        return "(none -- mia executed no tools during this meeting)"
    return "\n".join(
        f"- tool={action.tool_name} result={action.confirmation}" for action in executed
    )


def summarize(
    client,
    transcript_text: str,
    present: list[str],
    invited: list[str],
    actions_taken: list[ToolCallResult],
) -> str:
    """One Claude call returning the doc body as HTML.

    `actions_taken` is ground truth for what was completed: only these may be
    ticked. It also deduplicates -- a commitment discussed in the transcript
    and executed by mia must appear once, ticked, not twice.
    """
    prompt = (
        "Summarize this meeting.\n\n"
        f"People detected speaking: {', '.join(present) or 'unknown'}\n"
        f"People invited on the calendar: {', '.join(invited) or 'unknown'}\n\n"
        "The invited list is context about who was expected. Do NOT use it to "
        "guess the identity of a speaker labelled 'Speaker <number>' -- those "
        "labels mean the platform gave no name, and a confident wrong "
        "attribution is worse than an anonymous one. You may name such a "
        "speaker only if the transcript itself makes it unambiguous, for "
        "example if someone addresses them by name.\n\n"
        "Actions mia already completed during the meeting (ground truth):\n"
        f"{_format_actions(actions_taken)}\n\n"
        "Transcript:\n"
        f"{transcript_text}\n\n"
        "Produce:\n"
        "1. An <h1> title for the meeting.\n"
        "2. A few <p> paragraphs summarizing what was discussed and decided.\n"
        "3. An <h2>Action Items</h2> section with a single <ul> checklist.\n\n"
        "Checklist rules:\n"
        "- One list, not two.\n"
        "- Start a completed item with '[x]' and end it with ' - done by "
        "Mia'. Mark an item done ONLY if it appears in the ground-truth "
        "actions above. Never infer completion from the transcript.\n"
        "- Start every other item with '[ ]'.\n"
        "- If a commitment in the transcript matches a completed action, list "
        "it once as the completed item. Never list the same commitment twice.\n"
        "- Attribute an item to a person when the transcript makes the owner "
        "clear."
    )

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()

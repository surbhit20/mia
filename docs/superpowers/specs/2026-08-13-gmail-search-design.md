# Gmail Search Tool + Conversational Memory — Design

Date: 2026-08-13
Status: Approved, not yet implemented

## Context

This is the first sub-project of the broader "Google-native PA" vision
scoped out at the start of the overall `mia` project (Gmail triage,
Calendar automation beyond blocking, Drive/Docs automation, a cross-cutting
task list — each its own future sub-project). It adds a voice command that
finds and summarizes recent Gmail content, and — because a search tool
inherently produces ambiguous or empty results sometimes — upgrades
`dispatch_command` from a stateless, single-shot call into one that
remembers the last few exchanges of the current call, so a spoken follow-up
can refer back to a prior search.

## Goal

"Hey Mia, find that email from last week about the project proposal" →
mia searches Gmail, speaks a natural summary of what it found. If the
result is ambiguous or empty, mia says so, and a follow-up wake-word
command ("Hey Mia, the one from Bob") resolves using the prior search's
context rather than being parsed as a brand-new, disconnected command.

## Part 1: `find_gmail_messages` tool

**New file**: `src/mia/tools/gmail_tool.py`,
`build_gmail_search_tool(gmail_service, anthropic_client) -> Tool`

**Input schema**: `{query: string}`. Claude's own tool-call already
translates the natural-language ask into Gmail's search query syntax
(`after:2026/08/06 project proposal`), the same way it already translates
"3 PM tomorrow" into an ISO datetime for the calendar tool — no new NL
parsing code is needed on our side.

**Handler**:
1. `gmail_service.users().messages().list(q=query, maxResults=5)` — capped
   at 5 results regardless of how wide the query is. This is the actual
   cost/latency control: Gmail API search itself is free and unrestricted
   by time range, but each result gets summarized by a second Claude call,
   so cost scales with result *count*, not the calendar span a query covers.
2. For each returned message ID, fetch subject/sender/snippet.
3. **Zero results** → return `"I couldn't find anything matching that."`
   directly; no second Claude call needed.
4. **Results found** → a second, separate Claude call *inside the handler*
   (distinct from the outer `dispatch_command` call that selected this
   tool): summarize the results into 2-3 spoken sentences, phrased for
   listening, not reading. If more than one result is a plausible match,
   the summarization prompt instructs Claude to name the distinguishing
   details (sender, rough date) so the user has enough to say a specific
   follow-up. That summary is the tool's return value — same contract as
   `calendar_tool`'s confirmation string.

**Auth**: adds the `https://www.googleapis.com/auth/gmail.readonly` scope
(read-only, least-privilege, matching the `calendar.events` precedent) to
`main.py`'s `_SCOPES` list. `_authorize_calendar` is renamed to
`_authorize_google` and returns the OAuth `Credentials` object directly
(instead of a built Calendar service) — `run()` then builds both the
`calendar` and `gmail` service clients from those one shared credentials,
since a single OAuth grant now covers both scopes. Widening the scope list
means the existing cached `~/.mia/token.json` needs one re-consent — delete
it once, and the next run performs the OAuth flow again against the
combined scope list.

## Part 2: `dispatch_command` becomes stateful

**Why**: a search tool's results are inherently sometimes ambiguous or
empty. Without memory, resolving that requires the user to repeat a fully
self-contained command each time. With memory, a short follow-up works
naturally, the same way a real conversation would.

**Design**:
- `dispatch_command` (in `llm.py`) gains a conversation-history parameter:
  a bounded, in-memory list of prior turns (user messages, Claude's
  responses, and the tool-use/tool-result block pairs the Anthropic API's
  multi-turn tool-calling contract requires) that gets passed to
  `client.messages.create()` on every call, not just the latest command.
- **Lifetime**: created fresh at the start of each joined call in
  `_run_call_loop`, discarded when the bot leaves — same per-call scoping
  as `turn_state`, `wake_word`, and `command_buffer` already have. Never
  written to disk, consistent with the project's standing "never persist
  meeting content" constraint.
- **Bounded window**: capped at the last 3 exchanges. Keeps token cost
  predictable over a long meeting and avoids Claude anchoring on stale,
  unrelated earlier context.
- **Still wake-word-gated, every turn, no exception.** A follow-up like
  "Hey Mia, the one from Bob" still requires the wake word. mia does not
  "stay in a conversation" and listen without an explicit trigger after
  asking a clarifying question — that would mean processing speech without
  an explicit invocation, breaking the core safety property the whole
  design has held since the original pivot away from a transcript-driven
  bot. The memory means Claude *understands* the follow-up refers to the
  earlier search; it does not mean mia starts eavesdropping between turns.
- This capability lives in `dispatch_command` (not inside the Gmail tool)
  because it benefits any future tool, not just this one. A bare command
  with no relevant history (e.g. "block 30 minutes") is unaffected — the
  history is additional context Claude may or may not find relevant, never
  a requirement.

## Error handling

- Gmail API errors (auth, rate limit, network) propagate as an exception,
  caught by `dispatch_command`'s existing bare try/except around
  `tool.handler()`, falling back to the generic "Sorry, that didn't work"
  response — the same pattern `calendar_tool` already relies on, no new
  handling needed.
- Zero-result search is a valid, expected outcome, not an error — handled
  explicitly in the tool (see Part 1, step 3) rather than surfacing as a
  failure.

## Testing

- `gmail_tool.py`: same TDD pattern as `calendar_tool.py` — mock
  `gmail_service` and `anthropic_client`, assert the handler calls the
  right Gmail API methods with the query, calls Claude for summarization
  only when results exist, and returns the direct "couldn't find anything"
  string when they don't.
- `dispatch_command`'s new history behavior: unit test that a second call
  with history includes the prior turn in the messages sent to Claude, and
  that history beyond the bounded window gets trimmed.
- Live-only verification (can't be meaningfully unit tested): a real query
  against a real inbox, confirming the spoken summary is coherent, and a
  real ambiguous-result follow-up resolves correctly using memory.

## Explicit scope

**In**: Gmail search + spoken summary of matches, conversational memory in
`dispatch_command` (bounded, per-call, still wake-word-gated every turn).

**Out**: sending/drafting replies, marking messages read/archived,
attachments, full-body retrieval beyond snippet-level summary, memory that
persists across separate calls/meetings — these are candidates for future
sub-projects, not part of this one.

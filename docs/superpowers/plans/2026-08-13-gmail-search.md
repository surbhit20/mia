# Gmail Search Tool + Conversational Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `find_gmail_messages` voice command that searches Gmail and speaks a natural summary of the results, and upgrade `dispatch_command` from a stateless single-shot call into one with bounded, per-call conversational memory so an ambiguous search can be resolved with a wake-word-gated follow-up.

**Architecture:** A new `Tool` (`find_gmail_messages`) follows the exact same factory pattern as the existing `block_calendar_slot` tool, but its handler makes a second, internal Claude call to summarize variable search results into spoken language (since `dispatch_command` itself is single-shot and never re-queries Claude with a tool's raw output). Separately, `dispatch_command` gains a `ConversationHistory` parameter — a small, bounded, in-memory list of prior turns in the Anthropic Messages API's multi-turn tool-use format — that callers create once per joined call and pass in on every turn.

**Tech Stack:** Python, `google-api-python-client` (Gmail API v1, already a project dependency via the Calendar API usage), `anthropic` SDK (already a dependency).

## Global Constraints

- Gmail search is capped at 5 results (`maxResults=5`) regardless of the query's time range — this is the actual cost/latency control, since Gmail API search itself is free and unrestricted by date range, but each result gets summarized by a second Claude call.
- Conversational memory is bounded to the last 3 exchanges, created fresh per joined call, discarded when the bot leaves. Never written to disk.
- Every command remains wake-word-gated, including follow-ups that rely on conversational memory — mia never processes speech without an explicit trigger.
- New OAuth scope: `https://www.googleapis.com/auth/gmail.readonly` (read-only, least-privilege). Existing cached `~/.mia/token.json` needs one re-consent after this change (delete it once; the next run re-authorizes with the combined scope list).

---

### Task 1: `find_gmail_messages` tool

**Files:**
- Create: `src/mia/tools/gmail_tool.py`
- Test: `tests/test_tools_gmail.py`

**Interfaces:**
- Consumes: `Tool` dataclass from `mia.tools.base` (`name: str`, `description: str`, `input_schema: dict`, `handler: Callable[[dict], str]`) — already defined, no changes needed.
- Produces: `build_gmail_search_tool(gmail_service, anthropic_client) -> Tool`, with `tool.name == "find_gmail_messages"`. Imported by Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_gmail.py`:

```python
from unittest.mock import MagicMock

from mia.tools.gmail_tool import build_gmail_search_tool


def test_tool_metadata():
    tool = build_gmail_search_tool(MagicMock(), MagicMock())
    assert tool.name == "find_gmail_messages"
    assert tool.input_schema["required"] == ["query"]


def test_handler_returns_direct_message_when_no_results():
    gmail_service = MagicMock()
    gmail_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
    anthropic_client = MagicMock()

    tool = build_gmail_search_tool(gmail_service, anthropic_client)
    result = tool.handler({"query": "nonexistent topic"})

    assert result == "I couldn't find anything matching that."
    anthropic_client.messages.create.assert_not_called()


def test_handler_summarizes_results_via_claude():
    gmail_service = MagicMock()
    list_execute = gmail_service.users.return_value.messages.return_value.list.return_value.execute
    list_execute.return_value = {"messages": [{"id": "msg1", "threadId": "t1"}]}
    get_execute = gmail_service.users.return_value.messages.return_value.get.return_value.execute
    get_execute.return_value = {
        "id": "msg1",
        "snippet": "Here's the proposal draft, let me know what you think.",
        "payload": {
            "headers": [
                {"name": "From", "value": "Bob <bob@example.com>"},
                {"name": "Subject", "value": "Project Proposal"},
            ]
        },
    }

    anthropic_client = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I found one email from Bob about the project proposal."
    response = MagicMock()
    response.content = [text_block]
    anthropic_client.messages.create.return_value = response

    tool = build_gmail_search_tool(gmail_service, anthropic_client)
    result = tool.handler({"query": "project proposal"})

    assert result == "I found one email from Bob about the project proposal."
    gmail_service.users.return_value.messages.return_value.list.assert_called_once_with(
        userId="me", q="project proposal", maxResults=5
    )
    gmail_service.users.return_value.messages.return_value.get.assert_called_once_with(
        userId="me", id="msg1", format="metadata", metadataHeaders=["From", "Subject"]
    )
    _, kwargs = anthropic_client.messages.create.call_args
    prompt_text = kwargs["messages"][0]["content"]
    assert "Bob <bob@example.com>" in prompt_text
    assert "Project Proposal" in prompt_text
    assert "project proposal" in prompt_text  # the original query is included


def test_handler_surfaces_gmail_api_error_as_exception():
    gmail_service = MagicMock()
    gmail_service.users.return_value.messages.return_value.list.return_value.execute.side_effect = RuntimeError("api down")
    anthropic_client = MagicMock()

    tool = build_gmail_search_tool(gmail_service, anthropic_client)
    try:
        tool.handler({"query": "x"})
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools_gmail.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mia.tools.gmail_tool'`

- [ ] **Step 3: Write the implementation**

Create `src/mia/tools/gmail_tool.py`:

```python
from mia.tools.base import Tool

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "A Gmail search query using Gmail's own search operators "
                "(from:, subject:, after:, before:, etc.), translated from "
                "the user's spoken request."
            ),
        },
    },
    "required": ["query"],
}


def _fetch_message_summaries(gmail_service, query: str, max_results: int = 5) -> list[dict]:
    list_response = (
        gmail_service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    message_refs = list_response.get("messages", [])

    summaries = []
    for ref in message_refs:
        message = (
            gmail_service.users()
            .messages()
            .get(
                userId="me",
                id=ref["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}
        summaries.append(
            {
                "from": headers.get("From", "(unknown sender)"),
                "subject": headers.get("Subject", "(no subject)"),
                "snippet": message.get("snippet", ""),
            }
        )
    return summaries


def _summarize_results(anthropic_client, query: str, summaries: list[dict]) -> str:
    listing = "\n".join(
        f"- From: {s['from']}, Subject: {s['subject']}, Preview: {s['snippet']}"
        for s in summaries
    )
    prompt = (
        f'A user searched their email for: "{query}"\n'
        f"Here are the top matches:\n{listing}\n\n"
        "Summarize these results in 2-3 sentences meant to be spoken aloud, "
        "not read as a list. If more than one result could be the one they "
        "meant, mention distinguishing details (sender, rough date or topic) "
        "so they can name a specific one in a follow-up."
    )
    response = anthropic_client.messages.create(
        model="claude-sonnet-5",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text_block = next((b for b in response.content if b.type == "text"), None)
    return text_block.text if text_block is not None else "I found some matches but couldn't summarize them."


def build_gmail_search_tool(gmail_service, anthropic_client) -> Tool:
    def handler(args: dict) -> str:
        query = args["query"]
        summaries = _fetch_message_summaries(gmail_service, query)
        if not summaries:
            return "I couldn't find anything matching that."
        return _summarize_results(anthropic_client, query, summaries)

    return Tool(
        name="find_gmail_messages",
        description=(
            "Search the user's Gmail for messages matching a query. Use "
            "this when the user asks to find, look up, or recall an email "
            "or something someone said in email."
        ),
        input_schema=_SCHEMA,
        handler=handler,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools_gmail.py -v`
Expected: PASS, 4/4

- [ ] **Step 5: Commit**

```bash
git add src/mia/tools/gmail_tool.py tests/test_tools_gmail.py
git commit -m "feat: add find_gmail_messages tool"
```

---

### Task 2: Conversational memory in `dispatch_command`

**Files:**
- Modify: `src/mia/llm.py` (full file — see below)
- Modify: `tests/test_llm.py` (full file — see below)

**Interfaces:**
- Consumes: existing `ToolRegistry`/`Tool` from `mia.tools.base`; existing `_system_prompt()` (private to `llm.py`, unchanged).
- Produces:
  - `ConversationHistory` class in `mia.llm`, constructor `ConversationHistory(max_exchanges: int = 3)`, method `.as_messages() -> list[dict]`.
  - `dispatch_command(client, registry: ToolRegistry, command_text: str, history: ConversationHistory) -> ToolCallResult` — note the new required 4th positional parameter; every existing call site breaks until updated (Task 3 fixes the only two call sites: `main.py` and `demo_standalone.py`).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_llm.py` in full:

```python
from datetime import datetime
from unittest.mock import MagicMock

from mia.llm import ConversationHistory, dispatch_command
from mia.timeutil import local_timezone_label
from mia.tools.base import Tool, ToolRegistry


def _mock_tool_use_response(tool_name, tool_input, block_id="toolu_1"):
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = block_id
    response = MagicMock()
    response.content = [block]
    return response


def _mock_text_only_response():
    block = MagicMock()
    block.type = "text"
    response = MagicMock()
    response.content = [block]
    return response


def test_dispatches_to_matching_tool():
    registry = ToolRegistry()
    registry.register(Tool(
        name="block_calendar_slot",
        description="d",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: f"blocked {args['title']}",
    ))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response(
        "block_calendar_slot", {"title": "Focus time"}
    )
    history = ConversationHistory()

    result = dispatch_command(client, registry, "block an hour for focus time", history)

    assert result.tool_name == "block_calendar_slot"
    assert result.confirmation == "blocked Focus time"
    _, kwargs = client.messages.create.call_args
    assert kwargs["tools"] == registry.anthropic_tool_specs()
    today = datetime.now().astimezone().date().isoformat()
    assert today in kwargs["system"]
    assert local_timezone_label() in kwargs["system"]


def test_no_tool_use_returns_fallback():
    registry = ToolRegistry()
    client = MagicMock()
    client.messages.create.return_value = _mock_text_only_response()
    history = ConversationHistory()

    result = dispatch_command(client, registry, "what's the weather", history)

    assert result.tool_name is None
    assert "didn't catch" in result.confirmation


def test_handler_exception_returns_failure_notice():
    registry = ToolRegistry()
    def boom(args):
        raise RuntimeError("api down")
    registry.register(Tool(name="block_calendar_slot", description="d", input_schema={}, handler=boom))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response("block_calendar_slot", {})
    history = ConversationHistory()

    result = dispatch_command(client, registry, "block time", history)

    assert result.tool_name == "block_calendar_slot"
    assert "didn't work" in result.confirmation


def test_second_call_includes_first_turn_in_history():
    registry = ToolRegistry()
    registry.register(Tool(
        name="find_gmail_messages",
        description="d",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: "found 2 emails about that",
    ))
    client = MagicMock()
    client.messages.create.return_value = _mock_tool_use_response(
        "find_gmail_messages", {"query": "proposal"}, block_id="toolu_1"
    )
    history = ConversationHistory()

    dispatch_command(client, registry, "find that email about the proposal", history)

    client.messages.create.return_value = _mock_tool_use_response(
        "find_gmail_messages", {"query": "proposal from bob"}, block_id="toolu_2"
    )
    dispatch_command(client, registry, "the one from bob", history)

    _, kwargs = client.messages.create.call_args
    messages = kwargs["messages"]
    # first user turn, first assistant turn, first tool_result, second user turn
    assert len(messages) == 4
    assert messages[0] == {"role": "user", "content": "find that email about the proposal"}
    assert messages[1]["role"] == "assistant"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert messages[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert messages[2]["content"][0]["content"] == "found 2 emails about that"
    assert messages[3] == {"role": "user", "content": "the one from bob"}


def test_history_trims_to_bounded_window():
    registry = ToolRegistry()
    registry.register(Tool(
        name="find_gmail_messages",
        description="d",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: "ok",
    ))
    client = MagicMock()
    history = ConversationHistory(max_exchanges=2)

    for i in range(3):
        client.messages.create.return_value = _mock_tool_use_response(
            "find_gmail_messages", {"query": f"q{i}"}, block_id=f"toolu_{i}"
        )
        dispatch_command(client, registry, f"command {i}", history)

    # only the last 2 exchanges (4 messages) are kept, plus this new call's
    # own user message -- but we only assert on history.as_messages() here,
    # which excludes the not-yet-sent new message.
    kept_messages = history.as_messages()
    assert len(kept_messages) == 6  # 2 exchanges x 3 messages each
    assert kept_messages[0] == {"role": "user", "content": "command 1"}


def test_history_omits_tool_result_when_no_tool_was_used():
    registry = ToolRegistry()
    client = MagicMock()
    client.messages.create.return_value = _mock_text_only_response()
    history = ConversationHistory()

    dispatch_command(client, registry, "what's the weather", history)

    messages = history.as_messages()
    assert len(messages) == 2  # user + assistant only, no tool_result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm.py -v`
Expected: FAIL — `ImportError: cannot import name 'ConversationHistory'`, and the three pre-existing tests fail with `TypeError: dispatch_command() missing 1 required positional argument: 'history'`.

- [ ] **Step 3: Write the implementation**

Replace `src/mia/llm.py` in full:

```python
from dataclasses import dataclass, field
from datetime import datetime

from mia.timeutil import local_timezone_label
from mia.tools.base import ToolRegistry


@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str | None
    confirmation: str


class ConversationHistory:
    """Bounded, in-memory record of prior dispatch_command turns for one
    joined call. Never persisted to disk. Each remembered exchange is
    stored in the Anthropic Messages API's own multi-turn tool-use format,
    so it can be prepended directly to the next call's `messages` list."""

    def __init__(self, max_exchanges: int = 3):
        self._max_exchanges = max_exchanges
        self._exchanges: list[list[dict]] = []

    def as_messages(self) -> list[dict]:
        messages: list[dict] = []
        for exchange in self._exchanges:
            messages.extend(exchange)
        return messages

    def record(
        self,
        user_message: dict,
        assistant_message: dict,
        tool_result_message: dict | None,
    ) -> None:
        exchange = [user_message, assistant_message]
        if tool_result_message is not None:
            exchange.append(tool_result_message)
        self._exchanges.append(exchange)
        if len(self._exchanges) > self._max_exchanges:
            self._exchanges.pop(0)


def _system_prompt() -> str:
    """Ground Claude in the real current date/time.

    Without this the model has no idea what "today" is, so "block 3 PM" would
    be resolved against its training cutoff -- silently creating the event on
    the wrong day (or emitting a naive timestamp the Calendar API rejects).
    """
    now = datetime.now().astimezone()
    return (
        "You are a voice assistant taking spoken commands during a live meeting.\n"
        f"The current date and time is {now.isoformat()}.\n"
        f"The user's local timezone is {local_timezone_label()}.\n"
        "Use that as the reference point for every relative or bare time the "
        "user gives (\"3 PM\", \"tomorrow\", \"in an hour\"): interpret them in "
        "the user's local timezone, and always emit ISO 8601 datetimes that "
        "include the UTC offset -- never a timezone-naive timestamp."
    )


def dispatch_command(
    client, registry: ToolRegistry, command_text: str, history: ConversationHistory
) -> ToolCallResult:
    user_message = {"role": "user", "content": command_text}
    messages = history.as_messages() + [user_message]

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=256,
        system=_system_prompt(),
        tools=registry.anthropic_tool_specs(),
        messages=messages,
    )

    assistant_message = {"role": "assistant", "content": response.content}
    tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)

    if tool_use_block is None:
        history.record(user_message, assistant_message, None)
        return ToolCallResult(tool_name=None, confirmation="Sorry, I didn't catch a command I can act on.")

    tool = registry.get(tool_use_block.name)
    if tool is None:
        confirmation = "Sorry, that didn't work — try again?"
    else:
        try:
            confirmation = tool.handler(tool_use_block.input)
        except Exception:
            confirmation = "Sorry, that didn't work — try again?"

    tool_result_message = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_block.id,
                "content": confirmation,
            }
        ],
    }
    history.record(user_message, assistant_message, tool_result_message)
    return ToolCallResult(
        tool_name=tool.name if tool is not None else tool_use_block.name,
        confirmation=confirmation,
    )
```

Note: every tool-use response gets a recorded `tool_result` — including the
"tool not found" and "handler raised" failure paths — because the Anthropic
API rejects a follow-up request whose history contains a `tool_use` block
with no matching `tool_result`. Only the true no-tool-use case (bare text
response) skips it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm.py -v`
Expected: PASS, 6/6

- [ ] **Step 5: Commit**

```bash
git add src/mia/llm.py tests/test_llm.py
git commit -m "feat: add bounded conversational memory to dispatch_command"
```

---

### Task 3: Wire Gmail search and conversational memory into `main.py` and `demo_standalone.py`

**Files:**
- Modify: `src/mia/main.py:46` (`_SCOPES`), `src/mia/main.py:83-114` (`_authorize_calendar` → `_authorize_google`), `src/mia/main.py:117-135` (`_run_call_loop` setup), `src/mia/main.py:236-263` (`dispatch_command` call site), `src/mia/main.py:326-343` (`run()`)
- Modify: `demo_standalone.py` (import line, service/tool wiring, `dispatch_command` call site)

**Interfaces:**
- Consumes: Task 1's `build_gmail_search_tool(gmail_service, anthropic_client) -> Tool`; Task 2's `ConversationHistory` and the new `dispatch_command(client, registry, command_text, history)` signature.
- Produces: nothing new for later tasks — this is the final integration point. No automated test (consistent with `main.py`'s existing untested, live-verified status); verified by re-running the manual live check from the earlier standalone-demo session.

- [ ] **Step 1: Add the Gmail scope**

In `src/mia/main.py`, change line 46:

```python
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
```

to:

```python
_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
]
```

- [ ] **Step 2: Rename `_authorize_calendar` to `_authorize_google` and return credentials instead of a built service**

In `src/mia/main.py`, replace the `_authorize_calendar` function (originally lines 83-114):

```python
def _authorize_calendar(config: Config):
    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
        if not creds.valid and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_credentials(creds)
            except Exception as exc:
                safe_log("warning", "calendar token refresh failed", error=str(exc))
                creds = None

    if creds is None or not creds.valid:
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": config.google_client_id,
                    "client_secret": config.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            _SCOPES,
        )
        creds = flow.run_local_server(port=0)
        _save_credentials(creds)

    return build("calendar", "v3", credentials=creds)
```

with:

```python
def _authorize_google(config: Config):
    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
        if not creds.valid and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_credentials(creds)
            except Exception as exc:
                safe_log("warning", "google token refresh failed", error=str(exc))
                creds = None

    if creds is None or not creds.valid:
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": config.google_client_id,
                    "client_secret": config.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            _SCOPES,
        )
        creds = flow.run_local_server(port=0)
        _save_credentials(creds)

    return creds
```

(The only change is the function name and the final `return` line — `return creds` instead of `return build("calendar", "v3", credentials=creds)` — plus the log message string, which is cosmetic.)

- [ ] **Step 3: Update imports and add the Gmail tool registration in `run()`**

In `src/mia/main.py`, add to the imports (near the other `mia.tools` imports):

```python
from mia.llm import ConversationHistory, dispatch_command
from mia.tools.gmail_tool import build_gmail_search_tool
```

(The existing `from mia.llm import dispatch_command` line is replaced by the combined import above — `ConversationHistory` now comes from the same module.)

Replace this block inside `run()` (originally lines 339-343):

```python
    calendar_service = _authorize_calendar(config)
    registry = ToolRegistry()
    registry.register(build_calendar_tool(calendar_service))
    anthropic_client = Anthropic(api_key=config.anthropic_api_key)
    state = StateStore(config.state_file)
```

with:

```python
    creds = _authorize_google(config)
    calendar_service = build("calendar", "v3", credentials=creds)
    gmail_service = build("gmail", "v1", credentials=creds)
    anthropic_client = Anthropic(api_key=config.anthropic_api_key)
    registry = ToolRegistry()
    registry.register(build_calendar_tool(calendar_service))
    registry.register(build_gmail_search_tool(gmail_service, anthropic_client))
    state = StateStore(config.state_file)
```

- [ ] **Step 4: Create a `ConversationHistory` per call and pass it to `dispatch_command`**

In `src/mia/main.py`, inside `_run_call_loop` (originally lines 123-126), add a history instance alongside the other per-call state:

```python
    turn_state = TurnStateMachine()
    wake_word = WakeWordMatcher(config.wake_word, threshold=config.fuzzy_threshold)
    command_buffer = CommandBuffer()
    vad = FrameVAD(frame_ms=_FRAME_MS)
    history = ConversationHistory()
```

Then update the `dispatch_command` call site (originally line 238) from:

```python
                    result = dispatch_command(anthropic_client, registry, command_text)
```

to:

```python
                    result = dispatch_command(anthropic_client, registry, command_text, history)
```

- [ ] **Step 5: Verify `main.py` still imports and compiles cleanly**

Run: `python3 -m py_compile src/mia/main.py`
Expected: no output (success)

Run: `python3 -c "import mia.main"`
Expected: no output (success — confirms no import cycle or missing name)

- [ ] **Step 6: Fix `demo_standalone.py`'s now-broken import and wire in Gmail search**

`demo_standalone.py` imports `_authorize_calendar` directly and calls `dispatch_command` with the old 3-argument signature — both now broken by Tasks 2 and 3's changes. Update it to match `main.py`'s new pattern so the standalone demo keeps working and can also demonstrate the new Gmail search.

Change the import block (find the line `from mia.main import _authorize_calendar`) to:

```python
from mia.llm import ConversationHistory, dispatch_command
from mia.main import _authorize_google
from mia.tools.gmail_tool import build_gmail_search_tool
```

(Remove any pre-existing separate `from mia.llm import dispatch_command` line — it's now combined with `ConversationHistory` above.)

Change the calendar-only setup block (find where `_authorize_calendar` and `build_calendar_tool` are called) from:

```python
    calendar_service = _authorize_calendar(config)
    registry = ToolRegistry()
    registry.register(build_calendar_tool(calendar_service))
    anthropic_client = Anthropic(api_key=config.anthropic_api_key)
```

to:

```python
    creds = _authorize_google(config)
    calendar_service = build("calendar", "v3", credentials=creds)
    gmail_service = build("gmail", "v1", credentials=creds)
    anthropic_client = Anthropic(api_key=config.anthropic_api_key)
    registry = ToolRegistry()
    registry.register(build_calendar_tool(calendar_service))
    registry.register(build_gmail_search_tool(gmail_service, anthropic_client))
```

Add the `build` import if not already present:

```python
from googleapiclient.discovery import build
```

Add a `history = ConversationHistory()` line alongside the script's other per-run state (near where `turn_state`, `wake_word`, `command_buffer`, `vad` are constructed), and update its `dispatch_command(...)` call site to pass `history` as the 4th argument, matching Step 4 above.

- [ ] **Step 7: Verify `demo_standalone.py` still imports and compiles cleanly**

Run: `python3 -m py_compile demo_standalone.py`
Expected: no output (success)

Run: `python3 -c "import ast; ast.parse(open('demo_standalone.py').read())"`
Expected: no output (success)

- [ ] **Step 8: Run the full existing test suite to confirm nothing else broke**

Run: `pytest -q`
Expected: all tests pass (the two audio-fixture tests still skip, as before — unrelated to this change)

- [ ] **Step 9: Delete the cached OAuth token so the next run re-consents with the widened scope**

Run: `rm -f ~/.mia/token.json`

- [ ] **Step 10: Commit**

```bash
git add src/mia/main.py demo_standalone.py
git commit -m "feat: wire Gmail search and conversational memory into main.py and the standalone demo"
```

- [ ] **Step 11: Manual live verification**

Run `python3 demo_standalone.py`, complete the OAuth re-consent (now asks for both Calendar and Gmail read access), then try:
1. "Hey Mia, find that email about [something you know is in your inbox from the last week]" — confirm it speaks a coherent summary.
2. A deliberately ambiguous or empty query — confirm it says it couldn't find a clear match, or lists distinguishing details when there are multiple matches.
3. A wake-word-gated follow-up referring back to the first search (e.g. "Hey Mia, the one from \[sender\]") — confirm the response shows it used the prior search's context rather than treating the follow-up as a disconnected, ambiguous new command.
4. A bare "Hey Mia, block 30 minutes for lunch" — confirm the calendar tool still works unaffected by the history changes.

This step has no automated pass/fail — record what actually happened (coherent summary text, whether the follow-up correctly used memory) for the record, the same way Task 19's original live verification was documented rather than unit tested.

---

## Self-Review

**Spec coverage:**
- `find_gmail_messages` tool, capped-at-5 results, zero-result direct return, results-found summarization via a second Claude call → Task 1. ✅
- `gmail.readonly` scope, shared credentials across Calendar and Gmail services → Task 3, Steps 1-3. ✅
- `ConversationHistory`, bounded window, per-call lifetime, proper tool_use/tool_result pairing → Task 2. ✅
- Wake-word-gating unaffected (no changes to `wakeword.py`, `turn_state.py`, or the outer detection loop) → correctly out of scope for all three tasks, since the spec explicitly said memory changes nothing about trigger gating. ✅
- Existing `calendar_tool` unaffected by history → covered by Task 2's `test_dispatches_to_matching_tool` still passing unchanged in behavior, and Task 3 Step 11's manual check #4. ✅

**Placeholder scan:** No TBD/TODO; every step has literal, complete code or an exact runnable command.

**Type consistency:** `Tool`, `ToolRegistry`, `ToolCallResult` signatures match their existing definitions throughout. `dispatch_command`'s new 4th parameter (`history: ConversationHistory`) is consistent across Task 2's implementation and both of Task 3's call sites. `ConversationHistory.as_messages()` / `.record(...)` names are used identically in Task 2's implementation and its tests.

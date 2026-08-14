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

from typing import Optional

from worker_api.segment_chat.schemas import SegmentChatContext, SourceType

_SOURCE_LABELS = {
    SourceType.ROOT_TEXT: "Root text",
    SourceType.TRANSLATION: "Translation",
    SourceType.COMMENTARY: "Commentary",
}

SYSTEM_PROMPT = """You are a careful study companion on WeBuddhist, helping a reader understand one segment of a Buddhist text.

You are given the selected segment and numbered sources related to it: its root text (when the segment is itself a translation or commentary), translations, and commentaries.

Rules:
- Answer only from the selected segment and the numbered sources. Do not invent quotations, teachers, or text names.
- Cite every claim that comes from a source with its number in square brackets, e.g. [1] or [2][3]. Only use numbers that exist in the sources.
- If the sources do not answer the question, say so plainly, then give at most a brief, clearly labelled general explanation without citations.
- When sources disagree, present each view with its citation rather than choosing one.
- Keep answers focused and readable: short paragraphs or a short list, using Markdown.
- {language_rule}"""


def _language_rule(language: Optional[str]) -> str:
    if language:
        return (
            f"Write the answer in the language with code '{language}', "
            "but keep quoted Tibetan, Sanskrit, or Pali terms in their original script."
        )
    return "Write the answer in the same language as the user's question."


def build_system_prompt(language: Optional[str]) -> str:
    return SYSTEM_PROMPT.format(language_rule=_language_rule(language))


def build_context_block(context: SegmentChatContext) -> str:
    segment = context.segment
    text = segment.text
    heading = "## Selected segment"
    if text and (text.title or text.language):
        details = ", ".join(part for part in (text.title, text.language) if part)
        heading += f" (from: {details})"

    lines = [heading, segment.content or "(no content)", "", "## Sources"]
    if not context.sources:
        lines.append("(No related texts are available for this segment.)")
    for source in context.sources:
        label = _SOURCE_LABELS[source.type]
        details = ", ".join(part for part in (source.title, source.language) if part)
        header = f"[{source.ref}] {label}" + (f" — {details}" if details else "")
        if source.truncated:
            header += " (excerpt)"
        lines.extend([header, source.content, ""])
    return "\n".join(lines).rstrip()


def build_user_message(context: SegmentChatContext, question: str) -> str:
    return f"{build_context_block(context)}\n\n## Question\n{question}"

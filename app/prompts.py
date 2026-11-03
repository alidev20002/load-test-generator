"""Prompt templates for LLM-based API dependency verification."""

from __future__ import annotations

PROMPT_V1_JSON = """You are verifying whether two fields observed in HTTP traffic represent a producer-consumer relationship.

### API 1 (Producer / Response):
Endpoint: {producer_endpoint}
Field: <CANDIDATE>{producer_field}</CANDIDATE>
Value: {producer_value}

### API 2 (Consumer / Request):
Endpoint: {consumer_endpoint}
Location: {loc}
Field: <CANDIDATE>{consumer_field}</CANDIDATE>
Value: {consumer_value}

### Question:
Does API 2 genuinely depend on the value produced by API 1 in this field? Field names may differ lexically but refer to the same logical data item.

Respond ONLY with a JSON object in this exact format, nothing else:
{{"dependency": true or false}}
"""

PROMPT_V2_BOOLEAN = """Determine whether the producer and consumer fields are semantically related based on their names, values, and endpoints.

Producer endpoint: {producer_endpoint}
Producer field: {producer_field}

Consumer endpoint: {consumer_endpoint}
Consumer field: {consumer_field}

Return true if both fields represent the same or closely related concept and could plausibly form a producer-consumer dependency. Consider synonyms, abbreviations, and naming variations.

Return false if they represent different or unrelated concepts, even if their names look similar.
If uncertain, return true.

Output rules:
- Return exactly one lowercase boolean: true or false.
- Do not provide explanations, reasoning, punctuation, markdown, or any other text.
- Your entire response must be either true or false.
"""

LLM_PROMPTS: dict[str, str] = {
    "version-1.0": PROMPT_V1_JSON.strip(),
    "version-2.0": PROMPT_V2_BOOLEAN.strip(),
}


def get_prompt_template(version: str = "version-2.0") -> str:
    """Retrieve prompt template by version key, defaulting to version-2.0."""
    return LLM_PROMPTS.get(version, LLM_PROMPTS["version-2.0"])

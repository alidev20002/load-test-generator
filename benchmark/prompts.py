LLM_PROMPTS = {
    "version-1.0": """You are verifying whether two fields observed in HTTP traffic represent a producer-consumer data dependency (a value produced by one API response is consumed by a later API request).

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
Does API 2 genuinely depend on the value produced by API 1 in this field? Field names may differ lexically but refer to the same concept (e.g. an authentication token embedded with a scheme prefix like 'Bearer '). Coincidentally equal common values (booleans, small numbers) or unrelated entities that merely share a value do NOT constitute a real dependency.

Respond ONLY with a JSON object in this exact format, nothing else:
{{"dependency": true or false}}
""",
    "version-2.0": """Determine whether the producer and consumer fields are semantically related based on their names and API endpoint contexts.
 
Producer endpoint: {producer_endpoint}
Producer field: {producer_field}
 
Consumer endpoint: {consumer_endpoint}
Consumer field: {consumer_field}
 
Return true if both fields represent the same or closely related concept and could plausibly form a producer-consumer dependency. Consider synonyms, abbreviations, and naming variations. Return false if they represent different or unrelated concepts, even if their names look similar.
 
If uncertain, return true.
"""
}
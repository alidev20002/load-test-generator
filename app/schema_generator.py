SUPPORT_TYPE = ("string", "number", "array", "object", "boolean", "integer")


def _is_js_falsy(value):
    if value is None:
        return True
    if value is False:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, str):
        return value == ""
    return False


def _js_typeof(value):
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "object"


def get_type(value):
    if _is_js_falsy(value):
        value = "string"
    if value in SUPPORT_TYPE:
        return value
    return _js_typeof(value)


def is_schema(obj):
    if not isinstance(obj, dict):
        return False
    return obj.get("type") in SUPPORT_TYPE


def handle_schema(json_obj, schema):
    schema.update(json_obj)
    if schema.get("type") == "object":
        schema.pop("properties", None)
        parse(json_obj.get("properties"), schema)
    if schema.get("type") == "array":
        schema.pop("items", None)
        schema["items"] = {}
        parse(json_obj.get("items"), schema["items"])


def handle_array(arr, schema):
    schema["type"] = "array"
    props = schema["items"] = {}
    parse(arr[0] if len(arr) > 0 else None, props)


def handle_object(json_obj, schema):
    if is_schema(json_obj):
        return handle_schema(json_obj, schema)
    schema["type"] = "object"
    schema["required"] = []
    props = schema["properties"] = {}
    for original_key in list(json_obj.keys()):
        item = json_obj[original_key]
        if original_key and original_key[0] == "*":
            key = original_key[1:]
            cur_schema = props[key] = {}
            schema["required"].append(key)
        else:
            cur_schema = props[original_key] = {}
        parse(item, cur_schema)


def parse(data, schema):
    if isinstance(data, list):
        handle_array(data, schema)
    elif isinstance(data, dict):
        handle_object(data, schema)
    else:
        schema["type"] = get_type(data)


def generate_schema(payload):
    schema = {}
    parse(payload, schema)
    return schema

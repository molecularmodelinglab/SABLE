from typing import List, Dict, Any

def clean_schema_for_gemini(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively processes a Pydantic JSON schema to make it Gemini-compatible.
    - Inlines '$ref' definitions.
    - Removes unsupported keys ('title', 'default', '$defs', 'additionalProperties').
    - Simplifies 'anyOf' for optional types (Union[T, None]).
    """
    # Store definitions from '$defs' if they exist to inline them later
    defs = schema.pop('$defs', {})

    def _process_node(node):
        if not isinstance(node, (dict, list)):
            return node

        if isinstance(node, list):
            return [_process_node(item) for item in node]

        # If a reference is found, replace it with its definition
        if '$ref' in node:
            ref_path = node['$ref'].split('/')
            def_name = ref_path[-1]
            if def_name in defs:
                return _process_node(defs[def_name].copy())
            else:
                return node

        
        if 'anyOf' in node:
            if len(node['anyOf']) == 2:
                # Try to find a non-null type
                non_null_type = next((item for item in node['anyOf'] if item.get('type') != 'null'), None)
                if non_null_type:
                    return _process_node(non_null_type)
            # If we can't simplify anyOf, take the first option
            if node['anyOf']:
                return _process_node(node['anyOf'][0])

        # Recursively process other keys in the dictionary
        cleaned_node = {}
        for key, value in node.items():
            # Skip all unsupported keys
            if key in ['title', 'default', 'additionalProperties', 'anyOf']:
                continue
            cleaned_node[key] = _process_node(value)
        
        return cleaned_node

    return _process_node(schema)

def _proto_to_dict(proto_obj):
    """Convert a protobuf message to a dict, handling RepeatedComposite fields."""
    from google.protobuf.json_format import MessageToDict
    try:
        return MessageToDict(proto_obj, preserving_proto_field_name=True)
    except Exception:
        # Fall back to manual conversion if MessageToDict fails
        return _safe_convert(proto_obj)

def _safe_convert(obj):
    """Safely convert any object to JSON-serializable types."""
    from google.protobuf.message import Message
    
    # Base cases: primitive types that are already JSON-serializable
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
        
    # Handle protobuf messages
    if isinstance(obj, Message):
        try:
            from google.protobuf.json_format import MessageToDict
            return MessageToDict(obj, preserving_proto_field_name=True)
        except Exception:
            return str(obj)
    
    # Handle RepeatedComposite and other iterables
    if hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes, bytearray)):
        try:
            return [_safe_convert(item) for item in obj]
        except Exception:
            return str(obj)
            
    # Handle dict-like objects
    if hasattr(obj, 'items'):
        try:
            return {k: _safe_convert(v) for k, v in obj.items()}
        except Exception:
            return str(obj)
            
    # Last resort: stringify the object
    return str(obj)
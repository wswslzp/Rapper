#!/usr/bin/env python3

import re
import json

def test_parse_structured_result(result_text):
    if not result_text:
        return None

    # Look for JSON code blocks
    json_pattern = r'```json\s*(\{[^`]+\})\s*```'
    matches = re.findall(json_pattern, result_text, re.DOTALL | re.IGNORECASE)

    for match in matches:
        try:
            parsed = json.loads(match)
            if isinstance(parsed, dict) and 'structured_result' in parsed:
                return parsed['structured_result']
        except json.JSONDecodeError:
            continue

    return None

# Test case 1: JSON block format
test1 = """Task completed successfully.

```json
{"structured_result": {"status": "completed", "output_path": "src/main.py", "summary": "Added new feature", "errors": []}}
```
"""

result1 = test_parse_structured_result(test1)
print('Test 1 - JSON block:', result1)

# Test case 2: No structured result
test2 = """Just plain text output."""

result2 = test_parse_structured_result(test2)
print('Test 2 - No structured result:', result2)

print('Structured result parsing test completed successfully!')
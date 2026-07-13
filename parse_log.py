import json
with open('/Users/jiang/.gemini/antigravity-cli/brain/291e6862-ce89-4ede-8950-9f2d6ae38f2d/.system_generated/logs/transcript.jsonl', encoding='utf-8', errors='ignore') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try:
            data = json.loads(line)
            if data.get('type') == 'PLANNER_RESPONSE' and not data.get('tool_calls'):
                print("====================")
                print(data.get('content', ''))
        except:
            pass

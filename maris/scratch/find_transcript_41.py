import json

with open(r'C:\Users\Dhruva\.gemini\antigravity-ide\brain\6c29bfb4-5ea9-43a9-b222-6ad544b5a674\.system_generated\logs\transcript.jsonl', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if '41.15' in line:
            obj = json.loads(line)
            idx = line.find('41.15')
            snippet = line[max(0, idx-150):min(len(line), idx+150)]
            print(f'Line {i}: type={obj.get("type")}')
            print('  Snippet:', snippet)

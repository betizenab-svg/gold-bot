import json

with open('pyright_errors.json', 'r', encoding='utf-16') as f:
    data = json.load(f)

from collections import defaultdict

with open('pyright_errors.json', 'r', encoding='utf-16') as f:
    data = json.load(f)

errors = defaultdict(list)
for item in data.get('generalDiagnostics', []):
    file_path = item.get('file', '')
    if '\\src\\' in file_path or '\\config\\' in file_path or '\\test' in file_path or '\\verify' in file_path:
        line = item['range']['start']['line'] + 1
        msg = item.get('message', '').split('\n')[0]
        rel_path = file_path.split('gold_trading_bot\\')[-1]
        errors[f"{rel_path}:{line}"].append(msg)

with open('pyright_parsed_utf8.txt', 'w', encoding='utf-8') as out_f:
    for key, msgs in errors.items():
        unique_msgs = set(msgs)
        for msg in unique_msgs:
            out_f.write(f"{key} - {msg}\n")

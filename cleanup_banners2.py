import os
import re
import glob

def clean_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    new_lines = []
    changed = False
    for line in lines:
        if '----' in line and (line.strip().startswith('#') or line.strip().startswith('<!--') or line.strip().startswith('/*')):
            changed = True
            continue # skip this line
        new_lines.append(line)
        
    if changed:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f"Cleaned {filepath}")

if __name__ == '__main__':
    search_dirs = [
        '**/*.py',
        '**/*.html',
        '**/*.css',
    ]
    for pattern in search_dirs:
        for file in glob.glob(pattern, recursive=True):
            if 'env' in file or 'venv' in file or '.gemini' in file: continue
            clean_file(file)

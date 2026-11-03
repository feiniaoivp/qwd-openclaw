#!/usr/bin/env python3
import os
import shutil
import re

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")

TARGET_FILES = [
    "analysis/close_scan_v2.py",
    "analysis/adaptive_dual.py",
    "analysis/portfolio_sim.py",
    "analysis/service.py",
]

def fix_file(rel_path):
    path = os.path.join(WORKSPACE, rel_path)
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    modified = False
    new_lines = []
    
    # Ensure shutil is imported at the top or near the write
    has_shutil = any("import shutil" in line for line in lines)
    
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match: with open(..., 'w') or open(..., 'w')
        # This is a simple but effective pattern for this specific workspace
        if re.search(r"open\(.*, ['\"]w['\"]\)", line):
            # Look for the variable used as the filename
            # Extract the first argument of open()
            match = re.search(r"open\(([^,)]+)", line)
            if match:
                filename_var = match.group(1).strip()
                # Inject backup logic
                # We assume indentation is handled by copying the current line's lead
                indent = line[:len(line) - len(line.lstrip())]
                backup_logic = [
                    f"{indent}import shutil\n",
                    f"{indent}if os.path.exists({filename_var}):\n",
                    f"{indent}    shutil.copy({filename_var}, {filename_var} + '.bak')\n"
                ]
                new_lines.extend(backup_logic)
                modified = True
        
        new_lines.append(line)
        i += 1

    if modified:
        print(f"Fixing {rel_path}...")
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
    else:
        print(f"No write patterns found in {rel_path}")

if __name__ == "__main__":
    for f in TARGET_FILES:
        fix_file(f)
    print("Fixer completed.")

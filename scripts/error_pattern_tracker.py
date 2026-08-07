#!/usr/bin/env python3
"""
Error Pattern Tracker using Ontology.
Records high-frequency error patterns and injects them on startup.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

WORKSPACE = Path("/Users/duguke/.openclaw/workspace")
ONTOLOGY_FILE = WORKSPACE / ".error_patterns.json"

def load_patterns() -> dict:
    if ONTOLOGY_FILE.exists():
        return json.loads(ONTOLOGY_FILE.read_text())
    return {"patterns": [], "injection_prompt": ""}

def save_patterns(data: dict):
    ONTOLOGY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def add_pattern(pattern: str, fix: str, category: str = "general"):
    """Record a new error pattern or increment count."""
    data = load_patterns()
    for p in data["patterns"]:
        if p["pattern"] == pattern:
            p["count"] += 1
            p["last_seen"] = datetime.now().isoformat()
            p["fix"] = fix  # Update fix if better one found
            save_patterns(data)
            return
    data["patterns"].append({
        "pattern": pattern,
        "fix": fix,
        "category": category,
        "count": 1,
        "first_seen": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat()
    })
    save_patterns(data)

def get_injection_prompt(threshold: int = 2) -> str:
    """Generate system prompt injection for patterns seen >= threshold times."""
    data = load_patterns()
    frequent = [p for p in data["patterns"] if p["count"] >= threshold]
    if not frequent:
        return ""
    
    lines = ["## 🚫 高频错误模式黑名单 (启动自动注入)", ""]
    lines.append("以下错误已发生多次，**严禁再犯**：")
    for p in sorted(frequent, key=lambda x: -x["count"]):
        lines.append(f"- **模式**: {p['pattern']}")
        lines.append(f"  **修正**: {p['fix']}")
        lines.append(f"  **出现次数**: {p['count']} | 类别: {p['category']}")
        lines.append("")
    lines.append("---")
    return "\n".join(lines)

def list_patterns():
    data = load_patterns()
    for p in sorted(data["patterns"], key=lambda x: -x["count"]):
        print(f"[{p['count']}x] {p['pattern']} → {p['fix']} ({p['category']})")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs=3, metavar=("PATTERN", "FIX", "CATEGORY"), help="Add error pattern")
    parser.add_argument("--inject", action="store_true", help="Print injection prompt for startup")
    parser.add_argument("--list", action="store_true", help="List all patterns")
    parser.add_argument("--threshold", type=int, default=2, help="Min count for injection")
    args = parser.parse_args()
    
    if args.add:
        add_pattern(args.add[0], args.add[1], args.add[2])
        print(f"Added: {args.add[0]}")
    elif args.inject:
        print(get_injection_prompt(args.threshold))
    elif args.list:
        list_patterns()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
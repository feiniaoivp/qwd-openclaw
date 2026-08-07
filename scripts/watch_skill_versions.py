#!/usr/bin/env python3
"""
Watch SKILL.md files for version changes.
Run via cron (e.g., every hour) to detect skill version changes and notify.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

WORKSPACE = Path("/Users/duguke/.openclaw/workspace")
SKILLS_DIR = WORKSPACE / "skills"
STATE_FILE = WORKSPACE / ".skill_versions.json"

def extract_version(skill_path: Path) -> str:
    """Extract version from SKILL.md frontmatter or content."""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return ""
    content = skill_md.read_text(encoding="utf-8")
    # Look for version in frontmatter or version field
    for line in content.splitlines()[:20]:
        if line.strip().startswith("version:") or line.strip().startswith("version="):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
        if "version" in line.lower() and ("sha" in line.lower() or "hash" in line.lower()):
            # SHA format: sha256:xxx
            parts = line.split(":")
            if len(parts) >= 2:
                return ":".join(parts[1:]).strip()
    # Fallback: hash the file content
    return hashlib.sha256(content.encode()).hexdigest()[:16]

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

def scan_skills() -> dict:
    """Return {skill_name: version} for all skills."""
    versions = {}
    for skill_dir in SKILLS_DIR.iterdir():
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            versions[skill_dir.name] = extract_version(skill_dir)
    return versions

def notify_changes(added: list, removed: list, changed: list):
    """Send notification via cron wake or write to a notification file."""
    notif_file = WORKSPACE / ".skill_change_notifications.json"
    notifications = []
    if notif_file.exists():
        notifications = json.loads(notif_file.read_text())
    
    for skill in added:
        notifications.append({
            "type": "added",
            "skill": skill,
            "message": f"⚠️ 新技能安装: {skill} — 请阅读其 SKILL.md 了解新接口"
        })
    for skill in removed:
        notifications.append({
            "type": "removed",
            "skill": skill,
            "message": f"⚠️ 技能移除: {skill} — 相关调用将失效"
        })
    for skill, old_v, new_v in changed:
        notifications.append({
            "type": "changed",
            "skill": skill,
            "old_version": old_v,
            "new_version": new_v,
            "message": f"⚠️ 技能版本变更: {skill} ({old_v} → {new_v}) — 必须重读 SKILL.md 后再调用"
        })
    
    notif_file.write_text(json.dumps(notifications, indent=2, ensure_ascii=False))
    
    # Also wake the main session via cron wake (if available)
    # We'll write a marker file that the main session can check on next heartbeat
    marker = WORKSPACE / ".skill_version_changed"
    marker.write_text(json.dumps({
        "added": added,
        "removed": removed,
        "changed": changed,
        "timestamp": __import__("datetime").datetime.now().isoformat()
    }, ensure_ascii=False))

def main():
    old_state = load_state()
    new_state = scan_skills()
    
    added = [k for k in new_state if k not in old_state]
    removed = [k for k in old_state if k not in new_state]
    changed = [(k, old_state[k], new_state[k]) for k in new_state 
               if k in old_state and old_state[k] != new_state[k]]
    
    if added or removed or changed:
        notify_changes(added, removed, changed)
        print(f"Changes detected: +{len(added)} -{len(removed)} ~{len(changed)}")
    else:
        print("No skill version changes")
    
    save_state(new_state)

if __name__ == "__main__":
    main()
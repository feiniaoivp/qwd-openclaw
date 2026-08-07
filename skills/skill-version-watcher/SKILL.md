---
name: "skill-version-watcher"
description: "Watch skill SKILL.md files for version changes, alert on upgrade"
---

# Skill Version Watcher Skill

## Purpose
Monitor installed skills for version changes (SKILL.md `version` field) and notify user when upgrades occur, so they know to re-read the skill before use.

## Mechanism

### 1. Version Tracking (Ontology)

```yaml
entity_types:
  SkillVersion:
    fields:
      skill_name: string (PK)
      version: string (valid semantic version from SKILL.md frontmatter; falls back to a SHA256 content hash when no `version` field is declared)
      last_checked: datetime
      last_notified: datetime (nullable)
      changelog_summary: string (nullable)
    constraints:
      - unique: skill_name
```

### 2. Check Logic (Run via cron daily at 07:00)

```python
def check_skill_versions():
    skills_dir = Path("~/.openclaw/workspace/skills")  # or plugin-skills
    for skill_dir in skills_dir.iterdir():
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        # Parse the semantic version from SKILL.md frontmatter (supports both
        # `version: 1.2.3` YAML and `version = 1.2.3` INI syntax). If the
        # SKILL.md declares no explicit version, falls back to a content hash
        # so content-only changes are still detected.
        version = extract_version_from_skill(skill_dir)  # e.g., "0.4.2" or "6.2.0" or <16-char hash>
        
        # Query ontology
        existing = ontology.get("SkillVersion", skill_name=skill_dir.name)
        if existing and existing.version != version:
            # Version changed!
            notify_user(skill_dir.name, existing.version, version)
            ontology.update("SkillVersion", skill_dir.name, version=version, last_notified=now())
        elif not existing:
            ontology.create("SkillVersion", skill_name=skill_dir.name, version=version, last_checked=now())
```

### 3. Notification Format (via webchat)

```
🔔 Skill Version Changed: akshare-stock
Old: eaf0dfd766c0fef8
New: 4d7b3616d674e7f1
Action: Please re-read skill/akshare-stock/SKILL.md before next use
```

> A skill's version is its declared semantic version (e.g. `1.2.3`) when the
> SKILL.md frontmatter contains a `version:` field; otherwise it is a 16-char
> SHA256 hash of the SKILL.md content. Version changes can therefore be a real
> version bump OR any content edit.

### 4. Cron Job

```json
{
  "name": "skill-version-check",
  "schedule": {"kind": "cron", "expr": "0 7 * * *", "tz": "Asia/Shanghai"},
  "payload": {"kind": "agentTurn", "message": "Check all installed skills for version changes..."},
  "sessionTarget": "isolated"
}
```

### 5. Manual Trigger

Command: `skill_version_check` — runs the check immediately.

## Integration

- Uses `ontology` skill for persistence
- Uses `skill_workshop` to list/inspect proposals (but this tracks LIVE skills)
- Cron job runs in isolated session
- Notification via `sessions_send` to main session or webhook

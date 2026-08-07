#!/bin/bash
# skill-version-watcher: Check all installed skills for version changes
# Stores state in /tmp/skill-versions.json as a simple flat file

STATE_FILE="$HOME/.openclaw/workspace/skills/skill-version-watcher/versions.state"
SKILLS_DIR="$HOME/.openclaw/workspace/skills"
CHANGES_DETECTED=0

if [ ! -d "$SKILLS_DIR" ]; then
    echo "ERROR: Skills directory not found: $SKILLS_DIR"
    exit 1
fi

# Load existing state into a temp file for lookup
[ ! -f "$STATE_FILE" ] && touch "$STATE_FILE"

NEW_STATE=""
UPDATES_NEW=""
UPDATES_CHANGED=""

for skill_dir in "$SKILLS_DIR"/*/; do
    [ ! -d "$skill_dir" ] && continue
    skill_name=$(basename "$skill_dir")
    skill_md="$skill_dir/SKILL.md"
    
    [ ! -f "$skill_md" ] && continue
    
    # SHA256 of the file content, first 16 chars
    version=$(sha256sum "$skill_md" | cut -d' ' -f1 | cut -c1-16)
    
    # Look up old version
    old_ver=$(grep "^${skill_name}=" "$STATE_FILE" | cut -d= -f2)
    
    # Store in new state
    [ -n "$NEW_STATE" ] && NEW_STATE="$NEW_STATE\n"
    NEW_STATE="${NEW_STATE}${skill_name}=${version}"
    
    if [ -z "$old_ver" ]; then
        UPDATES_NEW="$UPDATES_NEW\n  📋 $skill_name ($version)"
    elif [ "$old_ver" != "$version" ]; then
        UPDATES_CHANGED="$UPDATES_CHANGED\n🔔 $skill_name"
        UPDATES_CHANGED="$UPDATES_CHANGED\n   Old: $old_ver"
        UPDATES_CHANGED="$UPDATES_CHANGED\n   New: $version"
        UPDATES_CHANGED="$UPDATES_CHANGED\n   📖 Re-read skills/$skill_name/SKILL.md"
        CHANGES_DETECTED=1
    fi
done

# Save new state
printf "%b\n" "$NEW_STATE" > "$STATE_FILE"

# Output results
echo "=== Skill Version Watcher ==="
echo "Last check: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo ""

if [ $CHANGES_DETECTED -eq 1 ]; then
    echo "🔔 Version Changes Detected:"
    printf "%b\n" "$UPDATES_CHANGED"
else
    echo "✅ All skills up-to-date — no version changes."
fi

if [ -n "$UPDATES_NEW" ]; then
    echo ""
    echo "📋 Newly tracked skills:"
    printf "%b\n" "$UPDATES_NEW"
fi

echo ""
echo "Total skills tracked: $(find "$SKILLS_DIR" -name "SKILL.md" | wc -l | tr -d ' ')"
exit 0

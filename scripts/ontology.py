#!/usr/bin/env python3
"""
Ontology CLI for managing typed knowledge graph.
Supports entity CRUD, relations, validation, and queries.
"""
import json
import sys
import os
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid
import argparse

WORKSPACE = Path("/Users/duguke/.openclaw/workspace")
ONTOLOGY_DIR = WORKSPACE / "memory" / "ontology"
GRAPH_FILE = ONTOLOGY_DIR / "graph.jsonl"
SCHEMA_FILE = ONTOLOGY_DIR / "schema.yaml"

class OntologyStore:
    def __init__(self):
        self.entities: Dict[str, Dict] = {}
        self.relations: List[Dict] = []
        self.schema: Dict = {}
        self._load()
    
    def _load(self):
        if GRAPH_FILE.exists():
            with open(GRAPH_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        op = json.loads(line)
                        if op.get('op') == 'create':
                            entity = op['entity']
                            self.entities[entity['id']] = entity
                        elif op.get('op') == 'relate':
                            self.relations.append({
                                'from': op['from'],
                                'rel': op['rel'],
                                'to': op['to'],
                                'props': op.get('props', {})
                            })
                    except json.JSONDecodeError:
                        pass
        
        if SCHEMA_FILE.exists():
            import yaml
            with open(SCHEMA_FILE, 'r', encoding='utf-8') as f:
                self.schema = yaml.safe_load(f) or {}
    
    def _save_entity(self, entity: Dict):
        """Append entity creation to graph.jsonl"""
        with open(GRAPH_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps({"op": "create", "entity": entity}, ensure_ascii=False) + '\n')
    
    def _save_relation(self, from_id: str, rel: str, to_id: str, props: Dict = None):
        """Append relation to graph.jsonl"""
        with open(GRAPH_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "op": "relate",
                "from": from_id,
                "rel": rel,
                "to": to_id,
                "props": props or {}
            }, ensure_ascii=False) + '\n')
    
    def generate_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:8]}"
    
    def validate_entity(self, entity_type: str, props: Dict) -> List[str]:
        """Validate entity against schema. Returns list of errors."""
        errors = []
        type_schema = self.schema.get('types', {}).get(entity_type, {})
        
        # Required fields
        for req in type_schema.get('required', []):
            if req not in props:
                errors.append(f"Missing required field: {req}")
        
        # Property constraints
        for prop_name, prop_schema in type_schema.get('properties', {}).items():
            if prop_name in props:
                val = props[prop_name]
                if prop_schema.get('type') == 'integer' and not isinstance(val, int):
                    errors.append(f"{prop_name}: expected integer")
                if prop_schema.get('type') == 'string' and not isinstance(val, str):
                    errors.append(f"{prop_name}: expected string")
                if prop_schema.get('type') == 'boolean' and not isinstance(val, bool):
                    errors.append(f"{prop_name}: expected boolean")
                if prop_schema.get('type') == 'array' and not isinstance(val, list):
                    errors.append(f"{prop_name}: expected array")
                if 'enum' in prop_schema and val not in prop_schema['enum']:
                    errors.append(f"{prop_name}: value '{val}' not in enum {prop_schema['enum']}")
                if 'minimum' in prop_schema and isinstance(val, (int, float)) and val < prop_schema['minimum']:
                    errors.append(f"{prop_name}: value {val} below minimum {prop_schema['minimum']}")
                if 'pattern' in prop_schema and isinstance(val, str):
                    import re
                    if not re.match(prop_schema['pattern'], val):
                        errors.append(f"{prop_name}: value '{val}' doesn't match pattern {prop_schema['pattern']}")
        
        # Forbidden properties
        for forbidden in type_schema.get('forbidden_properties', []):
            if forbidden in props:
                errors.append(f"Forbidden property: {forbidden}")
        
        return errors
    
    def validate_relation(self, rel_type: str, from_id: str, to_id: str) -> List[str]:
        """Validate relation against schema."""
        errors = []
        rel_schema = self.schema.get('relations', {}).get(rel_type, {})
        
        from_entity = self.entities.get(from_id)
        to_entity = self.entities.get(to_id)
        
        if not from_entity:
            errors.append(f"From entity {from_id} not found")
        if not to_entity:
            errors.append(f"To entity {to_id} not found")
        
        if from_entity and to_entity:
            from_types = rel_schema.get('from_types', [])
            to_types = rel_schema.get('to_types', [])
            
            if from_types and from_entity['type'] not in from_types:
                errors.append(f"Relation {rel_type}: from type {from_entity['type']} not in allowed {from_types}")
            if to_types and to_entity['type'] not in to_types:
                errors.append(f"Relation {rel_type}: to type {to_entity['type']} not in allowed {to_types}")
        
        # Check acyclic for certain relations
        if rel_schema.get('acyclic', False) and from_id == to_id:
            errors.append("Acyclic relation cannot reference itself")
        
        return errors
    
    def create(self, entity_type: str, props: Dict, entity_id: str = None) -> str:
        """Create new entity."""
        errors = self.validate_entity(entity_type, props)
        if errors:
            raise ValueError(f"Validation failed: {errors}")
        
        if entity_id is None:
            prefix = entity_type.lower()[:3]
            entity_id = self.generate_id(prefix)
        
        if entity_id in self.entities:
            raise ValueError(f"Entity {entity_id} already exists")
        
        now = datetime.now().isoformat()
        entity = {
            "id": entity_id,
            "type": entity_type,
            "properties": props,
            "created": now,
            "updated": now
        }
        
        self.entities[entity_id] = entity
        self._save_entity(entity)
        return entity_id
    
    def get(self, entity_id: str) -> Optional[Dict]:
        return self.entities.get(entity_id)
    
    def update(self, entity_id: str, props: Dict) -> bool:
        entity = self.entities.get(entity_id)
        if not entity:
            raise ValueError(f"Entity {entity_id} not found")
        
        # Merge properties
        new_props = {**entity['properties'], **props}
        errors = self.validate_entity(entity['type'], new_props)
        if errors:
            raise ValueError(f"Validation failed: {errors}")
        
        entity['properties'] = new_props
        entity['updated'] = datetime.now().isoformat()
        self._save_entity(entity)  # Append full entity for history
        return True
    
    def delete(self, entity_id: str) -> bool:
        if entity_id in self.entities:
            del self.entities[entity_id]
            # Note: not removing from graph.jsonl (append-only)
            return True
        return False
    
    def query(self, entity_type: str = None, where: Dict = None, limit: int = 100) -> List[Dict]:
        results = []
        for entity in self.entities.values():
            if entity_type and entity['type'] != entity_type:
                continue
            if where:
                match = True
                for k, v in where.items():
                    if entity['properties'].get(k) != v:
                        match = False
                        break
                if not match:
                    continue
            results.append(entity)
            if len(results) >= limit:
                break
        return results
    
    def relate(self, from_id: str, rel_type: str, to_id: str, props: Dict = None):
        errors = self.validate_relation(rel_type, from_id, to_id)
        if errors:
            raise ValueError(f"Relation validation failed: {errors}")
        
        self.relations.append({
            'from': from_id,
            'rel': rel_type,
            'to': to_id,
            'props': props or {}
        })
        self._save_relation(from_id, rel_type, to_id, props)
    
    def get_related(self, entity_id: str, rel_type: str = None, direction: str = 'both') -> List[Dict]:
        """Get entities related to entity_id."""
        results = []
        for rel in self.relations:
            if rel_type and rel['rel'] != rel_type:
                continue
            if direction in ('both', 'out') and rel['from'] == entity_id:
                to_entity = self.entities.get(rel['to'])
                if to_entity:
                    results.append({'relation': rel, 'entity': to_entity, 'direction': 'out'})
            if direction in ('both', 'in') and rel['to'] == entity_id:
                from_entity = self.entities.get(rel['from'])
                if from_entity:
                    results.append({'relation': rel, 'entity': from_entity, 'direction': 'in'})
        return results
    
    def validate_all(self) -> List[str]:
        errors = []
        for entity in self.entities.values():
            errs = self.validate_entity(entity['type'], entity['properties'])
            if errs:
                errors.append(f"Entity {entity['id']} ({entity['type']}): {errs}")
        
        for rel in self.relations:
            errs = self.validate_relation(rel['rel'], rel['from'], rel['to'])
            if errs:
                errors.append(f"Relation {rel['from']} -{rel['rel']}-> {rel['to']}: {errs}")
        
        return errors


# ErrorPattern specific helpers
class ErrorPatternManager:
    def __init__(self, store: OntologyStore):
        self.store = store
    
    def record_error(self, pattern: str, fix: str, tags: List[str], 
                     severity: str = "medium", pattern_id: str = None) -> str:
        """Record or increment an error pattern."""
        # Check for existing similar pattern
        existing = self.store.query("ErrorPattern", where={"resolved": False})
        for e in existing:
            if e['properties']['pattern'] == pattern:
                # Increment count
                new_count = e['properties']['count'] + 1
                self.store.update(e['id'], {
                    'count': new_count,
                    'last_seen': datetime.now().isoformat(),
                    'fix': fix,  # Update fix if better
                    'severity': severity if severity in ['high', 'critical'] else e['properties']['severity'],
                    'tags': list(set(e['properties']['tags'] + tags))
                })
                return e['id']
        
        # Create new
        if pattern_id is None:
            # Generate next ERR-XXX
            existing_ids = [e['properties'].get('pattern_id', '') for e in existing]
            nums = [int(pid.split('-')[1]) for pid in existing_ids if pid.startswith('ERR-')]
            next_num = max(nums) + 1 if nums else 1
            pattern_id = f"ERR-{next_num:03d}"
        
        entity_id = self.store.create("ErrorPattern", {
            "pattern_id": pattern_id,
            "pattern": pattern,
            "fix": fix,
            "count": 1,
            "last_seen": datetime.now().isoformat(),
            "severity": severity,
            "tags": tags,
            "resolved": False
        })
        return entity_id
    
    def get_startup_injection(self, min_count: int = 2, limit: int = 10) -> str:
        """Generate system prompt injection for high-frequency errors."""
        patterns = self.store.query("ErrorPattern", where={"resolved": False})
        frequent = [p for p in patterns if p['properties']['count'] >= min_count]
        frequent.sort(key=lambda x: (-x['properties']['count'], x['properties']['last_seen']))
        frequent = frequent[:limit]
        
        if not frequent:
            return ""
        
        lines = ["## ⚠️ High-Frequency Error Patterns (Auto-Injected)", ""]
        lines.append("DO NOT REPEAT THESE MISTAKES:")
        lines.append("")
        for i, p in enumerate(frequent, 1):
            props = p['properties']
            lines.append(f"{i}. [{props['pattern_id']}] {props['pattern']}")
            lines.append(f"   → Fix: {props['fix']}")
            lines.append(f"   → Count: {props['count']} | Severity: {props['severity']} | Tags: {props['tags']}")
            lines.append("")
        lines.append("---")
        return "\n".join(lines)
    
    def list_patterns(self, resolved: bool = None) -> List[Dict]:
        where = {}
        if resolved is not None:
            where['resolved'] = resolved
        return self.store.query("ErrorPattern", where=where)
    
    def resolve(self, pattern_id: str) -> bool:
        patterns = self.store.query("ErrorPattern", where={"pattern_id": pattern_id})
        if patterns:
            self.store.update(patterns[0]['id'], {"resolved": True})
            return True
        return False


# SkillVersion specific helpers
class SkillVersionManager:
    def __init__(self, store: OntologyStore):
        self.store = store
    
    def check_and_notify(self, skill_name: str, current_version: str) -> bool:
        """Check if version changed, notify if so. Returns True if changed."""
        existing = self.store.query("SkillVersion", where={"skill_name": skill_name})
        if existing:
            old_version = existing[0]['properties']['version']
            if old_version != current_version:
                # Version changed!
                self.store.update(existing[0]['id'], {
                    "version": current_version,
                    "last_checked": datetime.now().isoformat(),
                    "last_notified": datetime.now().isoformat()
                })
                return True
            else:
                self.store.update(existing[0]['id'], {
                    "last_checked": datetime.now().isoformat()
                })
                return False
        else:
            # New skill
            self.store.create("SkillVersion", {
                "skill_name": skill_name,
                "version": current_version,
                "last_checked": datetime.now().isoformat()
            })
            return True  # Treat as "changed" for notification
    
    def get_all(self) -> List[Dict]:
        return self.store.query("SkillVersion")


_VERSION_RE = re.compile(r'(?<!\w)(\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?)(?!\w)')


def _parse_frontmatter_value(raw: str) -> Optional[str]:
    """Extract the value of a top-level `version:` / `version =` YAML/INI line.

    Only returns a valid version string (e.g. ``1.2.3``, ``6.2.0``,
    ``v1.4.0-rc1`` without the leading ``v``); otherwise returns None so the
    caller can keep looking or fall back to a content hash.
    """
    # Accept both YAML (``version: 1.2.3``) and INI-style
    # (``version = 1.2.3``) syntax via a single normalized regex.
    match = re.search(r'^version\s*[:=]\s*(.+)$', raw)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    # Strip an optional leading 'v' (e.g. "v1.2.3") and any trailing comment.
    value = re.sub(r'\s*(#.*)?$', '', value)
    if value.lower().startswith('v'):
        value = value[1:]
    # Require a leading digit so we reject free-form text / nested dict values
    # (e.g. ``metadata: {"version":"1.0.0",...}`` must not match "version").
    ver = _VERSION_RE.search(value)
    return ver.group(1) if ver else None


def extract_version_from_skill(skill_path: Path) -> str:
    """Extract a valid semantic version from SKILL.md frontmatter.

    Supports both ``version: 1.2.3`` (YAML) and ``version = 1.2.3`` (INI)
    syntax. Returns an empty string when no valid version is declared; callers
    may fall back to a content hash.
    """
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return ""
    content = skill_md.read_text(encoding="utf-8")
    for line in content.splitlines()[:30]:
        ver = _parse_frontmatter_value(line.strip())
        if ver:
            return ver
    # Fallback: hash content so version changes are still detected even when
    # the SKILL.md declares no explicit version field.
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def main():
    parser = argparse.ArgumentParser(description="Ontology CLI")
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Create entity
    create_p = subparsers.add_parser('create', help='Create entity')
    create_p.add_argument('--type', required=True)
    create_p.add_argument('--props', required=True, help='JSON properties')
    create_p.add_argument('--id', help='Custom entity ID')
    
    # Get entity
    get_p = subparsers.add_parser('get', help='Get entity')
    get_p.add_argument('--id', required=True)
    
    # Query entities
    query_p = subparsers.add_parser('query', help='Query entities')
    query_p.add_argument('--type', required=True)
    query_p.add_argument('--where', default='{}', help='JSON filter')
    query_p.add_argument('--limit', type=int, default=100)
    
    # Update entity
    update_p = subparsers.add_parser('update', help='Update entity')
    update_p.add_argument('--id', required=True)
    update_p.add_argument('--props', required=True, help='JSON properties')
    
    # Delete entity
    delete_p = subparsers.add_parser('delete', help='Delete entity')
    delete_p.add_argument('--id', required=True)
    
    # Relate entities
    relate_p = subparsers.add_parser('relate', help='Create relation')
    relate_p.add_argument('--from', required=True, dest='from_id')
    relate_p.add_argument('--rel', required=True)
    relate_p.add_argument('--to', required=True)
    relate_p.add_argument('--props', default='{}')
    
    # Get related
    related_p = subparsers.add_parser('related', help='Get related entities')
    related_p.add_argument('--id', required=True)
    related_p.add_argument('--rel', help='Relation type filter')
    related_p.add_argument('--direction', choices=['in', 'out', 'both'], default='both')
    
    # Validate
    subparsers.add_parser('validate', help='Validate all entities and relations')
    
    # ErrorPattern specific
    error_p = subparsers.add_parser('error-record', help='Record error pattern')
    error_p.add_argument('--pattern', required=True)
    error_p.add_argument('--fix', required=True)
    error_p.add_argument('--tags', required=True, help='Comma-separated tags')
    error_p.add_argument('--severity', choices=['low', 'medium', 'high', 'critical'], default='medium')
    error_p.add_argument('--id', help='Custom pattern ID (ERR-XXX)')
    
    error_list_p = subparsers.add_parser('error-list', help='List error patterns')
    error_list_p.add_argument('--resolved', choices=['true', 'false', 'all'], default='false')
    
    error_inject_p = subparsers.add_parser('error-inject', help='Get startup injection text')
    error_inject_p.add_argument('--min-count', type=int, default=2)
    error_inject_p.add_argument('--limit', type=int, default=10)
    
    error_resolve_p = subparsers.add_parser('error-resolve', help='Mark error pattern resolved')
    error_resolve_p.add_argument('--id', required=True, help='Pattern ID (ERR-XXX)')
    
    # SkillVersion specific
    skill_check_p = subparsers.add_parser('skill-check', help='Check skill version changes')
    skill_check_p.add_argument('--skills-dir', default='~/.openclaw/workspace/skills')
    
    args = parser.parse_args()
    
    store = OntologyStore()
    
    if args.command == 'create':
        entity_id = store.create(args.type, json.loads(args.props), args.id)
        print(f"Created: {entity_id}")
    
    elif args.command == 'get':
        entity = store.get(args.id)
        if entity:
            print(json.dumps(entity, indent=2, ensure_ascii=False))
        else:
            print(f"Entity {args.id} not found")
            sys.exit(1)
    
    elif args.command == 'query':
        results = store.query(args.type, json.loads(args.where), args.limit)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    elif args.command == 'update':
        store.update(args.id, json.loads(args.props))
        print(f"Updated: {args.id}")
    
    elif args.command == 'delete':
        if store.delete(args.id):
            print(f"Deleted: {args.id}")
        else:
            print(f"Entity {args.id} not found")
            sys.exit(1)
    
    elif args.command == 'relate':
        store.relate(args.from_id, args.rel, args.to, json.loads(args.props))
        print(f"Related: {args.from_id} -{args.rel}-> {args.to}")
    
    elif args.command == 'related':
        results = store.get_related(args.id, args.rel, args.direction)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    elif args.command == 'validate':
        errors = store.validate_all()
        if errors:
            print("Validation errors:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print("All valid")
    
    elif args.command == 'error-record':
        mgr = ErrorPatternManager(store)
        eid = mgr.record_error(
            args.pattern, args.fix, args.tags.split(','),
            args.severity, args.id
        )
        print(f"Recorded: {eid}")
    
    elif args.command == 'error-list':
        mgr = ErrorPatternManager(store)
        resolved = None if args.resolved == 'all' else (args.resolved == 'true')
        patterns = mgr.list_patterns(resolved)
        for p in patterns:
            props = p['properties']
            status = "✓" if props['resolved'] else "✗"
            print(f"[{status}] {props['pattern_id']} ({props['count']}x) {props['pattern'][:60]}...")
    
    elif args.command == 'error-inject':
        mgr = ErrorPatternManager(store)
        text = mgr.get_startup_injection(args.min_count, args.limit)
        print(text)
    
    elif args.command == 'error-resolve':
        mgr = ErrorPatternManager(store)
        if mgr.resolve(args.id):
            print(f"Resolved: {args.id}")
        else:
            print(f"Pattern {args.id} not found")
            sys.exit(1)
    
    elif args.command == 'skill-check':
        mgr = SkillVersionManager(store)
        skills_dir = Path(args.skills_dir).expanduser()
        if not skills_dir.exists():
            print(f"Skills dir not found: {skills_dir}")
            sys.exit(1)
        
        changed = []
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                version = extract_version_from_skill(skill_dir)
                if version:
                    if mgr.check_and_notify(skill_dir.name, version):
                        changed.append((skill_dir.name, version))
        
        if changed:
            print("Version changes detected:")
            for name, ver in changed:
                print(f"  {name}: {ver}")
                # Could add webchat notification here
        else:
            print("No version changes")


if __name__ == "__main__":
    main()
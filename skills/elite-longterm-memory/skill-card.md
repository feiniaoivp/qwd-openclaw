## Description:

Elite Longterm Memory helps AI coding agents keep durable context using a write-ahead log, vector search, git-notes storage, file archives, and optional cloud memory services.

This skill is ready for commercial/non-commercial use.

## Publisher:

[nextfrontierbuilds](https://clawhub.ai/user/nextfrontierbuilds)

### License/Terms of Use:

MIT

## Use Case:

Developers and engineers use this skill to add persistent memory practices to AI coding agents, including active session state, curated memory files, semantic recall, git-notes decisions, and optional external memory integrations.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: Durable memory can retain conversation details, project decisions, secrets, or personal data longer than the user intended.

Mitigation: Review memory files and vector stores regularly, avoid storing secrets or personal data, and clear or redact entries before syncing or sharing a workspace.

Risk: Optional cloud and Mem0 integrations can upload memory content to third-party services.

Mitigation: Keep those integrations disabled until upload scope, consent, and redaction controls have been reviewed.

Risk: Unpinned or floating package versions can change behavior after installation.

Mitigation: Prefer pinned package versions and review release changes before deploying the skill in a managed environment.

## Reference(s):

- [ClawHub skill page](https://clawhub.ai/nextfrontierbuilds/skills/elite-longterm-memory)
- [npm package](https://www.npmjs.com/package/elite-longterm-memory)
- [bulletproof-memory](https://clawdhub.com/skills/bulletproof-memory)
- [lancedb-memory](https://clawdhub.com/skills/lancedb-memory)
- [git-notes-memory](https://clawdhub.com/skills/git-notes-memory)
- [memory-hygiene](https://clawdhub.com/skills/memory-hygiene)
- [supermemory](https://clawdhub.com/skills/supermemory)

## Skill Output:

**Output Type(s):** [Text, Markdown, Code, Shell commands, Configuration, Guidance]

**Output Format:** [Markdown guidance with shell commands, JSON configuration snippets, JavaScript examples, and generated memory files]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [The bundled CLI can create SESSION-STATE.md, MEMORY.md, and dated Markdown logs in the current workspace.]

## Skill Version(s):

1.2.3 (source: SKILL.md frontmatter, package.json, server release metadata)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

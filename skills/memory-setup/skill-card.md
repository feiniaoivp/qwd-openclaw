## Description:

Enable and configure Moltbot/Clawdbot memory search for persistent context. Use when setting up memory, fixing "goldfish brain," or helping users configure memorySearch in their config. Covers MEMORY.md, daily logs, and vector search setup.

This skill is ready for commercial/non-commercial use.

## Publisher:

[jrbobbyhansen-pixel](https://clawhub.ai/user/jrbobbyhansen-pixel)

### License/Terms of Use:


## Use Case:

Developers and agent users use this skill to configure Moltbot/Clawdbot memory search, create persistent memory files, and troubleshoot recall across sessions.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: The skill recommends indexing memory files and past session transcripts, including with external embedding providers, which can expose sensitive or regulated data if configured broadly.

Mitigation: Review memory sources before indexing, prefer local or memory-only indexing in sensitive environments, avoid storing secrets or regulated data, and define a deletion or rebuild process for transcripts and embeddings.

## Reference(s):

- [ClawHub skill page](https://clawhub.ai/jrbobbyhansen-pixel/skills/memory-setup)

## Skill Output:

**Output Type(s):** [text, markdown, configuration, shell commands, guidance]

**Output Format:** [Markdown guidance with JSON configuration snippets and shell commands]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Docs-only setup guidance; no runtime code is executed by the skill itself.]

## Skill Version(s):

1.0.0 (source: server release metadata)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

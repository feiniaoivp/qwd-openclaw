## Description:

Helps agents reflect on their work, log corrections, and maintain tiered local memory for reusable preferences and lessons.

This skill is ready for commercial/non-commercial use.

## Publisher:

[ivangdavila](https://clawhub.ai/user/ivangdavila)

### License/Terms of Use:

MIT-0

## Use Case:

Agent users and developers use this skill to give an assistant durable local memory for corrections, preferences, and reusable workflow lessons. It is intended for assistants that should learn from explicit feedback while keeping the memory store organized across sessions.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: Durable local memory can influence future agent behavior across sessions.

Mitigation: Require explicit opt-in before enabling persistence and review proposed memory entries before they are written.

Risk: Memory entries may capture sensitive or confidential context.

Mitigation: Avoid storing credentials, financial data, health data, third-party information, location patterns, access patterns, or other sensitive material.

Risk: Setup may alter workspace steering files that affect agent behavior.

Mitigation: Inspect proposed changes to AGENTS.md, SOUL.md, and HEARTBEAT.md before accepting them.

Risk: Exports created during deletion are additional copies of retained memory.

Mitigation: Treat exported archives as sensitive data and manage or delete them after review.

## Reference(s):

- [ClawHub skill page](https://clawhub.ai/ivangdavila/skills/self-improving)
- [Publisher profile](https://clawhub.ai/user/ivangdavila)
- [Skill homepage](https://clawic.com/skills/self-improving)
- [Security boundaries](artifact/boundaries.md)
- [Setup guide](artifact/setup.md)
- [Memory operations](artifact/operations.md)

## Skill Output:

**Output Type(s):** [text, markdown, shell commands, configuration, guidance]

**Output Format:** [Markdown guidance with configuration snippets and shell command examples]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Produces local memory setup and maintenance guidance for the agent; no credentials or extra binaries are required.]

## Skill Version(s):

1.2.16 (source: frontmatter and server release evidence)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

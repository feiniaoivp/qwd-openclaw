## Description:

Captures errors, corrections, and learnings in persistent notes and helps agents review and maintain guidance before reuse.

This skill is ready for commercial/non-commercial use.

## Publisher:

[pskoett](https://clawhub.ai/user/pskoett)

### License/Terms of Use:

MIT-0

## Use Case:

Developers using OpenClaw capture failures, corrections, and feature requests, then review and revalidate saved lessons before reusing them or promoting them to workspace guidance.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: Persistent learning notes may expose sensitive details if shared.

Mitigation: Keep .learnings out of version control unless deliberately sharing it, and omit secrets or use short redacted excerpts.

Risk: Promoted lessons may introduce incorrect or outdated workspace guidance.

Mitigation: Check the supporting evidence and review changes to SOUL.md, TOOLS.md, and AGENTS.md before adopting them.

Risk: The optional hook scans session-end transcripts and persists error excerpts.

Mitigation: Enable the hook only if this scanning is acceptable, and review the saved redacted excerpts.

## Reference(s):

- [Self-Improving Agent on ClawHub](https://clawhub.ai/pskoett/skills/self-improving-agent)

## Skill Output:

**Output Type(s):** [Markdown, Guidance, Shell commands, Configuration instructions]

**Output Format:** [Markdown notes and guidance with optional shell commands]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Maintains local learning logs; reviewed lessons may be promoted to workspace guidance files.]

## Skill Version(s):

4.0.3 (source: ClawHub release)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

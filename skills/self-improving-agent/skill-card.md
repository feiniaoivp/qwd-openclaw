## Description:

Captures learnings, errors, and corrections to enable continuous improvement.

This skill is ready for commercial/non-commercial use.

## Publisher:

[pskoett](https://clawhub.ai/user/pskoett)

### License/Terms of Use:

MIT-0

## Use Case:

Developers and agent users use this skill to capture corrections, command failures, feature requests, and recurring workflow lessons as workspace-local Markdown records. OpenClaw users can optionally enable a hook that reminds agents to review learnings and performs session-end error sweeps.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: The skill can store potentially sensitive work history in .learnings/ files.

Mitigation: Treat .learnings/ as sensitive workspace data, keep it out of version control unless intentionally shared, and periodically review or delete stale entries.

Risk: The optional OpenClaw hook can sweep ended session transcripts and append redacted error excerpts, but redaction is best effort.

Mitigation: Enable the hook only in trusted workspaces, avoid logging secrets or raw transcripts, and review pending auto-detected entries before promoting them.

Risk: Captured corrections or lessons can be incorrect or too context-specific if promoted without review.

Mitigation: Review entries before promoting them into SOUL.md, TOOLS.md, AGENTS.md, or reusable skills.

## Reference(s):

- [ClawHub Skill Page](https://clawhub.ai/pskoett/skills/self-improving-agent)
- [OpenClaw Integration](references/openclaw-integration.md)
- [Entry Examples](references/examples.md)
- [Uninstall Guide](references/uninstall.md)

## Skill Output:

**Output Type(s):** [markdown, shell commands, configuration, guidance]

**Output Format:** [Markdown guidance with inline shell commands and workspace file templates]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Writes or appends local .learnings Markdown files when used by an agent; optional OpenClaw hook can add redacted session-end error entries.]

## Skill Version(s):

4.0.2 (source: frontmatter, changelog, server release evidence)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

## Description:

Automatically update Clawdbot and all installed skills once daily. Runs via cron, checks for updates, applies them, and messages the user with a summary of what changed.

This skill is ready for commercial/non-commercial use.

## Publisher:

[maximeprades](https://clawhub.ai/user/maximeprades)

### License/Terms of Use:


## Use Case:

Developers and Clawdbot users use this skill to configure daily update checks for Clawdbot and installed skills, apply available updates, and receive a concise update summary.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: Daily auto-updates can replace Clawdbot and every installed skill without fresh approval.

Mitigation: Prefer dry-run notifications or explicit approval before installation, pin or verify versions, keep backups, and define rollback and disable steps before enabling in sensitive environments.

Risk: Automatic update runs can encounter partial failures, permission issues, network problems, or package conflicts.

Mitigation: Review the delivered update summary, keep logs for failed runs, run diagnostic commands after updates, and require manual intervention when errors are reported.

## Reference(s):

- [Agent Implementation Guide](references/agent-guide.md)
- [Update Summary Examples](references/summary-examples.md)
- [Clawdbot Updating Guide](https://docs.clawd.bot/install/updating)
- [ClawdHub CLI](https://docs.clawd.bot/tools/clawdhub)
- [Cron Jobs](https://docs.clawd.bot/cron)

## Skill Output:

**Output Type(s):** [text, markdown, shell commands, configuration, guidance]

**Output Format:** [Markdown guidance with shell command blocks and cron configuration.]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Can propose a daily cron schedule, optional local update script, verification commands, and update-summary messaging.]

## Skill Version(s):

1.0.0 (source: evidence release version and SKILL.md metadata)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

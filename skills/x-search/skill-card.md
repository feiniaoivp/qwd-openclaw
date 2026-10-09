## Description:

Search X (Twitter) posts using the xAI API.

This skill is ready for commercial/non-commercial use.

## Publisher:

[jaaneek](https://clawhub.ai/user/jaaneek)

### License/Terms of Use:

MIT-0

## Use Case:

Developers and agents use this skill to search X/Twitter for posts, current discussion, account-filtered results, date-bounded results, and cited summaries through xAI.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: X/Twitter search queries and API key-authenticated requests are sent to xAI.

Mitigation: Use the skill only when that data sharing is acceptable, and avoid including secrets, private project details, or sensitive personal data in search queries.

Risk: Broad trigger wording could cause agents to invoke the skill for queries that should stay local or private.

Mitigation: Tighten trigger wording when publishing or operating the skill for others so it is used only for intended X/Twitter searches.

## Reference(s):

- [xAI X Search Documentation](https://docs.x.ai/developers/tools/x-search)
- [xAI Console](https://console.x.ai)
- [ClawHub Skill Page](https://clawhub.ai/jaaneek/skills/x-search)
- [Publisher Profile](https://clawhub.ai/user/jaaneek)

## Skill Output:

**Output Type(s):** [text, markdown, code, shell commands, configuration, guidance]

**Output Format:** [JSON search response with text, citations, search count, and token usage; the skill guidance asks agents to present results readably with post content, authors, and citations.]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Requires python3 and XAI_API_KEY; supports handle filters, exclusion filters, date ranges, image understanding, and video understanding.]

## Skill Version(s):

1.0.0 (source: server-resolved release metadata)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

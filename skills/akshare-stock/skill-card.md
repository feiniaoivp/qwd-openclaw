## Description:

A股量化数据分析工具，基于AkShare库获取A股行情、财务数据、板块信息等。用于回答关于A股股票查询、行情数据、财务分析、选股等问题。

This skill is for research and development only.

## Publisher:

[mbpz](https://clawhub.ai/user/mbpz)

### License/Terms of Use:


## Use Case:

Developers and external users use this skill to query and analyze China A-share quotes, historical K-line data, financial indicators, sector data, fund flows, IPO data, and margin trading data with AkShare-oriented examples and a CLI helper.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: Dependency and data-source reliability caveats can cause installation or runtime failures.

Mitigation: Install in an isolated Python environment with reviewed pinned dependency versions, then test target actions locally before relying on outputs.

Risk: The skill makes network requests to retrieve A-share market data, and upstream websites may change or be unavailable.

Mitigation: Expect network access, add retry and error handling around calls, and verify important outputs against authoritative market data sources.

Risk: Some advertised features appear incomplete or broken.

Mitigation: Validate each action before enabling it in a workflow and document any unavailable commands for users.

Risk: Market data analysis could be mistaken for investment advice.

Mitigation: Treat outputs as informational research support only and apply human review before financial decisions.

## Reference(s):

- [ClawHub skill page](https://clawhub.ai/mbpz/skills/akshare-stock)

## Skill Output:

**Output Type(s):** [Text, Markdown, Code, Shell commands, Configuration, JSON]

**Output Format:** [Markdown guidance with Python and shell code blocks; the CLI helper emits JSON records.]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [May require network access to market data endpoints and installed Python dependencies.]

## Skill Version(s):

1.0.1 (source: server release metadata)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

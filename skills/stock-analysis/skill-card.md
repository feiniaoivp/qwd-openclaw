## Description:

Analyze stocks and cryptocurrencies using Yahoo Finance data with portfolio management, watchlist alerts, dividend analysis, 8-dimension stock scoring, viral trend detection, and rumor or early-signal detection.

This skill is ready for commercial/non-commercial use.

## Publisher:

[udiedrichsen](https://clawhub.ai/user/udiedrichsen)

### License/Terms of Use:


## Use Case:

External users and developers use this skill to analyze US stocks and supported cryptocurrencies, track portfolios and watchlists, review dividend metrics, and scan for market trends or rumors. Outputs are informational and should not be treated as financial advice.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: Optional X/Twitter scanning can require live session credentials through a third-party CLI.

Mitigation: Keep social scanning disabled unless needed; use a dedicated session or account, avoid unrelated secrets in the skill environment, and review the third-party CLI before enabling it.

Risk: Portfolio and watchlist storage can contain private financial information.

Mitigation: Store state files in a controlled local directory, restrict file permissions, and avoid sharing generated portfolio or watchlist files.

Risk: Market, news, short-interest, and filing data can be delayed, cached, or rate-limited.

Mitigation: Treat outputs as informational, verify important figures with primary sources, and consult a licensed financial advisor before making investment decisions.

## Reference(s):

- [ClawHub skill page](https://clawhub.ai/udiedrichsen/skills/stock-analysis)
- [Publisher profile](https://clawhub.ai/user/udiedrichsen)
- [Yahoo Finance](https://finance.yahoo.com)
- [Usage guide](docs/USAGE.md)
- [Hot Scanner documentation](docs/HOT_SCANNER.md)
- [Architecture documentation](docs/ARCHITECTURE.md)

## Skill Output:

**Output Type(s):** [text, markdown, JSON, shell commands, guidance]

**Output Format:** [Terminal text, Markdown guidance, and optional JSON output]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [May read live market and news data, and may maintain local portfolio and watchlist JSON files.]

## Skill Version(s):

6.2.0 (source: frontmatter and server release evidence)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

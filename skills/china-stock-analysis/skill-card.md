## Description:

Analyze Chinese stock prices (A-shares, HK stocks) and provide investment recommendations.

This skill is ready for commercial/non-commercial use.

## Publisher:

[paulshe](https://clawhub.ai/user/paulshe)

### License/Terms of Use:

MIT-0

## Use Case:

External users can use this skill to ask an agent for structured Chinese stock analysis, including current price context, market signals, and buy, hold, or sell recommendations with a risk disclaimer.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: Stock prices, market news, and investment recommendations can be stale or incorrect.

Mitigation: Verify current market data and any buy, hold, or sell recommendation against trusted financial sources before acting.

Risk: The skill's default financial risk notice is in Chinese.

Mitigation: Request an English or bilingual disclaimer when users are not comfortable reading Chinese financial risk notices.

## Reference(s):

- [Popular Chinese Stocks Reference](references/china-stocks.md)
- [Eastmoney](https://www.eastmoney.com)
- [Xueqiu](https://xueqiu.com)
- [Yahoo Finance](https://finance.yahoo.com)
- [Google Finance](https://www.google.com/finance)

## Skill Output:

**Output Type(s):** [analysis, markdown, guidance]

**Output Format:** [Markdown with tables and bullet lists]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Includes a Chinese financial risk disclaimer; no files or executable code are produced.]

## Skill Version(s):

1.0.0 (source: release metadata)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

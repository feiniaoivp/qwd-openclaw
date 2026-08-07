## Description: <br>
Use Wiz for current market prices, macro mood, Market Track, movers, global TradingWizard bots, ranked opportunities, Bot Fund performance, and optional private account tools through OAuth. <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[hugors00](https://clawhub.ai/user/hugors00) <br>

### License/Terms of Use: <br>
MIT-0 <br>


## Use Case: <br>
External users and developers use Wiz to add TradingWizard market intelligence, bot context, Bot Fund performance, and optional private workspace tools to OpenClaw market research and paper-trading workflows. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Private account and paper-trading controls require OAuth with read and paper-trading write scopes. <br>
Mitigation: Enable OAuth only when private TradingWizard features are needed, confirm user intent before paper controls, and avoid exposing tokens or OAuth codes in chat. <br>
Risk: Market data, bot signals, and performance information can be mistaken for personalized financial advice or guaranteed returns. <br>
Mitigation: Present market information as educational, separate observed data from interpretation, include relevant timeframes and freshness, and never promise future returns or real-money execution. <br>


## Reference(s): <br>
- [TradingWizard OpenClaw Connection](https://www.tradingwizard.ai/mcp?client=openclaw#connect) <br>
- [TradingWizard MCP Documentation](https://www.tradingwizard.ai/docs/mcp) <br>
- [ClawHub Skill Page](https://clawhub.ai/hugors00/skills/wiz-by-tradingwizard) <br>


## Skill Output: <br>
**Output Type(s):** [Guidance, Markdown, Shell commands, Configuration] <br>
**Output Format:** [Markdown with inline shell commands and concise market-research guidance] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [May call TradingWizard MCP tools for live market data; private account and paper-trading tools require OAuth and user confirmation.] <br>

## Skill Version(s): <br>
1.0.0 (source: frontmatter and release evidence) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>

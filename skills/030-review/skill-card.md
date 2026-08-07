## Description: <br>
Provides a seven-stage A-share short-term trading review framework for reviewing prior trades, scanning pre-market conditions, analyzing auction and opening signals, monitoring intraday market behavior, summarizing closing data, and planning next-day scenarios. <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[userb000](https://clawhub.ai/user/userb000) <br>

### License/Terms of Use: <br>
MIT-0 <br>


## Use Case: <br>
External users and trading researchers use this skill to structure daily A-share short-term market reviews, evaluate market health and sector rotation, and draft next-day trading plans for human review. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: The framework can influence real financial decisions and may be mistaken for personalized investment advice. <br>
Mitigation: Treat outputs as educational review guidance, validate market signals independently, and require explicit human review before acting. <br>
Risk: Position sizing or risk signals may not account for slippage, losses, or the user's financial circumstances. <br>
Mitigation: Account for transaction costs, liquidity, slippage, and loss tolerance before using any suggested trading plan. <br>
Risk: An agent could apply the framework mechanically to place trades or size positions without user approval. <br>
Mitigation: Do not allow autonomous trade execution or position sizing from this skill; keep all trading actions under explicit user control. <br>


## Reference(s): <br>
- [每日短线复盘详细检查清单](references/daily-checklist.md) <br>


## Skill Output: <br>
**Output Type(s):** [Guidance, Markdown, Text] <br>
**Output Format:** [Markdown checklists and structured review templates] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Designed for human-reviewed trading analysis; does not execute trades or fetch market data.] <br>

## Skill Version(s): <br>
1.0.0 (source: server release metadata) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>

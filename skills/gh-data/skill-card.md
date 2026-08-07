## Description: <br>
A股炒股选股量化工具 - 16维数据深度分析，自学习预判引擎，趋势预测，自动报告。直接HTTP采集新浪/腾讯/东方财富数据+WebAPI量化分析。支持自动生成DOCX分析报告。 <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[sunbinpy](https://clawhub.ai/user/sunbinpy) <br>

### License/Terms of Use: <br>
MIT-0 <br>


## Use Case: <br>
External ClawHub users and developers use this skill to analyze A-share stocks, screen candidates, review quantitative signals and predictions, and generate local DOCX and chart reports from public market data and a configured WebAPI. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Stock codes, analysis requests, and API keys may be sent to a configured WebAPI that defaults to plain HTTP. <br>
Mitigation: Review or replace the WebAPI URL before use and install only when this network exposure is acceptable. <br>
Risk: API keys can be persisted in plaintext under ~/.ghdata/ghdataapikey, including keys supplied through GHDATA_API_KEY. <br>
Mitigation: Use a key appropriate for this service, avoid highly sensitive shared credentials, and rotate or remove the local key file when needed. <br>
Risk: Report and chart generation can create local DOCX and PNG files. <br>
Mitigation: Confirm the configured report directory before running the skill and review generated files before sharing them. <br>
Risk: Quantitative stock analysis and predictions can be incomplete, stale, or misleading for investment decisions. <br>
Mitigation: Treat generated analysis as informational and verify conclusions against current market data and qualified financial judgment. <br>


## Reference(s): <br>
- [ClawHub skill page](https://clawhub.ai/sunbinpy/skills/gh-data) <br>
- [股海罗盘 service page](https://www.oraskl.com/ghdata-admin) <br>


## Skill Output: <br>
**Output Type(s):** [Text, Markdown, Code, Shell commands, Configuration, Files, Guidance] <br>
**Output Format:** [Markdown guidance with Python code snippets plus generated DOCX and PNG report files] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [May call public market-data endpoints and a configured WebAPI; report generation writes local DOCX and chart files.] <br>

## Skill Version(s): <br>
2.0.20 (source: server release evidence) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>

## Description: <br>
Local speech-to-text with the Whisper CLI (no API key). <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[steipete](https://clawhub.ai/user/steipete) <br>

### License/Terms of Use: <br>


## Use Case: <br>
Developers and audio workflows use this skill to prepare local Whisper CLI commands for transcribing or translating audio files without an API key. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Installation depends on the user's Homebrew setup and the openai-whisper package. <br>
Mitigation: Install only from trusted Homebrew sources and review the package before use. <br>
Risk: Transcripts can contain sensitive content from the audio files being processed. <br>
Mitigation: Handle generated transcripts as sensitive files and store or share them according to the data's confidentiality requirements. <br>
Risk: Whisper models are downloaded and cached locally on first use. <br>
Mitigation: Confirm the local cache location and available storage before running larger models. <br>


## Reference(s): <br>
- [OpenAI Whisper](https://openai.com/research/whisper) <br>
- [ClawHub skill page](https://clawhub.ai/steipete/skills/openai-whisper) <br>


## Skill Output: <br>
**Output Type(s):** [text, markdown, shell commands, guidance] <br>
**Output Format:** [Markdown with inline shell commands] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [The skill may direct local Whisper model downloads and local transcript file output through CLI options.] <br>

## Skill Version(s): <br>
1.0.0 (source: server release metadata) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>

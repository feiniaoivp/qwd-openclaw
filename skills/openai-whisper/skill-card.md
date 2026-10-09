## Description:

Local speech-to-text with the Whisper CLI (no API key).

This skill is ready for commercial/non-commercial use.

## Publisher:

[steipete](https://clawhub.ai/user/steipete)

### License/Terms of Use:


## Use Case:

Developers and agents use this skill to generate local Whisper CLI commands for transcribing or translating audio files without an API key.

### Deployment Geography for Use:

Global

## Known Risks and Mitigations:

Risk: The Whisper CLI and model files are installed and cached locally through Homebrew and the CLI runtime.

Mitigation: Install only when local Homebrew installation and local model caching are acceptable for the target environment.

Risk: Transcription output is written to the path selected in the generated command.

Mitigation: Review audio input paths and output directories before running generated Whisper commands.

## Reference(s):

- [OpenAI Whisper](https://openai.com/research/whisper)
- [ClawHub Openai Whisper Skill](https://clawhub.ai/steipete/skills/openai-whisper)

## Skill Output:

**Output Type(s):** [text, markdown, shell commands, guidance]

**Output Format:** [Markdown with inline shell commands]

**Output Parameters:** [1D]

**Other Properties Related to Output:** [Produces local Whisper CLI usage guidance; transcription files are written by the CLI to the requested output directory.]

## Skill Version(s):

1.0.0 (source: server release metadata)

## Ethical Considerations:

Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment.

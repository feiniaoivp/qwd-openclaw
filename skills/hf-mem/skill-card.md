## Description: <br>
Hugging Face CLI to estimate the required memory to load Safetensors or GGUF model weights for inference from the Hugging Face Hub. <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[huggingface](https://clawhub.ai/user/huggingface) <br>

### License/Terms of Use: <br>
MIT-0 <br>


## Use Case: <br>
Developers and engineers use this skill to estimate whether Hugging Face Hub models fit within available VRAM or instance memory before inference. It is useful when a user provides a Hugging Face model ID or URL and asks about Safetensors or GGUF memory requirements. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: The skill runs an external CLI through uvx and contacts Hugging Face when estimating model memory. <br>
Mitigation: Review the generated command before execution and use it only for model repositories you intend to query. <br>
Risk: Accessing gated or private models may require HF_TOKEN or an explicit Hugging Face token. <br>
Mitigation: Provide tokens only through the environment or approved secret handling, and avoid pasting long-lived credentials into prompts or logs. <br>


## Reference(s): <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, Guidance, Analysis] <br>
**Output Format:** [Markdown with inline bash commands and JSON-producing CLI examples] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [CLI examples use --json-output and may require HF_TOKEN for gated or private Hugging Face models.] <br>

## Skill Version(s): <br>
1.0.10 (source: server release evidence) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>

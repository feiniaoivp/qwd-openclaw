## Description: <br>
Amap Traffic helps agents query AMap real-time traffic conditions and plan fastest driving routes with route time, distance, estimated cost, and congestion details. <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[robin797860](https://clawhub.ai/user/robin797860) <br>

### License/Terms of Use: <br>


## Use Case: <br>
Drivers, operations teams, and travel-planning agents use this skill to turn addresses into AMap driving routes informed by real-time traffic. It is useful when an agent needs to compare route duration, distance, estimated toll cost, and congestion details before recommending a driving option. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: The skill sends queried addresses, route endpoints, and derived coordinates to AMap. <br>
Mitigation: Use it only when sharing those locations with AMap is acceptable, and avoid submitting highly sensitive locations. <br>
Risk: The skill requires an AMap API key read from OpenClaw configuration or the AMAP_KEY environment variable. <br>
Mitigation: Use a dedicated or restricted AMap key where possible and keep OpenClaw configuration files private. <br>


## Reference(s): <br>
- [ClawHub skill page](https://clawhub.ai/robin797860/skills/amap-traffic) <br>
- [Publisher profile](https://clawhub.ai/user/robin797860) <br>
- [AMap Open Platform](https://console.amap.com/) <br>
- [AMap geocoding API endpoint](https://restapi.amap.com/v3/geocode/geo) <br>
- [AMap driving route API endpoint](https://restapi.amap.com/v3/direction/driving) <br>


## Skill Output: <br>
**Output Type(s):** [text, shell commands, configuration, guidance] <br>
**Output Format:** [Plain text route summaries and Markdown guidance with command examples] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Requires an AMap API key and address or coordinate inputs for route planning.] <br>

## Skill Version(s): <br>
1.0.0 (source: server release metadata) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>

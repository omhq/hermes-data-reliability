# Tool boundary

Controls the agent's access to tools within approved resources, time windows,
and usage limits. Loads configured tool packages and checks each request
before allowing it to run.

Validates and bounds returned evidence, records tool activity, and issues
receipts tied to the investigation session. Partial results remain explicit.
Credentials, original evidence, and authorization state stay on the host.

Consumers and investigation tools use the same access and evidence checks.
The agent receives approved results through the boundary.

# Tool boundary

Controls the agent's access to AWS tools. Checks each request against its
approved resources, time window, and usage limits, and rejects calls outside
that scope.

Validates returned data and records tool activity and evidence sources.
Currently supports read-only access to S3, CloudWatch Logs, and CloudTrail.

# Pipeline health

Checks whether an expected object or event is present for a declared delivery
after its deadline. Schedules,
delivery identities, evidence sources, and expectations are configured separately.
It reads configured events from shared collection and checks configured S3
objects. It keeps observations and later availability, and queues unmet expectations for
Hermes to investigate.

The same checks are available in chat. Reports distinguish observed absence
from unavailable or incomplete evidence. Object presence does not establish
valid contents or a complete load. Missing events remain unknown when
collection has gaps or has not allowed enough time for delayed logs.

You configure schedules, source mappings, expectations, and investigation
objectives and instructions in `/srv/hermes-lab/pipeline-health.yaml`.
See the [configuration guide](../../../docs/OBSERVATION_CONFIG.md#pipeline-delivery-expectations)
for daily and lab examples, and [operations](../../../docs/OPERATIONS.md#configure-delivery-expectations)
for installation commands.

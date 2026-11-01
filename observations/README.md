# Observations

Stores collected events with their source, event identity, event time, and
collection time. Repeated reads keep one copy of each event.

Tracks which time ranges were collected and where each consumer has reached.
Consumers can read the same evidence at different speeds. Retention gaps,
partial collection, and failed reads remain visible when checking whether an
expected event was observed.

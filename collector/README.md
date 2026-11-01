# Collector

Collects events from approved sources into a shared database. It combines
enabled consumers' subscriptions so they share source reads, avoids storing
duplicate events, and resumes unfinished collection after a restart.

Collection keeps successful and informational events as well as failures. It
records completed ranges and collection errors so consumers can distinguish
missing evidence from an observed absence. It runs independently of consumers
and the investigation agent.

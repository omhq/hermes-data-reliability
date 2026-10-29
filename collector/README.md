# Collector

Reads configured CloudWatch logs and checks them against your YAML match rules.
Saves matching events as incidents, along with collection progress, so it can
resume after a restart.

Collection runs independently of the investigator and continues while the agent
is busy or unavailable.

# Log rules

Reads saved CloudWatch events and applies the rules and investigation context
you define in YAML. Matching events become incidents in the shared queue for
Hermes to investigate.

Keeps its own progress. Rule changes can replay retained evidence, while
collection continues independently. Failed rule evaluation leaves its unread
work in place and reports an error.

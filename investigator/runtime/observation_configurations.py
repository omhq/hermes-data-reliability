from observations.configurations.registry import (
    ConfigurationRegistry,
    ConfigurationSpec,
)

CONFIGURATIONS = ConfigurationRegistry(
    ConfigurationSpec(
        identifier="observations",
        yaml_filename="observations.yaml",
        compiled_filename="collection.json",
        provider="collector.configuration:SourceCatalogConfiguration",
    ),
    ConfigurationSpec(
        identifier="log_rules",
        yaml_filename="log-rules.yaml",
        compiled_filename="log-rules.json",
        provider=(
            "investigator.consumers.log_rules.configuration:LogRulesConfiguration"
        ),
    ),
    ConfigurationSpec(
        identifier="pipeline_health",
        yaml_filename="pipeline-health.yaml",
        compiled_filename="pipeline-health.json",
        provider=(
            "investigator.capabilities.pipeline_health.configuration:"
            "PipelineHealthConfiguration"
        ),
    ),
)

from .connectors.cloudtrail import CloudTrailConnector
from .connectors.cloudwatch import CloudWatchConnector
from .registry import ConnectorRegistry

CONNECTORS = ConnectorRegistry(CloudWatchConnector, CloudTrailConnector)

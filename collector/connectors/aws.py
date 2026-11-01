import re
from abc import abstractmethod
from typing import Any

from collector.execution import CollectionError
from collector.providers.aws.client import AwsClient
from collector.providers.aws.identity import AwsIdentity
from observations.configuration import (
    InvalidConfiguration,
    fields,
    fingerprint,
)
from observations.models import Source

from .base import Connector


class AwsConnector(Connector):
    def __init__(
        self, configuration: dict[str, Any], client: AwsClient | None = None
    ) -> None:
        self.identity = AwsIdentity.load(configuration)
        self.client = client or AwsClient(self.identity)

    @classmethod
    def validate_definition(cls, value: object) -> dict[str, Any]:
        definition = fields(value, {"connector", "region", "location"})

        if definition["connector"] != cls.name:
            raise InvalidConfiguration("Source connector does not match")
        if type(definition["region"]) is not str or not re.fullmatch(
            r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", definition["region"]
        ):
            raise InvalidConfiguration("Invalid source region")
        if not cls.valid_location(definition["location"]):
            raise InvalidConfiguration("Invalid source location")

        return definition.copy()

    @staticmethod
    @abstractmethod
    def valid_location(value: object) -> bool: ...

    @classmethod
    def prepare_template(
        cls, value: dict[str, Any], configuration: dict[str, Any]
    ) -> dict[str, Any]:
        return {**value, "region": configuration["region"]}

    def resolve(self, definition: dict[str, Any]) -> Source:
        definition = self.validate_definition(definition)
        descriptor = {"account": self.identity.account_id, **definition}
        source = Source(fingerprint(descriptor), **descriptor)
        self.authorize(source)
        return source

    def authorize(self, source: Source) -> None:
        try:
            definition = self.validate_definition(
                {
                    "connector": source.connector,
                    "region": source.region,
                    "location": source.location,
                }
            )
        except InvalidConfiguration:
            raise CollectionError("resource_not_allowed") from None

        if (
            source.options
            or source.account != self.identity.account_id
            or source.region != self.identity.region
            or source.key
            != fingerprint({"account": source.account, **definition})
        ):
            raise CollectionError("resource_not_allowed")

        self.authorize_location(source.location)

    @abstractmethod
    def authorize_location(self, location: str) -> None: ...

from tool_boundary.providers.aws_identity import AwsIdentity

from .base import Scope, ToolProvider


class AwsToolProvider(ToolProvider):
    def validate_scope(self, scope: Scope) -> None:
        AwsIdentity.validate_scope(scope.context, self.configuration)

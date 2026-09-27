from dataclasses import dataclass

from aws_cdk import Acknowledgment, Validations
from constructs import IConstruct


@dataclass(frozen=True)
class Suppression:
    path: str
    finding_id: str
    reason: str


SUPPRESSIONS: list[Suppression] = []


def apply_suppressions(root: IConstruct, suppressions: list[Suppression] = SUPPRESSIONS) -> None:
    constructs = {construct.node.path: construct for construct in root.node.find_all()}
    for suppression in suppressions:
        if suppression.path not in constructs:
            raise LookupError(f"nag suppression target not found: {suppression.path}")
        Validations.of(constructs[suppression.path]).acknowledge(
            Acknowledgment(id=suppression.finding_id, reason=suppression.reason)
        )

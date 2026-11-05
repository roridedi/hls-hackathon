from um_agentic.contracts import Actor, ActorKind, Role
from um_agentic.errors import Forbidden

READ_ROLES = frozenset(Role)
CLINICAL_ROLES = frozenset({Role.NURSE, Role.PHYSICIAN, Role.PHARMACIST})
INTAKE_ROLES = CLINICAL_ROLES | {Role.INTAKE}


def require_roles(actor: Actor, allowed: frozenset[Role]) -> None:
    if actor.kind != ActorKind.HUMAN or not actor.roles & allowed:
        raise Forbidden("The authenticated account does not have permission for this action.")

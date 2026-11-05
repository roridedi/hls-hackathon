from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from um_agentic.contracts import Actor, ActorKind

bearer = HTTPBearer(auto_error=False)


def current_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "A bearer credential is required.", headers={"WWW-Authenticate": "Bearer"})
    settings = request.app.state.settings
    if settings.auth_mode != "development":
        return request.app.state.entra.authenticate(credentials.credentials)
    account = request.app.state.config.dev_accounts.get(credentials.credentials)
    if account is None:
        raise HTTPException(401, "Invalid development credential.")
    return Actor(actor_id=account.actor_id, kind=ActorKind.HUMAN, roles=account.roles)


Authenticated = Annotated[Actor, Depends(current_actor)]

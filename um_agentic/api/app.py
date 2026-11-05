from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from um_agentic.adapters.blob import LocalBlobStore
from um_agentic.adapters.documents import LocalDocumentExtractor
from um_agentic.adapters.intake import IntakeEnvelope
from um_agentic.api.auth import Authenticated
from um_agentic.api.workbench import router as workbench_router
from um_agentic.contracts import Actor, CaseAggregate, Source, SourceType
from um_agentic.errors import AgentFailure, Conflict, Forbidden, InvalidRequest, NotFound, UMError
from um_agentic.persistence import Store
from um_agentic.review import ReviewService
from um_agentic.security.roles import INTAKE_ROLES, READ_ROLES, require_roles
from um_agentic.service import UMService
from um_agentic.settings import Settings
from um_agentic.workflow import Workflow


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    selected = settings or Settings()
    config = selected.configuration()
    repository = store or Store(selected.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repository.initialize()
        yield
        repository.engine.dispose()

    app = FastAPI(title="Human supervised utilization management", version="0.1.0", lifespan=lifespan)
    app.state.settings = selected
    app.state.config = config
    app.state.store = repository
    app.state.workflow = Workflow(repository, config)
    app.state.service = UMService(repository, selected)
    app.state.review = ReviewService(app.state.service)
    app.state.blobs = LocalBlobStore(selected.storage_path)
    app.state.extractor = LocalDocumentExtractor()
    app.include_router(workbench_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=selected.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(UMError)
    async def domain_error(request: Request, exc: UMError) -> JSONResponse:
        status = 400
        if isinstance(exc, NotFound):
            status = 404
        elif isinstance(exc, Forbidden):
            status = 403
        elif isinstance(exc, Conflict):
            status = 409
        elif isinstance(exc, InvalidRequest):
            status = 422
        elif isinstance(exc, AgentFailure):
            status = 503
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "detail": "The request does not match the required contract.",
            "fields": [".".join(str(part) for part in error["loc"]) for error in exc.errors()],
        })

    @app.get("/health")
    def health() -> dict[str, str]:
        with repository.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "environment": selected.environment}

    @app.get("/me", response_model=Actor)
    def me(actor: Authenticated) -> Actor:
        return actor

    @app.post("/cases/intake", response_model=CaseAggregate, status_code=201)
    def intake(
        body: IntakeEnvelope, actor: Authenticated,
        idempotency_key: str | None = Header(default=None, max_length=200),
    ) -> CaseAggregate:
        return app.state.service.intake(body, actor, idempotency_key)

    @app.get("/cases/{case_id}", response_model=CaseAggregate)
    def case(case_id: UUID, actor: Authenticated) -> CaseAggregate:
        require_roles(actor, READ_ROLES)
        return repository.get(case_id)

    @app.post("/documents/extract", response_model=Source)
    async def extract_document(file: UploadFile, actor: Authenticated) -> Source:
        from uuid import uuid4

        require_roles(actor, INTAKE_ROLES)
        content = await file.read(LocalDocumentExtractor.max_bytes + 1)
        media_type = file.content_type or ""
        pages = app.state.extractor.extract(content, media_type)
        document_id = f"document_{uuid4().hex}"
        app.state.blobs.put(f"documents/{document_id}", content, media_type)
        return Source(source_id=document_id, source_type=SourceType.DOCUMENT, pages=pages)

    @app.post("/demo/{scenario}", response_model=CaseAggregate, status_code=201)
    def demo(scenario: str, actor: Authenticated) -> CaseAggregate:
        from um_agentic.synthetic import golden_fixture

        if selected.environment == "production":
            raise NotFound("Demo routes are disabled.")
        try:
            fixture = golden_fixture(scenario.upper())
        except ValueError as exc:
            raise InvalidRequest("Use a golden scenario from G1 through G10.") from exc
        return app.state.service.intake(IntakeEnvelope.model_validate(fixture["request"]), actor)

    return app


app = create_app()

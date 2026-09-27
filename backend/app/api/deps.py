"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.services.container import AppContainer
from app.services.paper import PaperService


def get_container(request: Request) -> AppContainer:
    container: AppContainer = request.app.state.container
    return container


ContainerDep = Annotated[AppContainer, Depends(get_container)]


def get_paper_service(container: ContainerDep) -> PaperService:
    return PaperService(container)


PaperServiceDep = Annotated[PaperService, Depends(get_paper_service)]

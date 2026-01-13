import json
import httpx
from pathlib import Path
from typing import Generic, Optional, TypeVar, List, Any
from uuid import uuid4
import asyncio

from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .logging_config import logger
from .config import JOBS_DIR, FISCALIZACION_API_URL
from .jobs import create_job, load_job

T = TypeVar("T")

class EntityResult(BaseModel, Generic[T]):
    success: bool
    message: str
    model: Optional[T] = None

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    stage: str
    message: str | None = None
    original_filename: str

class VerificationInstruction(BaseModel):
    """Schema for Rule Instructions (used mainly by frontend)."""

    id: str | None = None
    instruction: str | None = None
    description: str | None = None
    source_document: str | None = None
    tags: list[str] | None = None
    isAdded: bool | None = None
app = FastAPI(
    title="PROSEI Backend Gateway",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/jobs", response_model=EntityResult[JobStatusResponse])
async def create_job_endpoint(file: UploadFile = File(...)):
    logger.info(f"Received file upload: {file.filename}")
    job_id = str(uuid4())
    job = create_job(job_id, original_filename=file.filename)

    raw_dir = JOBS_DIR / job_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / "input.pdf"

    content = await file.read()
    with dest.open("wb") as f:
        f.write(content)

    async with httpx.AsyncClient() as client:
        try:
            logger.info(f"Delegating job {job_id} to Fiscalizacion API at {FISCALIZACION_API_URL}")
            response = await client.post(
                f"{FISCALIZACION_API_URL}/pipeline/run",
                json={"job_id": job_id},
                timeout=10.0
            )
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to trigger Fiscalizacion API: {e}")
            return EntityResult(
                success=False,
                message=f"File uploaded but failed to start processing: {str(e)}",
                model=JobStatusResponse(
                    job_id=job.job_id,
                    status="error",
                    stage="init",
                    message=str(e),
                    original_filename=job.original_filename
                )
            )

    return EntityResult[JobStatusResponse](
        success=True,
        message="Job created and processing started",
        model=JobStatusResponse(
            job_id=job.job_id,
            status=job.status,
            stage=job.stage,
            message=job.message,
            original_filename=job.original_filename,
        ),
    )

@app.get("/jobs/{job_id}", response_model=EntityResult[JobStatusResponse])
def get_job_status(job_id: str):
    job = load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return EntityResult[JobStatusResponse](
        success=True,
        message="Job status retrieved successfully",
        model=JobStatusResponse(
            job_id=job.job_id,
            status=job.status,
            stage=job.stage,
            message=job.message,
            original_filename=job.original_filename,
        ),
    )

@app.get("/jobs/{job_id}/report")
def download_report(job_id: str):
    job = load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Report not ready yet")

    report_path = job.report_file
    if report_path is None or not report_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    suffix = report_path.suffix.lower()
    media_type = "application/octet-stream"
    if suffix == ".pdf":
        media_type = "application/pdf"
    elif suffix == ".txt":
        media_type = "text/plain"
    elif suffix in {".md", ".markdown"}:
        media_type = "text/markdown"

    headers = {
        "Content-Disposition": f"attachment; filename={report_path.name}"
    }

    return FileResponse(
        path=report_path,
        filename=report_path.name,
        media_type=media_type,
        headers=headers
    )


# Endpoints verificações
@app.get("/rules/lista_cargas/instructions")
async def get_rules_proxy():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{FISCALIZACION_API_URL}/rules/lista_cargas/instructions")
            return resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error connecting to Fiscalizacion API: {e}")

@app.get("/verifications/original/{rule_id}", response_model=EntityResult[VerificationInstruction])
async def get_rules_proxy(rule_id: str):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{FISCALIZACION_API_URL}/verifications/original/{rule_id}")
            return resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error connecting to Fiscalizacion API: {e}")            

@app.post("/verifications")
async def add_verification_proxy(rule: VerificationInstruction):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{FISCALIZACION_API_URL}/verifications", json=rule.model_dump())
            return resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error connecting to Fiscalizacion API: {e}")

@app.put("/verifications/{rule_id}")
async def update_verification_proxy(rule_id: str, rule: VerificationInstruction):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.put(f"{FISCALIZACION_API_URL}/rules/lista_cargas/instructions/{rule_id}", json=rule.model_dump())
            return resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error connecting to Fiscalizacion API: {e}")            

@app.delete("/verifications/{rule_id}")
async def delete_verification_proxy(rule_id: str):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.delete(f"{FISCALIZACION_API_URL}/verifications/{rule_id}")
            return resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error connecting to Fiscalizacion API: {e}")

FISCALIZACION_WS_URL = f"{FISCALIZACION_API_URL}/ws/chat"
@app.websocket("/ws/chat")
async def websocket_proxy(websocket: WebSocket):
    await websocket.accept()
    fiscal_ws = None

    try:
        while True:
            # Aguarda mensagem do frontend
            data = await websocket.receive_text()

            # Conecta no fiscal apenas uma vez
            if fiscal_ws is None:
                fiscal_ws = await websockets.connect(FISCALIZACION_WS_URL)

                # Escuta respostas do fiscal em background
                asyncio.create_task(
                    fiscal_to_client(fiscal_ws, websocket)
                )

            # Repassa mensagem
            await fiscal_ws.send(data)

    except WebSocketDisconnect:
        print("Cliente desconectou do backend")

    except Exception as e:
        print(f"Erro no proxy websocket: {e}")

    finally:
        if fiscal_ws:
            await fiscal_ws.close()
        await websocket.close()


async def fiscal_to_client(fiscal_ws, client_ws):
    try:
        async for message in fiscal_ws:
            await client_ws.send_text(message)
    except Exception as e:
        print(f"Erro fiscal → client: {e}")
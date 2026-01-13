import json
import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Generic, Optional, TypeVar

from .pipeline import run_full_pipeline
from .chat_service import generate_answer_stream, stream_generator
from .config import RULES_FILE, CUSTOM_RULES_FILE, CONFIGS_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_fisc_internal")

app = FastAPI(title="PROSEI Fiscalizac+tion Internal API")

T = TypeVar("T")

class EntityResult(BaseModel, Generic[T]):
    success: bool
    message: str
    model: Optional[T] = None

class PipelineRequest(BaseModel):
    job_id: str

class ChatRequest(BaseModel):
    query: str
    context: str | None = None

class VerificationInstruction(BaseModel):
    """Schema for Rule Instructions (used mainly by frontend)."""

    id: str | None = None
    instruction: str | None = None
    description: str | None = None
    source_document: str | None = None
    tags: list[str] | None = None
    isAdded: bool | None = None

@app.post("/pipeline/run")
async def trigger_pipeline(request: PipelineRequest, background_tasks: BackgroundTasks):
    logger.info(f"Received pipeline trigger for job: {request.job_id}")
    background_tasks.add_task(run_full_pipeline, request.job_id)
    return {"status": "started", "job_id": request.job_id}

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            data_text = await websocket.receive_text()
            data = json.loads(data_text)

            query = data.get("query")
            context = data.get("context", "")

            if not query:
                await websocket.send_text("Erro: A pergunta é obrigatória.")
                await websocket.send_text("[DONE]")
                continue

            generator = generate_answer_stream(query, context)

            async for token in stream_generator(generator):
                await websocket.send_text(token)

            await websocket.send_text("[DONE]")

    except WebSocketDisconnect:
        print("Cliente desconectado.")

    except Exception as e:
        print(f"Erro no websocket: {e}")
        await websocket.close()


 # Endpoints verificações
@app.get(
    "/rules/lista_cargas/instructions",
    response_model=EntityResult[list[VerificationInstruction]],
)
def get_lista_cargas_instructions():
    """
    Returns only the id and instruction of each verification.
    """
    # Preferir arquivo custom se existir; caso contrário, usar o original
    if CUSTOM_RULES_FILE.exists():
        try:
            data = json.loads(CUSTOM_RULES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
    else:
        data = None

    if data is None:
        if not RULES_FILE.exists():
            raise HTTPException(status_code=500, detail="rules_lista_cargas.json not found")
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="rules_lista_cargas.json is invalid")

    verifications = data.get("verifications", [])
    result = [
        VerificationInstruction(
            id=v.get("id"),
            instruction=v.get("instruction"),
            description=v.get("description"),
            tags=v.get("tags"),
            source_document= v.get("source_document"),
            isAdded=v.get("isAdded"),
        )
        for v in verifications
    ]

    return EntityResult[list[VerificationInstruction]](
        success=True,
        message="Verification instructions retrieved successfully",
        model=result,
    )

@app.get("/verifications/original/{rule_id}", response_model=EntityResult[VerificationInstruction])
def get_rule_original_by_id(rule_id: str):
    """Get a verification rule by id from the original rules JSON only."""

    if not RULES_FILE.exists():
        raise HTTPException(status_code=500, detail="rules_lista_cargas.json not found")

    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="rules_lista_cargas.json is invalid")

    for rule_data in data.get("verifications", []):
        if rule_data.get("id") == rule_id:
            rule = VerificationInstruction(**rule_data)
            return EntityResult[VerificationInstruction](
                success=True,
                message="Original verification rule found",
                model=rule,
            )

    return EntityResult[VerificationInstruction](
        success=False,
        message="Original verification rule was not found",
        model=None,
    )

@app.post("/verifications", response_model=EntityResult[VerificationInstruction])
def add_verification(rule: VerificationInstruction):
    """Add a new verification rule to the custom JSON file.

    The original `rules_lista_cargas.json` file is never modified.
    """

    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

    if CUSTOM_RULES_FILE.exists():
        try:
            data = json.loads(CUSTOM_RULES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"document_name": "Lista de Cargas (custom)", "verifications": []}
    else:
        if RULES_FILE.exists():
            try:
                original_data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                original_data = {"document_name": "Lista de Cargas (original)", "verifications": []}
        else:
            original_data = {"document_name": "Lista de Cargas (original)", "verifications": []}

        data = {
            "document_name": "Lista de Cargas (custom)",
            "verifications": original_data.get("verifications", []),
        }

    verifications = data.setdefault("verifications", [])

    # Gerar ID se não vier do frontend
    if not rule.id:
        existing_ids = [v.get("id") for v in verifications if v.get("id")]
        base_prefix = "RMV-CUSTOM-"
        next_num = 1
        if existing_ids:
            nums: list[int] = []
            for rid in existing_ids:
                if rid.startswith(base_prefix):
                    try:
                        nums.append(int(rid.replace(base_prefix, "")))
                    except ValueError:
                        continue
            if nums:
                next_num = max(nums) + 1
        generated_id = f"{base_prefix}{next_num:03d}"
        rule.id = generated_id

    rule.isAdded = True
    rule.source_document = "Lista de Verificação";

    verifications.append(rule.model_dump())

    CUSTOM_RULES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return EntityResult[VerificationInstruction](
        success=True,
        message="Verification rule added successfully",
        model=rule,
    )

@app.put(
    "/rules/lista_cargas/instructions/{rule_id}",
    response_model=EntityResult[VerificationInstruction],
)
def update_lista_cargas_instruction(rule_id: str, updated_rule: VerificationInstruction):
    """
    Updates a single verification rule in the *custom* rules file.
    """
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure path id and body id are consistent (if body has id)
    if updated_rule.id is not None and updated_rule.id != rule_id:
        raise HTTPException(status_code=400, detail="Body id does not match path rule_id")
    # Load existing custom rules or start a new structure
    if CUSTOM_RULES_FILE.exists():
        try:
            data = json.loads(CUSTOM_RULES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"document_name": "Lista de Cargas (custom)", "verifications": []}
    else:
        # Primera vez: copiar estrutura completa desde o JSON original
        if RULES_FILE.exists():
            try:
                original_data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                original_data = {"document_name": "Lista de Cargas (original)", "verifications": []}
        else:
            original_data = {"document_name": "Lista de Cargas (original)", "verifications": []}

        data = {
            "document_name": "Lista de Cargas (custom)",
            "verifications": original_data.get("verifications", []),
        }

    verifications = data.get("verifications", [])

    index_to_update = None
    for idx, rule in enumerate(verifications):
        if rule.get("id") == rule_id:
            index_to_update = idx
            break

    new_rule_dict = {
        "id": rule_id,
        "description": updated_rule.description,
        "instruction": updated_rule.instruction,
        "tags": updated_rule.tags or [],
        "source_document": updated_rule.source_document,
        "isAdded": updated_rule.isAdded,
    }

    if index_to_update is None:
        verifications.append(new_rule_dict)
    else:
        verifications[index_to_update] = new_rule_dict

    data["verifications"] = verifications

    CUSTOM_RULES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    updated_model = VerificationInstruction(**new_rule_dict)

    return EntityResult[VerificationInstruction](
        success=True,
        message="Verification rule updated successfully",
        model=updated_model,
    )    

@app.delete("/verifications/{rule_id}", response_model=EntityResult[bool])
def delete_verification(rule_id: str):
    """Delete a custom verification rule only if it was added (isAdded == True)."""

    if not CUSTOM_RULES_FILE.exists():
        raise HTTPException(status_code=404, detail="Custom rules file not found")

    try:
        data = json.loads(CUSTOM_RULES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Custom rules file is invalid")

    verifications = data.get("verifications", [])
    new_verifications: list[dict] = []
    deleted = False

    for rule in verifications:
        if rule.get("id") == rule_id:
            if rule.get("isAdded") is True:
                deleted = True
                continue
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Only rules with isAdded == True can be deleted",
                )
        new_verifications.append(rule)

    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found or not deletable")

    data["verifications"] = new_verifications
    CUSTOM_RULES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return EntityResult[bool](
        success=True,
        message="Verification rule deleted successfully",
        model=True,
    )
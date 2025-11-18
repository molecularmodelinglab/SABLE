"""Conversation API endpoints for interactive optimization setup."""

from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from server.database import get_db
from server.models.user import User
from server.models.conversation import Conversation as ConversationModel
from server.models.session import Session as SessionModel
from server.auth.dependencies import get_current_active_user
from server.services.conversation_service import ConversationService
from utils.llm_factory import get_llm_client
from server.services.run_service import run_service
from server.services.run_scheduler import run_scheduler
from server.services.cache_service import cache_service
from server.schemas.conversation import (
    ConversationStartRequest,
    ConversationMessageRequest,
    ConversationResponse,
    ConversationConfirmRequest,
    ConversationCreateRunResponse,
    ConversationListResponse,
    ConversationContext,
    ConversationState,
)
from server.audit import audit_logger, AuditEventType
from server.experiment_logger import experiment_logger
from server.storage import ensure_run_dirs

router = APIRouter(prefix="/conversations", tags=["conversations"])

# Initialize conversation service with shared LLM client (if available)
_conversation_llm_client = get_llm_client()
conversation_service = ConversationService(llm_client=_conversation_llm_client)


@router.post("", response_model=ConversationResponse)
async def start_conversation(
    request: ConversationStartRequest,
    current_user: User = Depends(get_current_active_user),
    db: DBSession = Depends(get_db)
):
    """Start a new conversation for setting up an optimization run.

    This endpoint creates a new interactive conversation that guides the user
    through collecting all necessary information for starting an optimization.
    """
    try:
        # Start conversation
        conversation, message, suggestions = conversation_service.start_conversation(
            db=db,
            user_id=str(current_user.id),
            initial_message=request.initial_message
        )

        # Log conversation start
        audit_logger.log(
            event_type=AuditEventType.API_REQUEST,
            message=f"Started conversation {conversation.id}",
            user_id=str(current_user.id),
            username=current_user.username,
            details={"conversation_id": str(conversation.id)}
        )

        return ConversationResponse(
            conversation_id=str(conversation.id),
            state=ConversationState(conversation.state),
            message=message,
            context=ConversationContext(**conversation.context),
            suggestions=suggestions,
            can_proceed=conversation_service.can_create_run(conversation.context)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start conversation: {str(e)}")


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    current_user: User = Depends(get_current_active_user),
    db: DBSession = Depends(get_db),
    limit: int = 10,
    offset: int = 0
):
    """List user's conversations."""
    try:
        # Query conversations
        conversations = db.query(ConversationModel).filter(
            ConversationModel.user_id == current_user.id
        ).order_by(
            ConversationModel.created_at.desc()
        ).limit(limit).offset(offset).all()

        total = db.query(ConversationModel).filter(
            ConversationModel.user_id == current_user.id
        ).count()

        return ConversationListResponse(
            conversations=[
                {
                    "id": str(c.id),
                    "state": c.state,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                    "run_id": getattr(c, "run_id", None)
                }
                for c in conversations
            ],
            total=total
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list conversations: {str(e)}")


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_active_user),
    db: DBSession = Depends(get_db)
):
    """Get conversation details."""
    # Get conversation
    conversation = db.query(ConversationModel).filter(
        ConversationModel.id == conversation_id,
        ConversationModel.user_id == current_user.id
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Generate response for current state
    message, suggestions = conversation_service._generate_response(
        ConversationState(conversation.state),
        conversation.context
    )

    return ConversationResponse(
        conversation_id=str(conversation.id),
        state=ConversationState(conversation.state),
        message=message,
        context=ConversationContext(**conversation.context),
        suggestions=suggestions,
        can_proceed=conversation_service.can_create_run(conversation.context)
    )


@router.post("/{conversation_id}/message", response_model=ConversationResponse)
async def send_message(
    conversation_id: str,
    request: ConversationMessageRequest,
    current_user: User = Depends(get_current_active_user),
    db: DBSession = Depends(get_db)
):
    """Send a message in an ongoing conversation.

    This endpoint processes the user's message, updates the conversation context,
    and returns the next question or confirmation.
    """
    # Get conversation
    conversation = db.query(ConversationModel).filter(
        ConversationModel.id == conversation_id,
        ConversationModel.user_id == current_user.id
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if conversation is completed
    if conversation.state in [ConversationState.COMPLETED, ConversationState.ABANDONED]:
        raise HTTPException(
            status_code=400,
            detail=f"Conversation is already {conversation.state}"
        )

    try:
        # Process message
        message, new_state, suggestions = conversation_service.send_message(
            db=db,
            conversation=conversation,
            message=request.message
        )

        return ConversationResponse(
            conversation_id=str(conversation.id),
            state=new_state,
            message=message,
            context=ConversationContext(**conversation.context),
            suggestions=suggestions,
            can_proceed=conversation_service.can_create_run(conversation.context)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process message: {str(e)}")


@router.post("/{conversation_id}/confirm", response_model=ConversationCreateRunResponse)
async def confirm_and_create_run(
    conversation_id: str,
    payload: ConversationConfirmRequest,
    http_request: Request,
    current_user: User = Depends(get_current_active_user),
    db: DBSession = Depends(get_db)
):
    """Confirm configuration and create optimization run.

    This endpoint creates the actual optimization run from the collected
    conversation context.
    """
    # Get conversation
    conversation = db.query(ConversationModel).filter(
        ConversationModel.id == conversation_id,
        ConversationModel.user_id == current_user.id
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation_state = ConversationState(conversation.state)

    # Allow confirmations when conversation is still on confirmation step or already marked completed without a run
    if conversation_state not in {ConversationState.CONFIRMATION, ConversationState.COMPLETED}:
        raise HTTPException(
            status_code=400,
            detail="Conversation must be in confirmation state"
        )

    # If a run has already been created, return existing metadata instead of duplicating work
    if conversation.run_id:
        return ConversationCreateRunResponse(
            run_id=conversation.run_id,
            message=f"Optimization already started for run {conversation.run_id}."
        )

    # Handle non-confirmation
    if not payload.confirmed:
        # TODO: Parse requested changes and update context
        raise HTTPException(
            status_code=400,
            detail="Please send another message with the changes you'd like to make"
        )

    # Check if we can create run
    if not conversation_service.can_create_run(conversation.context):
        raise HTTPException(
            status_code=400,
            detail="Insufficient information to create run"
        )

    try:
        # Build prompt from context
        prompt = conversation_service.build_run_prompt(conversation.context)
        context = ConversationContext(**conversation.context)

        note = context.notes.strip() if context.notes else None

        # Generate run identifier and persist prompt assets
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        paths = ensure_run_dirs(run_id)
        (Path(paths["inputs"]) / "prompt.txt").write_text(prompt)
        if note:
            (Path(paths["inputs"]) / "note.txt").write_text(note)

        # Ensure an active session exists for the user
        session = db.query(SessionModel).filter(
            SessionModel.user_id == current_user.id,
            SessionModel.is_active == True
        ).order_by(SessionModel.created_at.desc()).first()

        now_utc = datetime.now(timezone.utc)
        if not session:
            session = SessionModel(
                user_id=current_user.id,
                token=f"conversation-run-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                created_at=now_utc,
                last_activity=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                is_active=True,
                extra_metadata={"source": "conversation"},
            )
            db.add(session)
            db.flush()
        else:
            session.last_activity = now_utc
            db.flush()

        # Capture starting molecule if provided
        starting_molecules = []
        if context.starting_molecule:
            starting_molecules = [context.starting_molecule]

        extra_metadata = {
            "paths": paths,
            "max_iterations": context.max_iterations,
            "batch_size": context.batch_size,
            "conversation_id": conversation_id,
        }

        run_model = run_service.create_run(
            db=db,
            run_id=run_id,
            user_id=current_user.id,
            session_id=session.id,
            prompt=prompt,
            starting_molecules=starting_molecules,
            note=note,
            extra_metadata=extra_metadata,
        )

        experiment = experiment_logger.create_experiment(
            run_id=run_id,
            session_id=str(session.id),
            user_id=str(current_user.id),
            username=current_user.username,
            prompt=prompt,
            parameters={
                "max_iterations": context.max_iterations,
                "batch_size": context.batch_size,
            },
            notes=note,
        )

        updated_metadata = dict(run_model.extra_metadata or {})
        updated_metadata["experiment_id"] = experiment.id
        run_model.extra_metadata = updated_metadata
        db.commit()
        db.refresh(run_model)

        run_scheduler.submit_run(run_id)

        db.refresh(run_model)

        metadata = run_model.extra_metadata or {}
        paths = metadata.get("paths", {}) if isinstance(metadata, dict) else {}
        info = run_service.run_to_info(run_model, paths=paths)
        info.username = current_user.username

        cache_service.cache_run(run_id, info.model_dump())
        cache_service.invalidate_user_runs_list(str(current_user.id))

        # Update conversation to reflect run creation
        conversation.state = ConversationState.COMPLETED.value
        conversation.run_id = run_id
        conversation.completed_at = now_utc
        conversation.updated_at = now_utc
        conversation.context = context.model_dump()
        db.commit()

        audit_logger.log(
            event_type=AuditEventType.RUN_CREATED,
            message=f"Created run {run_id} from conversation {conversation_id}",
            user_id=str(current_user.id),
            username=current_user.username,
            run_id=run_id,
            details={
                "conversation_id": conversation_id,
                "prompt": prompt
            }
        )

        return ConversationCreateRunResponse(
            run_id=run_id,
            message=f"Optimization started! You can track progress at /runs/{run_id}"
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create run: {str(e)}")


@router.delete("/{conversation_id}")
async def abandon_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_active_user),
    db: DBSession = Depends(get_db)
):
    """Abandon a conversation."""
    # Get conversation
    conversation = db.query(ConversationModel).filter(
        ConversationModel.id == conversation_id,
        ConversationModel.user_id == current_user.id
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Mark as abandoned
    conversation.state = ConversationState.ABANDONED
    db.commit()

    return {"message": "Conversation abandoned"}

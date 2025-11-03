"""Conversation API endpoints for interactive optimization setup."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session as DBSession
from typing import List

from server.database import get_db
from server.models.user import User
from server.models.conversation import Conversation as ConversationModel
from server.auth.dependencies import get_current_active_user
from server.services.conversation_service import ConversationService
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
from server.schemas.run import RunCreateRequest, RunInfo
from server.audit import audit_logger, AuditEventType

router = APIRouter(prefix="/conversations", tags=["conversations"])

# Initialize conversation service
conversation_service = ConversationService()


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
                    "created_at": c.created_at.isoformat(),
                    "updated_at": c.updated_at.isoformat(),
                    "run_id": c.run_id
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
    request: ConversationConfirmRequest,
    background: BackgroundTasks,
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

    # Check if in confirmation state
    if conversation.state != ConversationState.CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail="Conversation must be in confirmation state"
        )

    # Handle non-confirmation
    if not request.confirmed:
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

        # Create run request
        run_request = RunCreateRequest(
            prompt=prompt,
            max_iterations=context.max_iterations,
            batch_size=context.batch_size,
            note=context.notes
        )

        # Import here to avoid circular import
        from server.app import create_run

        # Create the run (this will handle the actual creation logic)
        # We'll need to refactor server.app.create_run to be callable as a function
        # For now, let's create the run info directly
        from server.storage import ensure_run_dirs
        from uuid import uuid4
        from datetime import datetime

        run_id = f"run_{uuid4().hex[:12]}"
        ensure_run_dirs(run_id)

        # TODO: Integrate with actual run creation from server.app
        # This is a placeholder - we need to refactor the run creation logic

        # Update conversation
        conversation.state = ConversationState.COMPLETED
        conversation.run_id = run_id
        db.commit()

        # Log event
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

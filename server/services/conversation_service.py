"""Conversation service for managing interactive optimization dialogues.

This service implements a state machine that guides users through collecting
all necessary information for starting an optimization run.
"""

import json
import re
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy.orm import Session

from server.models.conversation import (
    Conversation as ConversationModel,
    ConversationMessage,
)
from server.models.session import Session as SessionModel
from server.schemas.conversation import (
    ConversationState,
    ConversationContext,
    OptimizationMode,
    TargetProperty,
)


class ConversationService:
    """Service for managing conversational UI interactions."""

    def __init__(self, llm_client=None):
        """Initialize conversation service.

        Args:
            llm_client: Optional LLM client for NLU parsing
        """
        self.llm_client = llm_client

    def start_conversation(
        self,
        db: Session,
        user_id,
        initial_message: Optional[str] = None
    ) -> Tuple[ConversationModel, str, List[str]]:
        """Start a new conversation.

        Args:
            db: Database session
            user_id: User ID
            initial_message: Optional initial message from user

        Returns:
            Tuple of (conversation, response_message, suggestions)
        """
        # Ensure we have an active session to associate
        session = db.query(SessionModel).filter(
            SessionModel.user_id == user_id,
            SessionModel.is_active == True
        ).order_by(SessionModel.created_at.desc()).first()

        now = datetime.now(timezone.utc)
        if not session:
            session = SessionModel(
                user_id=user_id,
                token=f"conv-session-{uuid4().hex}",
                ip_address=None,
                user_agent="conversation-service",
                created_at=now,
                last_activity=now,
                expires_at=now + timedelta(hours=24),
                is_active=True,
                extra_metadata={"source": "conversation_service"},
            )
            db.add(session)
            db.flush()

        # Create conversation
        conversation = ConversationModel(
            user_id=user_id,
            session_id=session.id,
            status=ConversationState.GREETING.value,
            context=ConversationContext().model_dump()
        )
        db.add(conversation)
        db.flush()

        # Parse initial message if provided
        if initial_message:
            context = ConversationContext(**conversation.context)
            context, new_state = self._parse_initial_message(initial_message, context)
            conversation.context = context.model_dump()
            conversation.state = new_state.value

        # Generate response
        conversation.updated_at = now
        db.commit()
        db.refresh(conversation)

        message, suggestions = self._generate_response(
            ConversationState(conversation.state),
            conversation.context
        )

        return conversation, message, suggestions

    def send_message(
        self,
        db: Session,
        conversation: ConversationModel,
        message: str
    ) -> Tuple[str, ConversationState, List[str]]:
        """Process user message and update conversation state.

        Args:
            db: Database session
            conversation: Conversation model
            message: User message

        Returns:
            Tuple of (response_message, new_state, suggestions)
        """
        context = ConversationContext(**conversation.context)
        current_state = ConversationState(conversation.state)

        # Record user message
        user_message = ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content=message,
            extra_metadata={},
        )
        db.add(user_message)

        # Parse message based on current state
        context, new_state = self._parse_message(message, context, current_state)

        # Update conversation
        conversation.context = context.model_dump()
        conversation.state = new_state.value
        conversation.updated_at = datetime.now(timezone.utc)

        # Generate response
        response, suggestions = self._generate_response(new_state, conversation.context)

        # Record assistant response
        assistant_message = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=response,
            extra_metadata={},
        )
        db.add(assistant_message)

        db.commit()
        db.refresh(conversation)

        return response, new_state, suggestions

    def _parse_initial_message(
        self,
        message: str,
        context: ConversationContext
    ) -> Tuple[ConversationContext, ConversationState]:
        """Parse initial message to extract any upfront information.

        Args:
            message: User message
            context: Current context

        Returns:
            Updated context and new state
        """
        # Try to extract molecule
        molecule_info = self._extract_molecule(message)
        if molecule_info:
            context.starting_molecule = molecule_info.get("smiles")
            context.molecule_name = molecule_info.get("name")
            context.molecule_source = molecule_info.get("source", "unknown")

        # Try to extract targets
        targets = self._extract_targets(message)
        if targets:
            context.targets.extend(targets)

        # Determine next state
        if context.starting_molecule and context.targets:
            return context, ConversationState.COLLECTING_PARAMETERS
        elif context.starting_molecule:
            return context, ConversationState.COLLECTING_TARGETS
        else:
            return context, ConversationState.COLLECTING_MOLECULE

    def _parse_message(
        self,
        message: str,
        context: ConversationContext,
        current_state: ConversationState
    ) -> Tuple[ConversationContext, ConversationState]:
        """Parse message based on current state.

        Args:
            message: User message
            context: Current context
            current_state: Current conversation state

        Returns:
            Updated context and new state
        """
        if current_state == ConversationState.COLLECTING_MOLECULE:
            return self._parse_molecule_message(message, context)
        elif current_state == ConversationState.COLLECTING_TARGETS:
            return self._parse_targets_message(message, context)
        elif current_state == ConversationState.COLLECTING_PARAMETERS:
            return self._parse_parameters_message(message, context)
        elif current_state == ConversationState.CONFIRMATION:
            return self._parse_confirmation_message(message, context)
        else:
            # Default: try to extract any useful information
            return self._parse_initial_message(message, context)

    def _parse_molecule_message(
        self,
        message: str,
        context: ConversationContext
    ) -> Tuple[ConversationContext, ConversationState]:
        """Parse molecule collection message."""
        molecule_info = self._extract_molecule(message)

        if molecule_info:
            context.starting_molecule = molecule_info.get("smiles")
            context.molecule_name = molecule_info.get("name")
            context.molecule_source = molecule_info.get("source", "unknown")

            # Move to collecting targets
            return context, ConversationState.COLLECTING_TARGETS
        else:
            # Need clarification
            context.needs_clarification.append("molecule")
            return context, ConversationState.COLLECTING_MOLECULE

    def _parse_targets_message(
        self,
        message: str,
        context: ConversationContext
    ) -> Tuple[ConversationContext, ConversationState]:
        """Parse targets collection message."""
        targets = self._extract_targets(message)

        if targets:
            context.targets.extend(targets)
            # Move to collecting parameters
            return context, ConversationState.COLLECTING_PARAMETERS
        else:
            context.needs_clarification.append("targets")
            return context, ConversationState.COLLECTING_TARGETS

    def _parse_parameters_message(
        self,
        message: str,
        context: ConversationContext
    ) -> Tuple[ConversationContext, ConversationState]:
        """Parse parameters collection message."""
        # Extract iterations
        iterations_match = re.search(r'(\d+)\s*iterations?', message, re.IGNORECASE)
        if iterations_match:
            context.max_iterations = int(iterations_match.group(1))

        # Extract batch size
        batch_match = re.search(r'batch(?:\s+size)?\s*of\s*(\d+)', message, re.IGNORECASE)
        if not batch_match:
            batch_match = re.search(r'(\d+)\s*(?:molecules?|compounds?)\s+per\s+batch', message, re.IGNORECASE)
        if batch_match:
            context.batch_size = int(batch_match.group(1))

        # Extract notes
        notes_match = re.search(r'note[s]?:\s*(.+)', message, re.IGNORECASE)
        if notes_match:
            context.notes = notes_match.group(1).strip()

        # Check if we have everything
        if context.max_iterations and context.batch_size:
            return context, ConversationState.CONFIRMATION
        else:
            return context, ConversationState.COLLECTING_PARAMETERS

    def _parse_confirmation_message(
        self,
        message: str,
        context: ConversationContext
    ) -> Tuple[ConversationContext, ConversationState]:
        """Parse confirmation message."""
        message_lower = message.lower()

        if any(word in message_lower for word in ['yes', 'correct', 'looks good', 'confirm', 'proceed', 'start']):
            return context, ConversationState.COMPLETED
        elif any(word in message_lower for word in ['no', 'change', 'modify', 'edit']):
            # TODO: Implement change handling
            return context, ConversationState.COLLECTING_PARAMETERS
        else:
            return context, ConversationState.CONFIRMATION

    def _extract_molecule(self, message: str) -> Optional[Dict[str, Any]]:
        """Extract molecule information from message.

        Args:
            message: User message

        Returns:
            Dictionary with molecule info or None
        """
        # Check for SMILES pattern
        smiles_patterns = [
            r'SMILES[:\s]+([A-Za-z0-9@+\-\[\]\(\)=#$]+)',
            r'\b([A-Z][a-z]?[A-Z0-9@+\-\[\]\(\)=#$]{5,})\b'  # Simple SMILES heuristic
        ]

        for pattern in smiles_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                smiles = match.group(1)
                return {
                    "smiles": smiles,
                    "source": "smiles",
                    "name": None
                }

        # Check for common molecule names
        common_molecules = {
            "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
            "ibuprofen": "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
            "caffeine": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
            "paracetamol": "CC(=O)Nc1ccc(O)cc1",
            "acetaminophen": "CC(=O)Nc1ccc(O)cc1",
        }

        message_lower = message.lower()
        for name, smiles in common_molecules.items():
            if name in message_lower:
                return {
                    "smiles": smiles,
                    "source": "name",
                    "name": name
                }

        return None

    def _extract_targets(self, message: str) -> List[TargetProperty]:
        """Extract optimization targets from message.

        Args:
            message: User message

        Returns:
            List of target properties
        """
        targets = []
        message_lower = message.lower()

        # Property patterns
        property_patterns = {
            "qed": r'\bqed\b',
            "logP": r'\blog\s*p\b',
            "SA_Score": r'\b(?:sa[_\s]?score|synthetic\s+accessibility)\b',
            "molecular_weight": r'\b(?:mw|molecular\s+weight)\b',
        }

        # Mode patterns
        for prop_name, pattern in property_patterns.items():
            if re.search(pattern, message_lower):
                # Determine mode
                mode = OptimizationMode.MAXIMIZE  # Default
                target_value = None

                if any(word in message_lower for word in ['maximize', 'max', 'increase', 'improve', 'higher']):
                    mode = OptimizationMode.MAXIMIZE
                elif any(word in message_lower for word in ['minimize', 'min', 'decrease', 'lower', 'reduce']):
                    mode = OptimizationMode.MINIMIZE
                elif any(word in message_lower for word in ['match', 'target', 'around', 'approximately']):
                    mode = OptimizationMode.MATCH
                    # Try to extract target value
                    value_match = re.search(rf'{pattern}[:\s]+(?:around|approximately|~)?\s*([\d.]+)', message_lower)
                    if value_match:
                        target_value = float(value_match.group(1))

                targets.append(TargetProperty(
                    name=prop_name,
                    mode=mode,
                    target_value=target_value,
                    weight=1.0
                ))

        return targets

    def _generate_response(
        self,
        state: ConversationState,
        context: Dict[str, Any]
    ) -> Tuple[str, List[str]]:
        """Generate response message based on state and context.

        Args:
            state: Current conversation state
            context: Conversation context

        Returns:
            Tuple of (response_message, suggestions)
        """
        ctx = ConversationContext(**context)

        if state == ConversationState.GREETING:
            return (
                "Hi! I'll help you set up a molecular optimization run. "
                "What molecule would you like to optimize? You can provide a SMILES string or a molecule name.",
                ["Aspirin", "Ibuprofen", "Show me examples"]
            )

        elif state == ConversationState.COLLECTING_MOLECULE:
            if "molecule" in ctx.needs_clarification:
                return (
                    "I couldn't understand the molecule. Please provide either:\n"
                    "- A SMILES string (e.g., 'CC(=O)Oc1ccccc1C(=O)O')\n"
                    "- A common molecule name (e.g., 'aspirin')",
                    ["CC(=O)Oc1ccccc1C(=O)O", "Aspirin"]
                )
            return (
                "What molecule would you like to optimize?",
                ["Aspirin", "Caffeine", "Ibuprofen"]
            )

        elif state == ConversationState.COLLECTING_TARGETS:
            molecule_desc = ctx.molecule_name or ctx.starting_molecule[:20] + "..."
            return (
                f"Great! You're optimizing {molecule_desc}. "
                f"What properties would you like to optimize? (e.g., QED, logP, SA_Score)",
                ["Maximize QED", "Minimize logP", "Improve synthetic accessibility"]
            )

        elif state == ConversationState.COLLECTING_PARAMETERS:
            target_desc = ", ".join([f"{t.mode.value} {t.name}" for t in ctx.targets])
            return (
                f"Perfect! You want to {target_desc}. "
                f"How many iterations and molecules per batch would you like? "
                f"(Default: 10 iterations, 5 molecules per batch)",
                ["10 iterations, 5 per batch", "20 iterations, 10 per batch", "Use defaults"]
            )

        elif state == ConversationState.CONFIRMATION:
            # Generate summary
            molecule_desc = ctx.molecule_name or ctx.starting_molecule
            targets_desc = "\n".join([
                f"  - {t.mode.value.capitalize()} {t.name}" +
                (f" (target: {t.target_value})" if t.target_value else "")
                for t in ctx.targets
            ])

            summary = (
                f"Here's your optimization configuration:\n\n"
                f"Molecule: {molecule_desc}\n"
                f"Targets:\n{targets_desc}\n"
                f"Max iterations: {ctx.max_iterations or 10}\n"
                f"Batch size: {ctx.batch_size or 5}\n"
            )

            if ctx.notes:
                summary += f"Notes: {ctx.notes}\n"

            summary += "\nDoes this look correct?"

            return (summary, ["Yes, start optimization", "No, let me change something"])

        elif state == ConversationState.COMPLETED:
            return (
                "Configuration confirmed! Creating your optimization run...",
                []
            )

        return ("I'm not sure what to do next. Let's start over.", ["Start over"])

    def can_create_run(self, context: Dict[str, Any]) -> bool:
        """Check if context has all required information to create a run.

        Args:
            context: Conversation context dict

        Returns:
            True if run can be created
        """
        ctx = ConversationContext(**context)
        return (
            ctx.starting_molecule is not None and
            len(ctx.targets) > 0 and
            ctx.max_iterations is not None and
            ctx.batch_size is not None
        )

    def build_run_prompt(self, context: Dict[str, Any]) -> str:
        """Build optimization prompt from conversation context.

        Args:
            context: Conversation context dict

        Returns:
            Optimization prompt string
        """
        ctx = ConversationContext(**context)

        # Build targets description
        targets_desc = []
        for target in ctx.targets:
            if target.mode == OptimizationMode.MAXIMIZE:
                targets_desc.append(f"maximize {target.name}")
            elif target.mode == OptimizationMode.MINIMIZE:
                targets_desc.append(f"minimize {target.name}")
            elif target.mode == OptimizationMode.MATCH and target.target_value is not None:
                targets_desc.append(f"match {target.name} to {target.target_value}")

        molecule_desc = ctx.molecule_name or ctx.starting_molecule

        prompt = (
            f"Optimize {molecule_desc} (SMILES: {ctx.starting_molecule}) "
            f"to {', '.join(targets_desc)}."
        )

        if ctx.notes:
            prompt += f" Notes: {ctx.notes}"

        return prompt

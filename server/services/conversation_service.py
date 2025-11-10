"""Conversation service for managing interactive optimization dialogues.

This service implements a state machine that guides users through collecting
all necessary information for starting an optimization run.
"""

import logging
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
from nodes.extract_arguments_hybrid import HybridArgumentExtractor


logger = logging.getLogger(__name__)


class ConversationService:
    """Service for managing conversational UI interactions."""

    def __init__(self, llm_client=None):
        """Initialize conversation service.

        Args:
            llm_client: Optional LLM client for NLU parsing
        """
        self.llm_client = llm_client

        self.field_question_prompts = {
            "starting_molecule": (
                "What molecule should we optimize? You can share a SMILES string or a common name."
            ),
            "targets": (
                "Which properties should we optimize? For example, 'maximize QED and keep logP around 2.5'."
            ),
            "max_iterations": "How many iterations do you want to run this for?",
            "batch_size": "How many molecules should we evaluate per iteration (batch size)?",
            "enumeration_size": (
                "How many molecules should I enumerate in the initial library? (Optional)"
            ),
        }

        self.field_suggestions = {
            "starting_molecule": [
                "CC(=O)Oc1ccccc1C(=O)O",
                "Aspirin",
                "Caffeine",
            ],
            "targets": [
                "Maximize QED",
                "Minimize logP",
                "Match logP around 2.5",
            ],
            "max_iterations": [
                "10 iterations",
                "12 iterations",
                "Use 15 iterations",
            ],
            "batch_size": [
                "Batch size 5",
                "Batch size 8",
                "Batch size 10",
            ],
            "enumeration_size": [
                "Enumerate 100 molecules",
                "Enumerate 200 molecules",
                "Enumerate 500 molecules",
            ],
        }

        self.property_keyword_map: Dict[str, List[str]] = {
            "qed": ["qed", "drug-likeness", "drug likeness", "druglike"],
            "logp": ["logp", "lipophilicity", "hydrophobicity", "partition"],
            "tpsa": ["tpsa", "polar surface area", "psa"],
            "molecular_weight": ["molecular weight", "mw", "weight", "mass"],
            "sa_score": ["sa score", "synthetic accessibility", "synthetic-accessibility"],
            "binding_affinity": ["binding", "affinity", "uniprot", "docking"],
        }

        self.argument_extractor = HybridArgumentExtractor(llm_client=llm_client)

    def _append_to_full_prompt(self, current: Optional[str], message: Optional[str]) -> str:
        """Append a new message to the accumulated prompt text."""
        if not message:
            return (current or "").strip()

        message = message.strip()
        if not message:
            return (current or "").strip()

        if not current:
            return message

        combined = f"{current.strip()}\n{message}"
        # Limit stored prompt to avoid unbounded growth (keep last 4000 characters)
        return combined[-4000:]

    def _process_new_message(
        self,
        context: ConversationContext,
        message: Optional[str],
        current_state: Optional[ConversationState] = None
    ) -> Tuple[ConversationContext, ConversationState]:
        """Update context based on a new user message."""
        context.full_prompt = self._append_to_full_prompt(context.full_prompt, message)

        context = self._update_context_from_prompt(context)

        required_missing, _ = self._determine_missing_fields(context)
        context.needs_clarification = required_missing

        # Track which clarifications have been requested, keeping order stable
        context.clarifications_asked = [
            field for field in context.clarifications_asked if field in required_missing
        ]
        for field in required_missing:
            if field not in context.clarifications_asked:
                context.clarifications_asked.append(field)

        next_state = self._determine_state(context)

        if current_state == ConversationState.CONFIRMATION:
            transition = self._confirmation_transition(message or "", context)
            if transition:
                next_state = transition

        return context, next_state

    def _update_context_from_prompt(self, context: ConversationContext) -> ConversationContext:
        """Extract structured data from the accumulated prompt."""
        prompt_text = (context.full_prompt or "").strip()
        if not prompt_text:
            return context

        extraction = self._parse_prompt(prompt_text)
        if extraction:
            context = self._apply_extraction_to_context(context, extraction, prompt_text)

        notes = self._extract_notes(prompt_text)
        if notes:
            context.notes = notes

        return context

    def _parse_prompt(self, prompt_text: str) -> Dict[str, Any]:
        """Run hybrid extraction pipeline against the prompt text."""
        if not self.argument_extractor:
            return {}

        llm_result: Optional[Dict[str, Any]] = None
        try:
            llm_result = self.argument_extractor.extract_with_llm(prompt_text)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("LLM extraction failed: %s", exc)

        rule_result: Dict[str, Any] = {}
        try:
            rule_result = self.argument_extractor.extract_with_rules(prompt_text)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Rule-based extraction failed: %s", exc)

        try:
            return self.argument_extractor.validate_and_merge(
                llm_result,
                rule_result,
                prompt_text,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to merge extraction results: %s", exc)
            return rule_result or {}

    def _apply_extraction_to_context(
        self,
        context: ConversationContext,
        extraction: Dict[str, Any],
        prompt_text: str
    ) -> ConversationContext:
        """Populate conversation context from extracted information."""

        starting_molecules = extraction.get("starting_molecules") or []
        if starting_molecules:
            smiles_candidate = next(
                (item for item in starting_molecules if self._looks_like_smiles(item)),
                None,
            )
            name_candidate = next(
                (item for item in starting_molecules if not self._looks_like_smiles(item)),
                None,
            )

            if smiles_candidate:
                context.starting_molecule = smiles_candidate
            if name_candidate:
                context.molecule_name = name_candidate

        molecule_source = extraction.get("molecule_source")
        if molecule_source:
            context.molecule_source = molecule_source

        proteins = extraction.get("proteins") or []
        if proteins:
            context.protein_target = proteins[0]

        max_iterations = extraction.get("max_iterations")
        if max_iterations is not None:
            context.max_iterations = int(max_iterations)

        batch_size = extraction.get("batch_size")
        if batch_size is not None:
            context.batch_size = int(batch_size)

        enumeration_size = extraction.get("enumeration_size")
        if enumeration_size is not None:
            context.enumeration_size = int(enumeration_size)

        targets = self._convert_targets(
            extraction.get("target_properties") or [],
            prompt_text,
        )
        if targets:
            context.targets = targets

        return context

    def _convert_targets(
        self,
        raw_targets: List[Dict[str, Any]],
        prompt_text: str
    ) -> List[TargetProperty]:
        """Convert extracted target dictionaries to conversation models."""
        converted: List[TargetProperty] = []

        for target_data in raw_targets:
            property_name = target_data.get("property_name") or target_data.get("name")
            if not property_name:
                continue

            canonical = property_name.upper()
            mode = self._normalize_mode(target_data.get("optimization_mode"))

            snippet = self._find_property_snippet(prompt_text, property_name)
            if snippet:
                inferred_mode = self._infer_target_mode(snippet)
                if inferred_mode != mode:
                    mode = inferred_mode
            else:
                snippet = None

            target_value = target_data.get("target_value")

            inferred_value = self._infer_target_value(snippet, mode) if snippet else None
            if target_value is None and inferred_value is not None:
                target_value = inferred_value

            if target_value is None and mode == OptimizationMode.MATCH:
                bounds = target_data.get("bounds")
                if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                    lower, upper = bounds
                    if lower is not None and upper is not None:
                        target_value = round((float(lower) + float(upper)) / 2.0, 3)

            weight = float(target_data.get("weight") or 1.0)

            try:
                converted.append(
                    TargetProperty(
                        name=canonical,
                        mode=mode,
                        target_value=target_value,
                        weight=weight,
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.debug("Skipping target %s due to validation error: %s", property_name, exc)

        if converted:
            equal_weight = 1.0 / len(converted)
            for target in converted:
                target.weight = equal_weight

        return converted

    def _normalize_mode(self, value: Optional[str]) -> OptimizationMode:
        """Normalize optimization mode strings to enum values."""
        if not value:
            return OptimizationMode.MAXIMIZE

        normalized = str(value).strip().lower()
        if normalized in {"max", "maximize", "maximum", "increase"}:
            return OptimizationMode.MAXIMIZE
        if normalized in {"min", "minimize", "minimum", "decrease", "reduce"}:
            return OptimizationMode.MINIMIZE
        if normalized in {"match", "target", "equal", "maintain"}:
            return OptimizationMode.MATCH

        return OptimizationMode.MAXIMIZE

    def _find_property_snippet(self, prompt_text: str, property_name: str) -> Optional[str]:
        """Grab a clause around a property mention for heuristics."""
        keywords = self.property_keyword_map.get(property_name.lower(), [property_name])
        pattern = re.compile("|".join(re.escape(keyword) for keyword in keywords), re.IGNORECASE)
        matches = list(pattern.finditer(prompt_text))
        if not matches:
            return None

        match = matches[-1]

        separators = [",", ";", ".", " and ", " but "]

        # Determine clause start by looking backward for nearest separator
        before_candidates = [
            (prompt_text.rfind(sep, 0, match.start()), len(sep))
            for sep in separators
        ]
        before_candidates = [item for item in before_candidates if item[0] != -1]
        if before_candidates:
            before_index, sep_len = max(before_candidates, key=lambda item: item[0])
            clause_start = before_index + sep_len
        else:
            clause_start = 0

        # Determine clause end by looking forward for nearest separator
        after_candidates = [prompt_text.find(sep, match.end()) for sep in separators]
        after_candidates = [idx for idx in after_candidates if idx != -1]
        if after_candidates:
            clause_end = min(after_candidates)
        else:
            clause_end = len(prompt_text)

        snippet = prompt_text[clause_start:clause_end].strip()
        if not snippet:
            snippet = prompt_text[max(0, match.start() - 50):min(len(prompt_text), match.end() + 50)]
        return snippet

    def _has_explicit_iterations(self, prompt_text: str) -> bool:
        patterns = [
            r"\b\d+\s*(?:[A-Za-z][A-Za-z0-9\-]*\s+){0,2}?(?:iterations?|rounds?|cycles?)\b",
            r"(?:iterations?|rounds?|cycles?)\s*(?:for|of|=|:)?\s*\d+",
            r"run\s+\d+\s*(?:[A-Za-z][A-Za-z0-9\-]*\s+){0,2}?(?:iterations?|rounds?|cycles?)",
        ]
        return any(re.search(pattern, prompt_text, re.IGNORECASE) for pattern in patterns)

    def _has_explicit_batch_size(self, prompt_text: str) -> bool:
        patterns = [
            r"batch(?:\s+size)?\s*(?:of|=|:)?\s*\d+",
            r"\b\d+\s*(?:molecules?|compounds?)\s+per\s+(?:batch|iteration)\b",
            r"\b\d+\s*per\s+(?:batch|iteration)\b",
        ]
        return any(re.search(pattern, prompt_text, re.IGNORECASE) for pattern in patterns)

    def _has_explicit_enumeration_size(self, prompt_text: str) -> bool:
        patterns = [
            r"enumerat(?:e|ed|ion)[^\d]{0,20}(\d{1,4})",
            r"generate\s*(\d{1,4})\s+(?:analogs|derivatives|molecules|compounds)",
            r"(\d{1,4})\s+(?:molecules|compounds|analogs|derivatives)\b",
        ]
        return any(re.search(pattern, prompt_text, re.IGNORECASE) for pattern in patterns)

    def _mentioned_defaults(self, prompt_text: str) -> bool:
        return bool(re.search(r"use\s+defaults?", prompt_text, re.IGNORECASE))

    def _compute_explicit_flags(self, context: ConversationContext) -> Dict[str, bool]:
        prompt_text = (context.full_prompt or "").lower()
        if not prompt_text:
            return {
                "starting_molecule": False,
                "targets": False,
                "max_iterations": False,
                "batch_size": False,
                "enumeration_size": False,
            }

        defaults_ok = self._mentioned_defaults(prompt_text)

        return {
            "starting_molecule": bool(context.starting_molecule or context.molecule_name),
            "targets": bool(context.targets),
            "max_iterations": self._has_explicit_iterations(prompt_text) or defaults_ok,
            "batch_size": self._has_explicit_batch_size(prompt_text) or defaults_ok,
            "enumeration_size": self._has_explicit_enumeration_size(prompt_text),
        }

    def _extract_notes(self, prompt_text: str) -> Optional[str]:
        """Extract notes annotation from prompt text."""
        matches = list(re.finditer(r"notes?\s*(?:=|:)?\s*(.+)", prompt_text, re.IGNORECASE))
        if not matches:
            return None
        return matches[-1].group(1).strip()

    def _determine_missing_fields(
        self,
        context: ConversationContext
    ) -> Tuple[List[str], List[str]]:
        """Determine which required fields are missing."""
        flags = self._compute_explicit_flags(context)

        required_missing: List[str] = []
        optional_missing: List[str] = []

        if not flags["starting_molecule"]:
            required_missing.append("starting_molecule")

        if not flags["targets"]:
            required_missing.append("targets")

        if context.max_iterations is None or not flags["max_iterations"]:
            required_missing.append("max_iterations")

        if context.batch_size is None or not flags["batch_size"]:
            required_missing.append("batch_size")

        if context.enumeration_size is None or not flags["enumeration_size"]:
            optional_missing.append("enumeration_size")

        return required_missing, optional_missing

    def _determine_state(self, context: ConversationContext) -> ConversationState:
        """Infer conversation state from collected context."""
        missing_required, _ = self._determine_missing_fields(context)

        if "starting_molecule" in missing_required:
            return ConversationState.COLLECTING_MOLECULE

        if "targets" in missing_required:
            return ConversationState.COLLECTING_TARGETS

        if any(field in missing_required for field in ("max_iterations", "batch_size")):
            return ConversationState.COLLECTING_PARAMETERS

        if self.can_create_run(context.model_dump()):
            return ConversationState.CONFIRMATION

        return ConversationState.COLLECTING_PARAMETERS

    def _build_summary_lines(
        self,
        context: ConversationContext,
        explicit_flags: Dict[str, bool]
    ) -> List[str]:
        """Create human-friendly summary lines of collected info."""
        lines: List[str] = []

        if context.starting_molecule or context.molecule_name:
            molecule_desc = context.molecule_name or context.starting_molecule
            lines.append(f"- Starting molecule: {molecule_desc}")

        if context.targets:
            target_descriptions: List[str] = []
            for target in context.targets:
                description = f"{target.mode.value} {target.name}"
                if target.target_value is not None:
                    description += f" (target {target.target_value})"
                target_descriptions.append(description)

            if target_descriptions:
                lines.append("- Targets: " + "; ".join(target_descriptions))

        if context.max_iterations is not None:
            label = f"- Iterations: {context.max_iterations}"
            if not explicit_flags.get("max_iterations", True):
                label += " (default)"
            lines.append(label)

        if context.batch_size is not None:
            label = f"- Batch size: {context.batch_size}"
            if not explicit_flags.get("batch_size", True):
                label += " (default)"
            lines.append(label)

        if context.enumeration_size is not None:
            label = f"- Enumeration size: {context.enumeration_size}"
            if not explicit_flags.get("enumeration_size", True):
                label += " (optional/default)"
            lines.append(label)

        if context.notes:
            lines.append(f"- Notes: {context.notes}")

        return lines

    def _missing_field_prompt(
        self,
        missing_required: List[str],
        missing_optional: List[str]
    ) -> str:
        """Build follow-up question for missing fields."""
        prompts: List[str] = []

        for field in missing_required:
            question = self.field_question_prompts.get(field)
            if question:
                prompts.append(question)

        optional_questions = [
            self.field_question_prompts[field]
            for field in missing_optional
            if field in self.field_question_prompts
        ]

        if optional_questions:
            prompts.append("Optional: " + " ".join(optional_questions))

        return "\n\n".join(prompts)

    def _infer_target_mode(self, snippet: str) -> OptimizationMode:
        """Infer optimization mode from nearby text."""
        snippet = snippet.lower()

        if re.search(r"between\s+\d", snippet) or re.search(r"within\s+\d", snippet):
            return OptimizationMode.MATCH

        if re.search(r"less\s+than|below|under|decrease|reduce|minim(?:ize|um)|<=|<", snippet):
            return OptimizationMode.MINIMIZE

        if re.search(r"greater\s+than|above|over|increase|maxim(?:ize|um)|higher|>=|>", snippet):
            return OptimizationMode.MAXIMIZE

        if re.search(r"match|target|keep|maintain|around|approx|equal", snippet):
            return OptimizationMode.MATCH

        if re.search(r"min\b", snippet):
            return OptimizationMode.MINIMIZE

        if re.search(r"max\b", snippet):
            return OptimizationMode.MAXIMIZE

        return OptimizationMode.MAXIMIZE

    def _infer_target_value(
        self,
        snippet: str,
        mode: OptimizationMode
    ) -> Optional[float]:
        """Extract a representative target value when matching."""
        if mode != OptimizationMode.MATCH:
            return None

        snippet = snippet.lower()

        between_match = re.search(
            r"between\s*(\d+(?:\.\d+)?)\s*(?:and|to|[-–])\s*(\d+(?:\.\d+)?)",
            snippet
        )
        if between_match:
            low = float(between_match.group(1))
            high = float(between_match.group(2))
            return round((low + high) / 2.0, 3)

        value_match = re.search(
            r"(?:around|about|near|approx(?:\.|imately)?|target(?:ing)?|match(?:ing)?|keep(?:ing)?|=|to|at)\s*(\d+(?:\.\d+)?)",
            snippet
        )
        if value_match:
            return float(value_match.group(1))

        return None

    def _suggestions_for_missing(
        self,
        missing_required: List[str],
        missing_optional: List[str]
    ) -> List[str]:
        """Suggest quick replies for missing items."""
        suggestions: List[str] = []
        seen: set[str] = set()

        for field in missing_required + missing_optional:
            for suggestion in self.field_suggestions.get(field, []):
                if suggestion not in seen:
                    suggestions.append(suggestion)
                    seen.add(suggestion)

        return suggestions[:3]

    def _confirmation_transition(
        self,
        message: str,
        context: ConversationContext
    ) -> Optional[ConversationState]:
        """Handle confirmation state responses."""
        if not message:
            return None

        message_lower = message.lower()

        if any(word in message_lower for word in [
            "yes",
            "confirm",
            "looks good",
            "go ahead",
            "proceed",
            "start",
            "sounds good",
        ]):
            if self.can_create_run(context.model_dump()):
                return ConversationState.COMPLETED

        change_keywords = ["no", "change", "modify", "edit", "update", "adjust"]
        if any(word in message_lower for word in change_keywords):
            if any(token in message_lower for token in ["molecule", "starting"]):
                return ConversationState.COLLECTING_MOLECULE
            if any(token in message_lower for token in ["target", "property"]):
                return ConversationState.COLLECTING_TARGETS
            return ConversationState.COLLECTING_PARAMETERS

        return None

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

        # Process initial message (if any) to pre-populate context
        context = ConversationContext(**conversation.context)
        context, new_state = self._process_new_message(
            context,
            initial_message,
            ConversationState.GREETING
        )
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

        # Parse message using accumulated prompt parsing
        context, new_state = self._process_new_message(message=message, context=context, current_state=current_state)

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


    def _looks_like_smiles(self, token: str) -> bool:
        """Heuristic check to see if a token resembles a SMILES string."""
        if not token or " " in token or len(token) > 200:
            return False

        if re.fullmatch(r"[A-Za-z]+", token):
            return False

        if not re.search(r"[A-Za-z]", token):
            return False

        if not re.search(r"[=#@()\[\]\.0-9]", token) and not re.search(r"(Cl|Br)", token):
            return False

        return True

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

        explicit_flags = self._compute_explicit_flags(ctx)
        required_missing, optional_missing = self._determine_missing_fields(ctx)
        summary_lines = self._build_summary_lines(ctx, explicit_flags)
        summary_section = ""
        if summary_lines:
            summary_section = "Here's what I have so far:\n" + "\n".join(summary_lines)

        if state == ConversationState.GREETING:
            return (
                "Hi! I'll help you set up a molecular optimization run. "
                "What molecule would you like to optimize? You can provide a SMILES string or a molecule name.",
                ["Aspirin", "Ibuprofen", "Show me examples"]
            )

        if state == ConversationState.COLLECTING_MOLECULE:
            ask_fields = ["starting_molecule"]
            prompt = self._missing_field_prompt(ask_fields, [])
            message_parts = []
            if summary_section:
                message_parts.append(summary_section)
            message_parts.append(prompt)
            message = "\n\n".join(part for part in message_parts if part).strip()
            suggestions = self._suggestions_for_missing(ask_fields, []) or [
                "Aspirin",
                "Caffeine",
                "CC(=O)Oc1ccccc1C(=O)O",
            ]
            return message, suggestions

        if state == ConversationState.COLLECTING_TARGETS:
            ask_fields = ["targets"]
            prompt = self._missing_field_prompt(ask_fields, [])
            message_parts = []
            if summary_section:
                message_parts.append(summary_section)
            message_parts.append(prompt)
            message_parts.append("You can mention goals like 'maximize QED and keep logP around 2.5'.")
            message = "\n\n".join(part for part in message_parts if part).strip()
            suggestions = self._suggestions_for_missing(ask_fields, []) or [
                "Maximize QED",
                "Minimize logP",
                "Match logP around 2.5",
            ]
            return message, suggestions

        if state == ConversationState.COLLECTING_PARAMETERS:
            ask_fields = [
                field for field in ("max_iterations", "batch_size")
                if field in required_missing
            ]
            optional_fields = [
                "enumeration_size"
            ] if "enumeration_size" in optional_missing else []
            prompt = self._missing_field_prompt(ask_fields, optional_fields)
            message_parts = []
            if summary_section:
                message_parts.append(summary_section)
            if prompt:
                message_parts.append(prompt)
            message_parts.append("Defaults are 10 iterations with 5 molecules per batch if you'd like to use them.")
            message = "\n\n".join(part for part in message_parts if part).strip()
            suggestions = self._suggestions_for_missing(ask_fields, optional_fields) or [
                "10 iterations, batch size 5",
                "12 iterations, batch size 8",
                "Batch size 5 and 10 iterations",
            ]
            return message, suggestions

        if state == ConversationState.CONFIRMATION:
            confirmation_lines = ["Here's your optimization configuration:"]
            confirmation_lines.extend(summary_lines)
            if "enumeration_size" in optional_missing:
                confirmation_lines.append("- Enumeration size: not specified (optional)")

            if ctx.full_prompt:
                confirmation_lines.append("")
                confirmation_lines.append("Original request:")
                confirmation_lines.append(ctx.full_prompt.strip())

            confirmation_lines.append("")
            confirmation_lines.append("Does this look correct?")

            suggestions = [
                "Yes, start optimization",
                "No, I need to change something",
            ]
            if "enumeration_size" in optional_missing:
                suggestions.append("Enumerate 200 molecules")

            return "\n".join(line for line in confirmation_lines if line is not None), suggestions[:3]

        if state == ConversationState.COMPLETED:
            return (
                "Configuration confirmed! Creating your optimization run...",
                []
            )

        fallback_message = summary_section or "I'm not sure what to do next. Let's start over."
        return fallback_message, ["Start over"]

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

        prompt_sections: List[str] = []

        if ctx.full_prompt:
            prompt_sections.append("User request:\n" + ctx.full_prompt.strip())

        config_lines: List[str] = []
        molecule_desc = ctx.molecule_name or ctx.starting_molecule
        config_lines.append(
            f"Starting molecule: {molecule_desc} (SMILES: {ctx.starting_molecule})"
        )

        if ctx.targets:
            config_lines.append("Targets:")
            for target in ctx.targets:
                line = f"  - {target.mode.value} {target.name}"
                if target.mode == OptimizationMode.MATCH and target.target_value is not None:
                    line += f" (target {target.target_value})"
                config_lines.append(line)

        config_lines.append(f"Iterations: {ctx.max_iterations or 10}")
        config_lines.append(f"Batch size: {ctx.batch_size or 5}")

        if ctx.enumeration_size is not None:
            config_lines.append(f"Enumeration size: {ctx.enumeration_size}")

        if ctx.notes:
            config_lines.append(f"Notes: {ctx.notes}")

        prompt_sections.append("Structured configuration:\n" + "\n".join(config_lines))

        return "\n\n".join(prompt_sections).strip()

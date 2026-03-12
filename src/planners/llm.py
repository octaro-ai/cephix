"""LLM-backed planner that satisfies PlannerPort.

When an ``LLMPort`` is provided the planner builds a proper message
history from the planning context and calls the LLM.  Without one it
falls back to keyword matching (demo/test mode).
"""

from __future__ import annotations

import json
from typing import Any

from src.domain import (
    DeliveryDirective,
    ExecutionContext,
    MessageRecord,
    Plan,
    PlanStep,
    PlanningContext,
    RobotEvent,
    ToolResult,
)
from src.llm.models import LLMCompletion, LLMMessage, ThinkingCallback, TokenCallback
from src.llm.ports import LLMPort
from src.utils import new_id


class LLMPlanner:
    """Planner that delegates reasoning to an LLM provider.

    If *llm* is ``None`` the planner uses a built-in keyword fallback
    so the system can run without a real LLM backend.
    """

    def __init__(self, llm: LLMPort | None = None) -> None:
        self._llm = llm
        self._conversation_history: list[LLMMessage] = []

    # -- PlannerPort ---------------------------------------------------------

    def create_initial_plan(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        planning_context: PlanningContext,
        *,
        token_callback: TokenCallback | None = None,
        thinking_callback: ThinkingCallback | None = None,
    ) -> Plan:
        if self._llm is None:
            return self._keyword_initial_plan(event)

        self._conversation_history = self._build_messages(event, planning_context)
        completion = self._llm.stream_complete(
            messages=self._conversation_history,
            tools=planning_context.tool_schemas or None,
            token_callback=token_callback,
            thinking_callback=thinking_callback,
        )
        self._append_assistant_message(completion)
        return self._completion_to_plan(completion)

    def revise_plan_after_tool(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        previous_plan: Plan,
        results: list[ToolResult],
        planning_context: PlanningContext,
        *,
        token_callback: TokenCallback | None = None,
        thinking_callback: ThinkingCallback | None = None,
    ) -> Plan:
        if self._llm is None:
            # Keyword fallback expects a dict keyed by tool_name.
            results_dict = {r.tool_name: r.result for r in results}
            return self._keyword_revise_plan(planning_context, results_dict)

        # Append tool results to conversation history using the exact call_id
        # from the LLM's tool_use blocks — this correctly handles duplicate
        # tool names and preserves ordering.
        for tr in results:
            self._conversation_history.append(LLMMessage(
                role="tool",
                content=json.dumps(tr.result, default=str, ensure_ascii=False),
                tool_call_id=tr.call_id,
                name=tr.tool_name,
            ))

        completion = self._llm.stream_complete(
            messages=self._conversation_history,
            tools=planning_context.tool_schemas or None,
            token_callback=token_callback,
            thinking_callback=thinking_callback,
        )
        self._append_assistant_message(completion)
        return self._completion_to_plan(completion)

    # -- Message building ----------------------------------------------------

    @staticmethod
    def _build_messages(event: RobotEvent, planning_context: PlanningContext) -> list[LLMMessage]:
        messages: list[LLMMessage] = []

        # System prompt from firmware + memory context.
        system_parts: list[str] = []
        for doc_name, doc_content in planning_context.firmware_documents.items():
            if doc_content.strip():
                system_parts.append(f"## {doc_name}\n{doc_content.strip()}")

        for doc_name, doc_content in planning_context.memory_documents.items():
            if doc_content.strip():
                system_parts.append(f"## {doc_name}\n{doc_content.strip()}")

        memory_ctx = planning_context.memory_context
        if memory_ctx:
            core_memory = memory_ctx.get("core_memory", "")
            if core_memory:
                system_parts.append(f"## Core Memory\n{core_memory}")

            facts = memory_ctx.get("facts", [])
            if facts:
                fact_lines = [f"- [{f.get('kind', '?')}] {f.get('content', '')}" for f in facts]
                system_parts.append(f"## Known Facts\n" + "\n".join(fact_lines))

            summary = memory_ctx.get("conversation_summary", "")
            if summary:
                system_parts.append(f"## Earlier Conversation\n{summary}")

        if system_parts:
            messages.append(LLMMessage(role="system", content="\n\n".join(system_parts)))

        # Recent interactions as conversation history.
        recent = memory_ctx.get("recent_interactions", []) if memory_ctx else []
        for interaction in recent:
            messages.append(LLMMessage(role="user", content=interaction.get("user_text", "")))
            messages.append(LLMMessage(role="assistant", content=interaction.get("robot_text", "")))

        # Current user message.
        user_text = event.text or event.event_type
        messages.append(LLMMessage(role="user", content=user_text))

        return messages

    # -- Completion → Plan conversion ----------------------------------------

    @staticmethod
    def _completion_to_plan(completion: LLMCompletion) -> Plan:
        steps: list[PlanStep] = []

        if completion.tool_calls:
            for tc in completion.tool_calls:
                steps.append(PlanStep(
                    step_id=new_id("step"),
                    kind="tool_call",
                    reason=f"LLM requested tool call: {tc.name}",
                    tool_name=tc.name,
                    tool_arguments=tc.arguments,
                    tool_call_id=tc.id,
                ))
        else:
            steps.append(PlanStep(
                step_id=new_id("step"),
                kind="finalize",
                reason="LLM provided a direct response.",
                response_text=completion.content or "",
            ))

        return Plan(
            plan_id=new_id("plan"),
            goal="LLM-generated plan",
            steps=steps,
        )

    def _append_assistant_message(self, completion: LLMCompletion) -> None:
        """Record the assistant's response in conversation history.

        This is critical for multi-turn tool flows: Anthropic (and others)
        require the assistant's tool_use message to appear before
        any tool_result messages.
        """
        self._conversation_history.append(LLMMessage(
            role="assistant",
            content=completion.content,
            tool_calls=completion.tool_calls if completion.tool_calls else None,
        ))

    # -- Keyword fallback (no LLM) ------------------------------------------

    @staticmethod
    def _keyword_initial_plan(event: RobotEvent) -> Plan:
        text = (event.text or "").lower()
        job_name = str(event.payload.get("job", "")).lower()

        if "postkorb" in text or "nachrichten" in text or "mail" in text or job_name == "check_inbox":
            return Plan(
                plan_id=new_id("plan"),
                goal="Read new inbox messages and summarize them",
                steps=[
                    PlanStep(
                        step_id=new_id("step"),
                        kind="tool_call",
                        reason="The robot must first inspect new inbox items.",
                        tool_name="mail.list_new_messages",
                        tool_arguments={"limit": 10},
                    )
                ],
            )

        return Plan(
            plan_id=new_id("plan"),
            goal="Reply directly without calling a tool",
            steps=[
                PlanStep(
                    step_id=new_id("step"),
                    kind="finalize",
                    reason="The current prototype cannot fulfill the request with its mounted tools yet.",
                    response_text="Dafuer brauche ich im Prototypen gerade eine konkretere Faehigkeit.",
                )
            ],
        )

    @staticmethod
    def _keyword_revise_plan(
        planning_context: PlanningContext,
        results: dict[str, object],
    ) -> Plan:
        memory_context = planning_context.memory_context
        messages = results.get("mail.list_new_messages", [])
        assert isinstance(messages, list)

        if not messages:
            return Plan(
                plan_id=new_id("plan"),
                goal="Create final response",
                steps=[
                    PlanStep(
                        step_id=new_id("step"),
                        kind="finalize",
                        reason="No new inbox items were found.",
                        response_text="In deinem Postkorb sind aktuell keine neuen Nachrichten.",
                    )
                ],
            )

        facts = memory_context.get("facts", [])
        prefers_concise = any(
            isinstance(fact, dict) and fact.get("content") == "prefers concise answers" for fact in facts
        )

        lines = [f"Ich habe {len(messages)} neue Nachricht{'en' if len(messages) != 1 else ''} gefunden:", ""]
        for index, message in enumerate(messages, start=1):
            assert isinstance(message, MessageRecord)
            lines.append(f"{index}. {message.subject}")
            lines.append(f"   Von: {message.sender}")
            body = message.body.strip()
            summary = body if len(body) <= 95 else body[:92].rstrip() + "..."
            lines.append(f"   Kurzfassung: {summary}")
            lines.append("")

        if prefers_concise:
            lines.append("Auf Wunsch kann ich dir eine einzelne Nachricht genauer aufschluesseln.")
        else:
            lines.append("Wenn du moechtest, kann ich als Naechstes eine davon detaillierter aufschluesseln.")

        return Plan(
            plan_id=new_id("plan"),
            goal="Return the inbox summary",
            steps=[
                PlanStep(
                    step_id=new_id("step"),
                    kind="finalize",
                    reason="The inbox items were read and can now be summarized.",
                    response_text="\n".join(lines).strip(),
                )
            ],
        )

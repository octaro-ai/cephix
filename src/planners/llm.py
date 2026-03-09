from __future__ import annotations

from src.domain import ExecutionContext, MessageRecord, Plan, PlanStep, PlanningContext, RobotEvent
from src.utils import new_id


class LLMPlanner:
    """
    Simulates the LLM as a planner/reasoner node.

    It decides on the next meaningful step, but it never executes
    tools directly. That responsibility stays inside the kernel.
    """

    def create_initial_plan(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        planning_context: PlanningContext,
    ) -> Plan:
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

    def revise_plan_after_tool(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        previous_plan: Plan,
        results: dict[str, object],
        planning_context: PlanningContext,
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
            lines.append(f"   Kurzfassung: {self._summarize_message(message)}")
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

    @staticmethod
    def _summarize_message(message: MessageRecord) -> str:
        body = message.body.strip()
        if len(body) <= 95:
            return body
        return body[:92].rstrip() + "..."

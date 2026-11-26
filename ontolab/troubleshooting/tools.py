"""Tools the troubleshooting agent uses on the diagram-based ontology: five read the
knowledge, six read or write the case."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..tools.generic import NoArgs, _out
from ..tools.registry import Capability, ToolRegistry, ToolSpec
from .engine import TroubleshootingEngine


class DescriptionArgs(BaseModel):
    description: str = Field(description="The customer's own words, unchanged.")


class SymptomArgs(BaseModel):
    symptom_id: str = Field(description="A symptom id from match_symptoms or list_symptoms.")


class CaseArgs(BaseModel):
    case_id: str = Field(description="The id of the case being worked on.")


class CheckResultArgs(BaseModel):
    case_id: str
    check_id: str = Field(description="The check definition that was performed.")
    result: str = Field(description="One of that check's result codes.")
    evidence: str = Field(description="What the customer said or observed, as close to verbatim as possible.")
    steps_skipped_because: str | None = Field(default=None, description=(
        "Leave empty normally. Fill in when steps without feedback are not needed: the customer already gave the answer "
        "unprompted, or an earlier reply settles the check. Say why. Those steps are stored as skipped with this reason."))


class StepFeedbackArgs(BaseModel):
    case_id: str
    check_id: str = Field(description="The check the customer is being led through.")
    step: int = Field(description="The number of the step the customer just replied to.")
    feedback: str = Field(description="What the customer said in reply to that step, as close to verbatim as possible.")


class Candidate(BaseModel):
    symptom_id: str
    verdict: Literal["candidate", "supported", "ruled_out", "confirmed"]
    reason: str = Field(default="", description="Why this verdict, in one short sentence.")


class AssessmentArgs(BaseModel):
    case_id: str
    candidates: list[Candidate] = Field(description="Every symptom under consideration, each with a verdict. Include ruled-out ones.")
    evidence: str = Field(description="One sentence stating what this judgement rests on.")


class SelectSolutionArgs(BaseModel):
    case_id: str
    solution_id: str = Field(description="A solution id listed as applicable by get_applicable_solutions.")
    outcome: Literal["resolved", "not_resolved", "awaiting_customer"] = Field(
        default="awaiting_customer", description="'resolved' closes the case; use it once the customer confirms the fix worked.")


class EscalateArgs(BaseModel):
    case_id: str
    reason: str = Field(description="Why the case cannot be resolved here.")


TOOL_NAMES = ("match_symptoms", "list_symptoms", "describe_symptom", "get_case", "suggest_next_check", "record_step_feedback",
              "record_check_result",
              "record_assessment", "get_applicable_solutions", "select_solution", "escalate_case")


def register_troubleshooting_tools(registry: ToolRegistry, engine: TroubleshootingEngine) -> None:
    read = Capability(operations=("resolve_term", "object_details", "neighbor_lookup"))
    decide = Capability(operations=("conditional_branching", "rule_evaluation"))
    write = Capability()
    specs = [
        ToolSpec("match_symptoms",
                 "Map the customer's description onto standard symptoms. Returns candidates, how each matched (name, typical "
                 "expression or vague expression) and description_quality: specific, vague or unrecognised. Call this first.",
                 DescriptionArgs, _out(description_quality={"type": "string"}, candidates={"type": "array"}),
                 lambda a: engine.match_symptoms(a.description), read, "troubleshooting"),
        ToolSpec("list_symptoms", "List every standard symptom with its definition. Use when match_symptoms recognises nothing.",
                 NoArgs, _out(symptoms={"type": "array"}), lambda a: engine.list_symptoms(), read, "troubleshooting"),
        ToolSpec("describe_symptom",
                 "One symptom in full: definition, typical and vague expressions, the distinguishing conditions that confirm it "
                 "(each a check and the result it must have), and its checklists in working order: triage, then locate_cause, "
                 "then deeper. A checklist may apply only to some devices.",
                 SymptomArgs, _out(distinguishing_conditions={"type": "array"}, checklists={"type": "array"}),
                 lambda a: engine.describe_symptom(a.symptom_id), read, "troubleshooting"),
        ToolSpec("get_case",
                 "The case as recorded so far: original description, current candidates and how their conditions stand, every "
                 "assessment, every check result, and the selected solution.",
                 CaseArgs, _out(current_candidates={"type": "array"}, assessments={"type": "array"}, check_results={"type": "array"}),
                 lambda a: engine.case_state(a.case_id), read, "troubleshooting"),
        ToolSpec("suggest_next_check",
                 "Which check to do next, given the current candidates and what is already recorded: the one that rules out the "
                 "most candidates, or once a symptom is confirmed, the one that decides the solution. Each check comes with its spoken steps and "
                 "which of them still need the customer's reply, plus what each final result would mean and alternative checks. "
                 "The ranking and the step order are advice: you decide the order. If a check is under way it returns that check.",
                 CaseArgs, _out(suggestion={"type": ["object", "null"]}, if_the_result_is={"type": "array"}, reason={"type": "string"}),
                 lambda a: engine.suggest_next_check(a.case_id), decide, "troubleshooting"),
        ToolSpec("record_step_feedback",
                 "Store the customer's reply to the step you just gave them. Returns the check's steps with the ones still "
                 "pending, so you can choose which to give next. Steps can be taken in any order, one per customer reply. "
                 "When none are pending it lists the result codes.",
                 StepFeedbackArgs, _out(recorded={"type": "object"}, pending_steps={"type": "array"}, next={"type": "string"}),
                 lambda a: engine.record_step_feedback(a.case_id, a.check_id, a.step, a.feedback), write, "troubleshooting"),
        ToolSpec("record_check_result",
                 "Record the outcome of a check. The result must be one of the check's result codes. It is rejected while steps "
                 "lack customer feedback, unless you give steps_skipped_because. Then record a new assessment.",
                 CheckResultArgs, _out(recorded={"type": "object"}, effect_on_candidates={"type": "array"}),
                 lambda a: engine.record_check_result(a.case_id, a.check_id, a.result, a.evidence, a.steps_skipped_because),
                 write, "troubleshooting"),
        ToolSpec("record_assessment",
                 "Record your current judgement of which symptom this is. It replaces the previous assessment, which is kept. "
                 "A symptom can be 'confirmed' only when all its distinguishing conditions are met by recorded check results; "
                 "otherwise the call is rejected and tells you what is missing.",
                 AssessmentArgs, _out(recorded={"type": "object"}, warnings={"type": "array"}, confirmed_symptom={"type": ["object", "null"]}),
                 lambda a: engine.record_assessment(a.case_id, [c.model_dump() for c in a.candidates], a.evidence), write, "troubleshooting"),
        ToolSpec("get_applicable_solutions",
                 "For the confirmed symptom, which solutions apply given the recorded check results, which are still undetermined "
                 "(and which checks would settle them), and which do not apply.",
                 CaseArgs, _out(applicable={"type": "array"}, undetermined={"type": "array"}, not_applicable={"type": "array"}),
                 lambda a: engine.applicable_solutions(a.case_id), decide, "troubleshooting"),
        ToolSpec("select_solution",
                 "Record the solution chosen for the case. Only a solution currently listed as applicable is accepted.",
                 SelectSolutionArgs, _out(selected_solution={"type": "object"}, status={"type": "string"}),
                 lambda a: engine.select_solution(a.case_id, a.solution_id, a.outcome), write, "troubleshooting"),
        ToolSpec("escalate_case",
                 "Hand the case to a human technician when no stored solution fits or the customer cannot do the checks. "
                 "Returns a handover summary.",
                 EscalateArgs, _out(escalated={"type": "boolean"}, handover_summary={"type": "object"}),
                 lambda a: engine.escalate(a.case_id, a.reason), write, "troubleshooting"),
    ]
    for spec in specs:
        registry.register(spec)

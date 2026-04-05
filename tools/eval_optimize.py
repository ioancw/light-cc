"""Evaluator-Optimizer tool — iterative refinement via generator + evaluator subagents."""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.registry import register_tool
from tools.subagent import run_subagent

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 5


async def handle_eval_optimize(tool_input: dict[str, Any]) -> str:
    """Run an evaluator-optimizer loop: generate, evaluate, refine, repeat."""

    task = tool_input.get("task", "")
    criteria = tool_input.get("criteria", "")
    max_iterations = tool_input.get("max_iterations", DEFAULT_MAX_ITERATIONS)
    generator_tools = tool_input.get("generator_tools")
    evaluator_tools = tool_input.get("evaluator_tools")

    if not task:
        return json.dumps({"error": "No task provided"})
    if not criteria:
        return json.dumps({"error": "No evaluation criteria provided"})

    max_iterations = min(max_iterations, 10)  # Hard cap

    # -- Iteration loop --
    current_output = None
    history: list[dict[str, str]] = []

    for iteration in range(1, max_iterations + 1):
        # --- Generator phase ---
        if current_output is None:
            gen_prompt = (
                f"Complete the following task:\n\n{task}\n\n"
                f"The output will be evaluated against these criteria:\n{criteria}\n\n"
                f"Produce your best output."
            )
        else:
            gen_prompt = (
                f"Original task:\n\n{task}\n\n"
                f"Your previous output:\n\n{current_output}\n\n"
                f"Evaluator feedback:\n\n{history[-1]['feedback']}\n\n"
                f"Revise your output to address the feedback. "
                f"The evaluation criteria are:\n{criteria}"
            )

        current_output, _ = await run_subagent(
            prompt=gen_prompt,
            system=(
                "You are a generator agent. Your job is to produce high-quality output "
                "for the given task. If feedback from a prior evaluation is provided, "
                "use it to improve your output. Return ONLY the refined output, "
                "no commentary about what you changed."
            ),
            tool_names=generator_tools,
            max_turns=15,
            max_result_chars=30000,
        )

        # --- Evaluator phase ---
        eval_prompt = (
            f"Task that was given:\n\n{task}\n\n"
            f"Output to evaluate:\n\n{current_output}\n\n"
            f"Evaluation criteria:\n{criteria}\n\n"
            f"Evaluate the output against each criterion. Respond in this exact JSON format:\n"
            f'{{"pass": true/false, "score": 1-10, "feedback": "specific actionable feedback"}}\n\n'
            f"Set pass=true ONLY if all criteria are adequately met (score >= 7). "
            f"If pass=false, the feedback MUST explain exactly what to fix."
        )

        eval_result, _ = await run_subagent(
            prompt=eval_prompt,
            system=(
                "You are an evaluator agent. Assess the output strictly against the criteria. "
                "Be specific and actionable in feedback. Respond ONLY with the requested JSON."
            ),
            tool_names=evaluator_tools,
            max_turns=5,
            max_result_chars=5000,
        )

        # Parse evaluator response
        passed = False
        score = 0
        feedback = eval_result

        try:
            # Extract JSON from response (evaluator might wrap it in markdown)
            json_str = eval_result
            if "```" in json_str:
                json_str = json_str.split("```")[1]
                if json_str.startswith("json"):
                    json_str = json_str[4:]
            parsed = json.loads(json_str.strip())
            passed = parsed.get("pass", False)
            score = parsed.get("score", 0)
            feedback = parsed.get("feedback", eval_result)
        except (json.JSONDecodeError, IndexError):
            # If evaluator didn't return valid JSON, treat as not passed
            feedback = eval_result

        history.append({
            "iteration": str(iteration),
            "score": str(score),
            "passed": str(passed),
            "feedback": feedback,
        })

        logger.info(f"EvalOptimize iteration {iteration}: score={score}, passed={passed}")

        if passed:
            return json.dumps({
                "status": "passed",
                "iterations": iteration,
                "final_score": score,
                "output": current_output,
                "history": history,
            })

    # Max iterations reached without passing
    return json.dumps({
        "status": "max_iterations",
        "iterations": max_iterations,
        "final_score": score,
        "output": current_output,
        "history": history,
    })


register_tool(
    name="EvalOptimize",
    aliases=["eval_optimize", "evaluator_optimizer"],
    description=(
        "Iteratively refine output using a generator-evaluator loop. "
        "A generator subagent produces output, an evaluator scores it against criteria, "
        "and the generator revises based on feedback until the criteria pass or max iterations are reached."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task to complete (given to the generator)",
            },
            "criteria": {
                "type": "string",
                "description": "Evaluation criteria the output must satisfy (given to the evaluator)",
            },
            "max_iterations": {
                "type": "integer",
                "description": "Maximum generate-evaluate cycles (default 5, hard cap 10)",
            },
            "generator_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tool names available to the generator (default: all tools)",
            },
            "evaluator_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tool names available to the evaluator (default: all tools)",
            },
        },
        "required": ["task", "criteria"],
    },
    handler=handle_eval_optimize,
)

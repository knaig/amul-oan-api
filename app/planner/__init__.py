"""Jev (TypeSafe System One) planner: replaces the agent's first LLM request.

Legacy turn = LLM request #1 (choose tools + fill args) -> tools -> LLM request #2
(compose). This package makes request #1 a single Jev evaluation: closed-set
typed questions over the turn state, decoded in code into an executable plan.
Request #2 (compose) stays generative and becomes the ONLY generative call.
"""

"""Agent layer — one async function per role.

All LLM-backed agents share `_llm.run_json()` to call Claude via the Agent SDK
(subscription auth) and parse a JSON response into a Pydantic model.
"""

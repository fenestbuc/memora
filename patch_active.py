file_path = "/home/yash/.hermes/plugins/memora/__init__.py"
with open(file_path, "r") as f:
    content = f.read()

old_prompt = """    def system_prompt_block(self) -> str:
        return (
            "You have access to a persistent long-term memory via the memora_* tools. "
            "Use memora_search to recall past context before answering. "
            "After learning something important, use memora_add to persist it."
        )"""

new_prompt = """    def system_prompt_block(self) -> str:
        return (
            "You have access to a persistent long-term memory via the memora_* tools. "
            "Use memora_search to recall past context before answering. "
            "After learning something important, DO NOT use the default memory tool — ALWAYS use memora_add to persist it directly to the RAG backend. "
            "When using memora_add, you MUST explicitly categorize the fact using precise tags (e.g., projects, strategy, business, integrations, user) rather than dumping it into the default 'memory' bucket."
        )"""

content = content.replace(old_prompt, new_prompt)

old_tool = """"category": {"type": "string", "description": "Category tag (default: memory)."},"""
new_tool = """"category": {"type": "string", "description": "Category tag (e.g., projects, strategy, business, integrations, user). MUST be specific, avoid the default 'memory' bucket."},"""

content = content.replace(old_tool, new_tool)

with open(file_path, "w") as f:
    f.write(content)
print("Patched __init__.py successfully")

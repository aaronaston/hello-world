"""Minimal OpenAI Agents SDK example: an agent with one function tool.

Run: export OPENAI_API_KEY=...; python hello_agent.py
Docs: https://openai.github.io/openai-agents-python/
"""
from agents import Agent, Runner, function_tool


@function_tool
def get_weather(city: str) -> str:
    """Return a (fake) weather report for a city."""
    return f"The weather in {city} is sunny."


agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant. Use tools when useful.",
    tools=[get_weather],
)

if __name__ == "__main__":
    result = Runner.run_sync(agent, "What's the weather in Paris?")
    print(result.final_output)

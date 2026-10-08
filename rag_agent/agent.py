"""ADK CLI entrypoint for routing-only evaluation, not the full guarded pipeline."""

from rag_agent.agents import create_agents
from rag_agent.settings import get_settings

root_agent = create_agents(get_settings())["router"]

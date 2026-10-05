import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_groq import ChatGroq

from agents.mes_tools import check_spc_tool

load_dotenv()

llm = ChatGroq(model=os.environ["GROQ_MODEL"], temperature=0)
agent = create_agent(
    llm,
    tools=[check_spc_tool],
    system_prompt="You are a manufacturing fault analyst. Use tools to check "
                  "station data before answering. Never guess.",
)

result = agent.invoke({"messages": [
    {"role": "user",
     "content": "At 2026-10-04T10:25:00Z, is station ST-040 torque_nm behaving normally?"}
]})
for m in result["messages"]:
    print(type(m).__name__, ":", m.content, getattr(m, "tool_calls", ""))
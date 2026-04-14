from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.messages import SystemMessage, AIMessage
from invoice_tools import invoice_tools

# RATE LIMITER ADDED
# Respects the free tier constraints (approx 1 request per 12 seconds).
rate_limiter = InMemoryRateLimiter(
    requests_per_second=0.0833, 
    check_every_n_seconds=0.1,
    max_bucket_size=1
)

# INITIALIZE LLM WITH RATE LIMITER
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    temperature=0, # Keep at 0 so it extracts exact numbers, not creative ones
    rate_limiter=rate_limiter
)

llm_with_tools = llm.bind_tools(invoice_tools)

# SYSTEM INSTRUCTIONS FOR CONVERSATIONAL PERMISSION
system_instruction = SystemMessage(content="""
You are an automated, highly precise Invoice Processing Agent.
Your workflow is STRICTLY as follows:
1. Read the PDF document.
2. Analyze the raw text and extract the data. You MUST output your findings. You must include every single field.
3. CRITICAL RULE: Present the report above to the user in the chat FIRST. Ask: "Does this look correct? May I save this to a file?" DO NOT use the save tool yet.
4. If the user asks for corrections, fix the data and show the report again and keep doing till the user is satisfied.
5. ONLY AFTER the user confirms, save the report to a text file. Ensure you name the text filename is based on the user's request.
""")

def call_model(state: MessagesState):
    """Invokes the language model with the system prompt and conversation history."""
    messages_to_pass = [system_instruction] + state['messages']
    
    try:
        response = llm_with_tools.invoke(messages_to_pass)
        return {"messages": [response]}
    except Exception as e:
        error_msg = f"System Error: Details: {str(e)}"
        print(f"\n[ERROR] {error_msg}")
        return {"messages": [AIMessage(content=error_msg)]}

# RECURSION LIMIT LOGIC
def should_continue(state: MessagesState):
    """Routes the graph and counts recent tool calls to prevent infinite loops."""
    messages = state['messages']
    loop_count = 0
    
    # Counts the number of tool invocations since the last human intervention
    for msg in reversed(messages):
        if msg.type == "human":
            break 
        if msg.type == "ai" and msg.tool_calls:
            loop_count += 1
            
    # Halts execution if the loop threshold is breached
    if loop_count >= 10:
        print("\n[SYSTEM] Safety limit reached. Stopping execution to protect resources.")
        return END

    # Routes to the tools node if the AI requested an action
    last_message = messages[-1]
    if last_message.tool_calls:
        return "tools"
        
    return END

# Build the workflow
workflow = StateGraph(MessagesState)
workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode(invoice_tools))

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, ["tools", END])
workflow.add_edge("tools", "agent")

# Compile the graph
graph = workflow.compile()
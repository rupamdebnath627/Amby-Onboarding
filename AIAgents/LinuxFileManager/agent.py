from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.messages import SystemMessage, AIMessage
from tools import file_tools

# Initializes a rate limiter to respect the API constraints (5 requests per minute).
rate_limiter = InMemoryRateLimiter(
    requests_per_second=0.0833, 
    check_every_n_seconds=0.1,
    max_bucket_size=1
)

# Initializes the Google Generative AI model with the specified rate limiter.
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    temperature=0,
    rate_limiter=rate_limiter
)

# Binds the predefined file management tools to the language model.
llm_with_tools = llm.bind_tools(file_tools)

# Defines the core security rules and operational instructions for the AI.
system_instruction = SystemMessage(content="""
You are a highly secure Linux File Manager AI. 
CRITICAL RULE: You may use 'list_directory', 'read_file', and 'fast_indexed_search' autonomously to find information. 
However, you MUST NEVER use 'delete_item', 'write_file', 'move_item', or 'create_folder' without explicitly asking the user for confirmation in the chat first. 
If the user asks you to delete something, find it first, tell them exactly what you found, and wait for them to say 'yes' before executing the tool.
""")

def call_model(state: MessagesState):
    """
    Invokes the language model with the current conversation state and system instructions.
    Handles potential API or network failures gracefully.
    """
    messages_to_pass = [system_instruction] + state['messages']
    
    try:
        response = llm_with_tools.invoke(messages_to_pass)
        return {"messages": [response]}
    except Exception as e:
        # Returns a safe AI message to prevent the graph from crashing during an API failure.
        error_msg = f"System Error: Unable to communicate with the AI model. Details: {str(e)}"
        print(f"\n[ERROR] {error_msg}")
        return {"messages": [AIMessage(content=error_msg)]}

def should_continue(state: MessagesState):
    """
    Determines the next node in the graph. Counts recent tool calls to prevent infinite loops.
    """
    messages = state['messages']
    loop_count = 0
    
    # Counts the number of tool invocations since the last human intervention.
    for msg in reversed(messages):
        if msg.type == "human":
            break 
        if msg.type == "ai" and msg.tool_calls:
            loop_count += 1
            
    # Halts execution if the loop threshold is breached.
    if loop_count >= 10:
        print("\n[SYSTEM] Safety limit reached. Stopping execution to protect resources.")
        return END

    # Routes to the tools node if the AI requested an action.
    last_message = messages[-1]
    if last_message.tool_calls:
        return "tools"
        
    return END

# Constructs the state graph and connects the nodes.
workflow = StateGraph(MessagesState)
workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode(file_tools))

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, ["tools", END])
workflow.add_edge("tools", "agent")

# Compiles the workflow with a strict interrupt before executing any tool.
graph = workflow.compile(interrupt_before=["tools"])
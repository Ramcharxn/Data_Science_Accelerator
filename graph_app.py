from typing import List, Tuple, Annotated, TypedDict
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph.message import add_messages
from langchain_groq import ChatGroq
from langgraph.checkpoint.mongodb import MongoDBSaver
from langchain_core.messages import SystemMessage
from langchain_mongodb import MongoDBAtlasVectorSearch
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
from langgraph.prebuilt import ToolNode, tools_condition
from langchain.tools import tool
from pymongo import MongoClient
import os
from dotenv import load_dotenv


load_dotenv()


class State(TypedDict):
    messages: Annotated[list, add_messages] 


MONGO_URI = os.getenv("MONGO_URI")

client = MongoClient(MONGO_URI)
db = client["DSLA_db"]
collection = db["doc_embedding"]


checkpointer = MongoDBSaver(
    client,
    db_name="DSLA_db",        
    collection_name="chat_checkpoints"
)

def build_graph():

    embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/paraphrase-MiniLM-L3-v2")

    vector_store = MongoDBAtlasVectorSearch(
        # documents=chunks,
        embedding=embedding,
        collection=collection,
        index_name="doc_vector_index",
        embedding_key="embedding",
        text_key="page_content",
    )

    retriever = vector_store.as_retriever(search_kwargs={"k": 4})


    @tool
    def langchain_info(query: str) -> str:
        """
        Retrieve information from my Data Science knowledge base.

        Use this tool whenever the user asks a question about:
        - Data Science
        - Machine Learning / Deep Learning
        - Statistics or Probability
        - Computer Science
        - Data Strcuture and Algorithm
        - Large Language Models (LLMs)
        - Python libraries commonly used for these (NumPy, pandas, scikit-learn, etc.)
        - Any topic that is likely covered in the indexed PDFs / web articles

        The `query` argument should be a short natural-language search query
        that captures the user's question or topic (you can reuse the user's
        question directly).

        If the user's question is not related to these topics, do NOT call this
        tool.
        """
        docs: list[Document] = retriever.invoke(query)
        joined = "\n\n".join(d.page_content for d in docs)
        return joined


    tools = [langchain_info]
    llm = ChatGroq(model='openai/gpt-oss-120b')
    llm_with_tools = llm.bind_tools(tools)

    system_prompt = """
    You are a data science assistant.

    You MUST use the `langchain_info` tool for ANY question that is:
    - About data science, machine learning, deep learning, statistics, probability, Computer Science, Data Strcuture and Algorithm
    - About Python libraries used for data science (NumPy, pandas, scikit-learn, etc.),
    - Or likely covered in the indexed documents.

    You are NOT allowed to answer those questions from your own training data
    without first calling the tool.

    For questions clearly outside these topics, do NOT call the tool; instead briefly explain that the
    question is out of scope.

    Always give short, concise answers. Only provide more detail when explicitly asked, and even then keep the explanation brief and not overly long.
    Dont keep the answers more that 2 line undless asked to explain in detailed, and not more that 10 lines in worst case

    Try to avoid giving answer in tables, unless asked by user specifically to give in tabular format
    """


    def ChatBot(state: State):
        history = state["messages"]
        max_history_messages = 6  # tweak as needed

        # only keep the most recent messages
        recent = history[-max_history_messages:]

        msgs = [SystemMessage(content=system_prompt)] + recent

        return {"messages": llm_with_tools.invoke(msgs)}


    # 2) Build graph
    graph_builder = StateGraph(State)

    graph_builder.add_node("chatbot", ChatBot)
    graph_builder.add_node("tools", ToolNode(tools))

    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "chatbot")
    graph_builder.add_edge("chatbot", END)

    graph = graph_builder.compile(checkpointer=checkpointer)

    return graph


graph_instance = None  # cache

def get_graph():
    global graph_instance
    if graph_instance is None:
        graph_instance = build_graph()
    return graph_instance


def get_chat_history(graph, thread_id: str) -> List[Tuple[str, str]]:
    config = {"configurable": {"thread_id": thread_id}}

    # This pulls the latest state for that thread from MongoDB
    snapshot = graph.get_state(config)

    messages = snapshot.values.get("messages", [])
    history: List[Tuple[str, str]] = []

    for m in messages:
        # LangChain message objects have .type ("human", "ai", "system", "tool", ...)
        role = getattr(m, "type", None)
        if role in ("human", "ai"):
            history.append((role, m.content))

    return history

# This is what we’ll import in the API server:
graph = build_graph()

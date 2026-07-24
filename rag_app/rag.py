import os
from langchain_ollama import OllamaLLM, OllamaEmbeddings
from langchain_chroma import Chroma
from system_prompt import build_prompt

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "llama3.2:latest")
CHROMA_DIR = os.getenv("CHROMA_DIR", "/data/chroma")
COLLECTION = os.getenv("CHROMA_COLLECTION", "acme_docs")

_embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)
_vectorstore = Chroma(
    collection_name=COLLECTION,
    embedding_function=_embeddings,
    persist_directory=CHROMA_DIR,
)
_llm = OllamaLLM(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.2)


def retrieve(question: str, k: int = 4):
    docs = _vectorstore.similarity_search(question, k=k)
    return docs


def answer(question: str, k: int = 4):
    docs = retrieve(question, k=k)
    context = "\n\n---\n\n".join(
        f"[source: {d.metadata.get('source', 'unknown')}]\n{d.page_content}" for d in docs
    )
    prompt = build_prompt(context, question)
    response = _llm.invoke(prompt)
    return {
        "answer": response,
        "sources": [d.metadata.get("source", "unknown") for d in docs],
        "prompt_sent": prompt,
    }

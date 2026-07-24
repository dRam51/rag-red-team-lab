import os
import glob
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "llama3.2:latest")
CHROMA_DIR = os.getenv("CHROMA_DIR", "/data/chroma")
COLLECTION = os.getenv("CHROMA_COLLECTION", "acme_docs")
DOCS_DIR = os.getenv("DOCS_DIR", "/data/documents")


def main():
    files = sorted(glob.glob(os.path.join(DOCS_DIR, "*.md")))
    print(f"Found {len(files)} documents in {DOCS_DIR}")

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    texts, metadatas = [], []
    for path in files:
        with open(path) as f:
            content = f.read()
        chunks = splitter.split_text(content)
        source = os.path.basename(path)
        texts.extend(chunks)
        metadatas.extend([{"source": source} for _ in chunks])
        print(f"  {source}: {len(chunks)} chunks")

    embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)
    store = Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=CHROMA_DIR,
    )

    existing = store._collection.count()
    if existing > 0:
        print(f"Collection has {existing} existing docs; deleting and re-indexing.")
        store.delete_collection()
        store = Chroma(
            collection_name=COLLECTION,
            embedding_function=embeddings,
            persist_directory=CHROMA_DIR,
        )

    print(f"Embedding {len(texts)} chunks with {EMBED_MODEL}...")
    store.add_texts(texts=texts, metadatas=metadatas)
    print(f"Done. Collection now has {store._collection.count()} chunks.")


if __name__ == "__main__":
    main()

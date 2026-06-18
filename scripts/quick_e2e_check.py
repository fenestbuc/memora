import json, os, sys, urllib.request, urllib.error, uuid

url = os.environ["RAG_WORKER_URL"]
token = os.environ["RAG_AUTH_TOKEN"]

run_id = str(uuid.uuid4())[:8]
fact_id = f"e2e-test-ragroundtrip-{run_id}"
content = f"The NavDhan E2E roundtrip test fact identifier is {fact_id}. It is used to validate live RAG endpoints."

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{url}{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "memora-e2e/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"HTTP {e.code} on {path}: {body_text}", file=sys.stderr)
        raise

def get(path):
    req = urllib.request.Request(f"{url}{path}", headers={"Authorization": f"Bearer {token}", "User-Agent": "memora-e2e/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

try:
    health = get("/health")
    print("health model", health["models"])

    embed, _ = post("/embed", {"text": [content]})
    assert embed["count"] == 1
    assert len(embed["embeddings"][0]) > 0
    print("embed OK dim", len(embed["embeddings"][0]))

    add, _ = post("/memory/add", {"id": fact_id, "category": "e2e_test", "content": content, "scope": "company"})
    assert add["success"]
    print("memory/add OK", add)

    search, _ = post("/search", {"query": f"NavDhan E2E roundtrip test fact identifier {fact_id}", "top_k": 5, "rerank": False, "scope": "company"})
    if not [r for r in search["results"] if r.get("id") == fact_id]:
        import time
        time.sleep(5)
        search, _ = post("/search", {"query": f"NavDhan E2E roundtrip test fact identifier {fact_id}", "top_k": 5, "rerank": False, "scope": "company"})
    found = [r for r in search["results"] if r.get("id") == fact_id]
    assert found, f"expected fact {fact_id} in search results: {search}"
    print("search OK found", len(found))

    chat, _ = post("/chat", {"query": f"What is the NavDhan E2E roundtrip test fact identifier {fact_id}?", "top_k": 3, "rerank": False})
    assert "answer" in chat and fact_id in chat["answer"]
    assert any(s.get("id") == fact_id for s in chat["sources"])
    print("chat OK answer contains id", chat["answer"][:120])

    delete, _ = post("/delete", {"ids": [fact_id]})
    assert delete["success"] and delete["deleted"] == 1
    print("delete OK", delete)

    # confirm removed via search (allow short propagation)
    search2, _ = post("/search", {"query": f"{fact_id}", "top_k": 10, "rerank": False, "scope": "company"})
    leftover = [r for r in search2["results"] if r.get("id") == fact_id]
    print("post-delete leftover search", len(leftover))
finally:
    try:
        post("/delete", {"ids": [fact_id]})
    except Exception as e:
        print("cleanup delete failed:", e, file=sys.stderr)

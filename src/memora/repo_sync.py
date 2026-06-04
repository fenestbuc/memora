import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Any
import sys

from .plugin import MemoraProvider
from .wiki_builder import build_wiki

def sync_repo(repo_dir: str):
    print(f"Starting Memora Company Repo Sync into {repo_dir}...")
    repo_path = Path(repo_dir)
    facts_dir = repo_path / "facts"
    facts_dir.mkdir(exist_ok=True, parents=True)
    
    p = MemoraProvider()
    p.initialize('repo_sync')
    
    if not p.is_available():
        print("Memora provider not available. Cannot fetch from RAG.")
        print("Skipping RAG fetch, building wiki from existing local JSONL...")
    else:
        print("Fetching facts from RAG worker...")
        facts_by_category = defaultdict(list)
        limit = 1000
        offset = 0
        
        while True:
            try:
                res_json = p.handle_tool_call("memora_list", {"limit": limit, "offset": offset})
                res = json.loads(res_json)
            except Exception as e:
                print(f"Error fetching page: {e}")
                break
                
            if "error" in res:
                print(f"API Error: {res['error']}")
                break
                
            facts = res.get("facts", [])
            if not facts:
                break
                
            for fact in facts:
                cat = fact.get("category", "memory")
                facts_by_category[cat].append(fact)
                
            offset += limit

        if facts_by_category:
            print(f"Writing {sum(len(v) for v in facts_by_category.values())} facts to JSONL...")
            for cat, items in facts_by_category.items():
                if cat in ("eval_golden", "test"):
                    continue
                    
                file_path = facts_dir / f"{cat}.jsonl"
                with open(file_path, "w", encoding="utf-8") as f:
                    for item in items:
                        export_item = {
                            "id": item.get("id"),
                            "content": item.get("content"),
                            "category": item.get("category"),
                            "created_at": item.get("created_at"),
                        }
                        f.write(json.dumps(export_item) + "\n")
            print("JSONL facts updated.")
        else:
            print("No new facts retrieved from RAG.")
    
    # Run Wiki Builder
    print("Building comprehensive Markdown Wiki...")
    build_wiki(repo_dir)
    
    # Commit and Push
    print("Committing to repository...")
    try:
        subprocess.run(["git", "add", "facts/", "wiki/", "README.md"], cwd=repo_dir, check=True)
        # Check if there are changes
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
        if status.stdout.strip():
            subprocess.run(["git", "commit", "-m", "chore(memora): auto-sync facts and compile comprehensive wiki"], cwd=repo_dir, check=True)
            try:
                subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir, check=True)
                print("Successfully pushed comprehensive wiki to company memory repo.")
            except subprocess.CalledProcessError:
                print("Could not push to remote. You may need to run `git push` manually.")
        else:
            print("No new changes to commit.")
    except subprocess.CalledProcessError as e:
        print(f"Git operation failed: {e}")

def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "hermes-workspace" / "kubarlabs-memory")
    sync_repo(repo)

if __name__ == "__main__":
    main()

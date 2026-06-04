import json
import os
from pathlib import Path
from collections import defaultdict
from typing import Dict, List

def build_wiki(repo_dir: str):
    """
    Reads all .jsonl facts in repo_dir/facts/ and generates 
    a comprehensive Markdown wiki in repo_dir/wiki/.
    """
    repo_path = Path(repo_dir)
    facts_dir = repo_path / "facts"
    wiki_dir = repo_path / "wiki"
    wiki_dir.mkdir(exist_ok=True, parents=True)
    
    if not facts_dir.exists():
        print(f"Facts directory {facts_dir} not found.")
        return

    # Categorized facts
    facts_by_category: Dict[str, List[dict]] = defaultdict(list)
    
    for jsonl_file in facts_dir.glob("*.jsonl"):
        cat = jsonl_file.stem
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        facts_by_category[cat].append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
                        
    # Sort facts chronologically if created_at exists, else leave as is
    for cat in facts_by_category:
        facts_by_category[cat].sort(key=lambda x: x.get("created_at", ""))

    # Generate Markdown for each category
    index_links = []
    
    for cat, facts in facts_by_category.items():
        if not facts:
            continue
            
        md_file = wiki_dir / f"{cat}.md"
        title = cat.replace("_", " ").title()
        
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n")
            f.write(f"*Auto-generated comprehensive memory for {title}.*\n\n")
            
            for fact in facts:
                content = fact.get("content", "").strip()
                date_str = fact.get("created_at", "")[:10]
                source = fact.get("source", "system")
                
                if date_str:
                    f.write(f"### {date_str} (Source: {source})\n")
                else:
                    f.write(f"### Source: {source}\n")
                    
                f.write(f"{content}\n\n---\n\n")
                
        index_links.append(f"- [{title}](wiki/{cat}.md) ({len(facts)} facts)")

    # Update or Create README.md at repo root
    readme_path = repo_path / "README.md"
    readme_content = "# Kubar Labs Comprehensive Brain\n\n"
    readme_content += "This repository serves as the central, synced brain for Kubar Labs.\n\n"
    readme_content += "## Wiki Index\n\n"
    readme_content += "\n".join(sorted(index_links)) + "\n\n"
    readme_content += "## How it works\n\n"
    readme_content += "Facts are continuously synchronized as `.jsonl` files in `facts/` by the Memora daemon, to prevent git conflicts. "
    readme_content += "The `wiki_builder.py` script automatically compiles these facts into the human-readable Markdown files found in `wiki/`."
    
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)
        
    print(f"Wiki generated successfully in {wiki_dir}")

if __name__ == "__main__":
    import sys
    repo = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "hermes-workspace" / "kubarlabs-memory")
    build_wiki(repo)

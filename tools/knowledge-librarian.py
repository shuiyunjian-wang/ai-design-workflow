import os
import re
import json
import yaml
from pathlib import Path

class KnowledgeLibrarian:
    def __init__(self, base_path: str):
        self.base_path = Path(base_path).resolve()
        # ① KB 迁 sheji 后，案例库位于 knowledge-base/case-studies（对齐总办规范）；
        # 同时收录各智能体分库 knowledge-base/agents/<id>/cases
        self.cases_roots = [
            self.base_path / "knowledge-base" / "case-studies",
            self.base_path / "knowledge-base" / "cases",  # 兼容：迁移残留
        ]
        self.agents_root = self.base_path / "knowledge-base" / "agents"
        self.index_file = self.base_path / "knowledge-base" / "knowledge-index.json"
        
        print(f"DEBUG: Base Path: {self.base_path}")
        print(f"DEBUG: Index File: {self.index_file}")

    def extract_metadata(self, file_path: Path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"DEBUG: Skip unreadable {file_path}: {e}")
            return None
        
        match = re.search(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        metadata = {}
        if match:
            try:
                metadata = yaml.safe_load(match.group(1))
            except Exception as e:
                print(f"DEBUG: Error parsing YAML in {file_path}: {e}")
        
        design_lang = ""
        design_match = re.search(r'## 1\. 核心视觉特征.*?\n(.*?)\n##', content, re.DOTALL)
        if design_match:
            design_lang = design_match.group(1).strip()
            
        return {
            "title": metadata.get("title", file_path.stem),
            "date": str(metadata.get("date", "")),
            "tags": metadata.get("tags", []),
            "summary": design_lang[:200] + "..." if len(design_lang) > 200 else design_lang,
            "path": str(file_path.relative_to(self.base_path))
        }

    def reindex(self):
        index = []
        roots = list(self.cases_roots)
        # 各智能体分库
        if self.agents_root.exists():
            for agent_dir in self.agents_root.iterdir():
                cand = agent_dir / "cases"
                if cand.is_dir():
                    roots.append(cand)

        seen = set()
        for root in roots:
            if not root.exists():
                continue
            for file in root.glob("*.md"):
                if file.name.lower() == "readme.md":
                    continue
                key = file.resolve()
                if key in seen:
                    continue
                seen.add(key)
                print(f"DEBUG: Found case: {file.name}")
                meta = self.extract_metadata(file)
                if meta:
                    index.append(meta)
        
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=4, ensure_ascii=False)
        
        print(f"SUCCESS: Knowledge index updated: {len(index)} cases indexed at {self.index_file}")

if __name__ == "__main__":
    # Get the root directory (parent of tools/)
    script_dir = Path(__file__).resolve().parent
    root_dir = script_dir.parent
    lib = KnowledgeLibrarian(str(root_dir))
    lib.reindex()

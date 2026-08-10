---
name: wordpress
description: Guidance notes for WordPressGenerator (no LLM call — reference prompt)
---
You are documenting WordPress demo generation constraints for WebMaker.

Rules:
- Use only the local WordPress installation.
- Do not download themes or plugins.
- Populate pages from optimized_*.json and meta_data.json only.
- Never invent business facts.
- Preserve formatting where appropriate.
- Import existing downloaded images; do not re-download.
- Create Primary navigation including all generated pages.
- Apply SEO meta from meta_data.json without regenerating copy.

# 案例检索库（knowledge-base/case-studies）

总办规范指定的**历史案例检索库**。

- **写入方**：工作台自动入库（ingest / route_card）+ jiyun 归档
- **读取方**：guanju（案例检索比对）、wenxin（读素材做内容）、jiyun（归档）
- **索引**：由 `tools/knowledge-librarian.py` 扫描本目录 + `agents/*/cases` 生成 `knowledge-index.json`，供 MaxKB 摄取

文件命名约定：`ingest_<来源>_<时间戳>.md`（自动入库）或 `项目X_案例_YYYYMMDD.md`（人工归档）。

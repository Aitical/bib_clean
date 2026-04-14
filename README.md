# Bib Manager CLI

一个用于合并多个 BibTeX 文件、去重并自动更新 LaTeX 引用 Key 的 CLI 工具。

## 功能
- **合并**: 解析多个 `.bib` 文件。
- **去重**: 基于标题和作者识别重复条目。
- **交互式解决**: 用户在终端中选择保留哪条记录。
- **Key 冲突处理**: 自动检测同 Key 不同文的情况并重命名。
- **Tex 更新**: 自动扫描 `.tex` 文件并替换为最终的 Key，安全避开注释。
- **字段精简与校验**: 仅保留各 Bib 类型的必备字段，缺失必备字段或缺失引用 Key 的条目会被跳过。
  - `article` 必备: `author/title/journal/volume/number/pages/year/doi`
  - `inproceedings` 与 `conference` 必备: `author/title/booktitle/year/doi`

## 安装

需要 Python 3.9+。

```bash
pip install -r requirements.txt
```

## 使用指南

### 1. 添加 Bib 文件
将所有相关的 bib 文件添加到数据库。

```bash
python -m src.cli add paper1.bib paper2.bib
```

### 2. 扫描重复
分析数据库，查找重复内容和 Key 冲突。

```bash
python -m src.cli scan
```

### 3. 解决冲突
进入交互式界面，选择主条目。

```bash
python -m src.cli resolve
```
- 输入 `a` 自动选择第一个（默认）。
- 输入 `ID` 选择特定条目。
- 输入 `s` 跳过合并。

### 4. 标准化期刊/会议名称 (可选)
将期刊或会议名称标准化为 BibTeX String Key (例如 `TPAMI`, `CVPR`)。支持启发式匹配和 LLM 模糊匹配。

1. 准备 CSV 文件 (列名: `abbr`, `name`)。
2. 运行标准化命令：

```bash
python -m src.cli normalize --csv-path strings.csv
```

如果需要使用 LLM 辅助匹配 (针对生僻缩写)：
```bash
python -m src.cli normalize --csv-path strings.csv --api-key "sk-..."
```

### 5. 导出 Bib
生成去重后的最终文件。

```bash
python -m src.cli export references.bib
```

### 6. 更新 Latex 源码
扫描 tex 文件，将旧 Key 替换为新 Key。会生成 `.tex.bak` 备份。

```bash
python -m src.cli update-tex ./thesis/
```

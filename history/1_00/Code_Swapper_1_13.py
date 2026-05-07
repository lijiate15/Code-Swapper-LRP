# -*- coding: utf-8 -*-
"""
脚本修改自动执行器 (Code_Swapper.py)
==================================================
配套提示词「Requirements Analysis & Instruction Generation Assistant」使用。

工作流程:
  1. 弹窗选择源脚本文件（.py）
  2. 弹窗选择指令文档文件（.md）
  3. 解析指令文档，提取所有 Modification 块
  4. 阶段 1 - 预检查：在源文件中验证每个 Locate 字符串能否唯一精确匹配
  5. 阶段 2 - 用户确认：全部通过才询问是否执行
  6. 阶段 3 - 执行：复制源文件为新版本，执行所有替换，归档指令文档

安全保证:
  - 源文件全程只读，绝不修改
  - 预检查任一失败 → 终止，不复制不执行
  - 不存在"半成品文件"的中间状态

路径记忆:
  - 上次选择的路径保存在脚本同目录的 Work/config.json
  - 下次启动自动读取，作为弹窗默认打开位置

用法:
  双击运行（弹窗模式）
  或命令行：python Code_Swapper_1_00.py <源脚本路径> <指令文档路径>
"""

import sys
import re
import shutil
import json
import logging
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from datetime import datetime


# ============================================================
# 配置
# ============================================================
ENCODING_CANDIDATES = ['utf-8', 'utf-8-sig', 'gbk', 'cp936']  # 文件编码尝试顺序

# Work 文件夹和 config.json 路径（脚本同目录下的 Work/ 子文件夹）
SCRIPT_DIR = Path(__file__).resolve().parent
WORK_DIR = SCRIPT_DIR / "Work"
CONFIG_PATH = WORK_DIR / "config.json"
LOGS_DIR = WORK_DIR / "logs"
ARCHIVE_DIR = SCRIPT_DIR / "历史版本"  # 归档根目录（脚本平级）

# 日志上限配置
LOG_MAX_COUNT = 60   # 超过此数量触发清理
LOG_KEEP_COUNT = 30  # 清理后保留数量


# ============================================================
# 日志初始化
# ============================================================
def init_log() -> None:
    """
    在 Work/logs/ 下创建本次运行的日志文件（按时间戳命名）。
    同时将 logging 配置为同步输出到控制台和日志文件。
    超过 LOG_MAX_COUNT 份时，删除最旧的直到剩 LOG_KEEP_COUNT 份。
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 日志轮转：超过上限则删除最旧的
    existing = sorted(LOGS_DIR.glob("run_*.log"))
    if len(existing) >= LOG_MAX_COUNT:
        to_delete = existing[:len(existing) - LOG_KEEP_COUNT + 1]
        for f in to_delete:
            try:
                f.unlink()
            except Exception:
                pass

    # 创建本次日志文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"run_{timestamp}.log"

    # 配置 logging：同时输出到控制台和文件
    log_format = "%(asctime)s %(message)s"
    date_format = "%H:%M:%S"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    logging.info(f"=== 日志文件: {log_path} ===")


# ============================================================
# 路径记忆 — config.json 读写
# ============================================================
def load_config() -> dict:
    """读取 Work/config.json，返回配置字典。文件不存在时返回空字典。"""
    if CONFIG_PATH.is_file():
        try:
            raw = CONFIG_PATH.read_bytes()
            for enc in ENCODING_CANDIDATES:
                try:
                    return json.loads(raw.decode(enc))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
        except Exception:
            pass
    return {}


def save_config(data: dict) -> None:
    """将配置字典写入 Work/config.json。Work 文件夹不存在时自动创建。"""
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"  ⚠️  config.json 保存失败（不影响主流程）: {e}")


# ============================================================
# 弹窗文件选择
# ============================================================
def print_dialog_banner(title: str, what: str, usage: str) -> None:
    """弹窗前在控制台打印用途说明。"""
    print("=" * 60)
    print(f"[弹窗用途] {title}")
    print(f"  本次弹窗：{what}")
    print(f"  用途：{usage}")
    print("=" * 60)


def select_files_via_dialog(prompt_mode: bool = False) -> tuple[Path, Path]:
    """
    弹窗模式：依次弹窗选择源文件和指令文档。
    prompt_mode=True 时，源文件筛选 .txt（提示词模式）；否则筛选 .py（代码模式）。
    返回 (source_path, instruction_path)。
    取消或关闭弹窗时退出程序。
    """
    config = load_config()

    # ── 弹窗 1：选择源文件 ──────────────────────────────────
    if prompt_mode:
        last_source_dir = config.get("last_prompt_dir", "")
        source_title = "选择提示词文件"
        source_what = "选择你想要修改的提示词文件（.txt）"
        source_usage = "文件将被复制为版本号 +1 的新文件，所有替换在新文件上执行，源文件保持不变"
        source_dialog_title = "选择提示词文件（.txt）"
        source_filetypes = [("文本文件", "*.txt"), ("所有文件", "*.*")]
    else:
        last_source_dir = config.get("last_script_dir", "")
        source_title = "选择源脚本"
        source_what = "选择你想要修改的 Python 脚本文件（.py）"
        source_usage = "脚本将被复制为版本号 +1 的新文件，所有替换在新文件上执行，源文件保持不变"
        source_dialog_title = "选择源脚本（.py）"
        source_filetypes = [("Python 脚本", "*.py"), ("所有文件", "*.*")]

    init_dir_source = last_source_dir if last_source_dir and Path(last_source_dir).is_dir() else str(Path.home())

    print_dialog_banner(
        title=source_title,
        what=source_what,
        usage=source_usage,
    )

    root = tk.Tk()
    root.withdraw()
    source_str = filedialog.askopenfilename(
        title=source_dialog_title,
        initialdir=init_dir_source,
        filetypes=source_filetypes,
    )
    root.destroy()

    if not source_str:
        print("❌ 未选择源文件，已取消。")
        sys.exit(0)

    source_path = Path(source_str).resolve()
    print(f"  ✅ 已选择源文件: {source_path}")

    # ── 弹窗 2：选择指令文档 ────────────────────────────────
    last_instruction_dir = config.get("last_instruction_dir", "")
    init_dir_instruction = (
        last_instruction_dir
        if last_instruction_dir and Path(last_instruction_dir).is_dir()
        else str(source_path.parent)
    )

    print_dialog_banner(
        title="选择指令文档",
        what="选择由 Claude 生成的修改指令文档（.txt）",
        usage="脚本将解析文档中的 Locate/Replace 块，在新文件上执行查找替换",
    )

    root = tk.Tk()
    root.withdraw()
    instruction_str = filedialog.askopenfilename(
        title="选择指令文档（.txt）",
        initialdir=init_dir_instruction,
        filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
    )
    root.destroy()

    if not instruction_str:
        print("❌ 未选择指令文档，已取消。")
        sys.exit(0)

    instruction_path = Path(instruction_str).resolve()
    print(f"  ✅ 已选择指令文档: {instruction_path}")

    # ── 保存路径到 config.json ──────────────────────────────
    if prompt_mode:
        config["last_prompt_dir"] = str(source_path.parent)
    else:
        config["last_script_dir"] = str(source_path.parent)
    config["last_instruction_dir"] = str(instruction_path.parent)
    save_config(config)

    return source_path, instruction_path


# ── 菜单入口：修改提示词 ────────────────────────────────────
def run_prompt_modification() -> tuple[Path, Path]:
    """菜单选项3的入口：以提示词模式弹窗选择文件。"""
    print("（弹窗模式）请在弹窗中依次选择提示词文件和指令文档...\n")
    return select_files_via_dialog(prompt_mode=True)


# ============================================================
# 工具函数
# ============================================================
# ============================================================
# 提示词修改专用解析器（选项3，与选项2完全隔离）
# ============================================================
def parse_prompt_instruction_doc(content: str) -> list[dict]:
    """
    解析提示词修改指令文档。仅支持 boundary 格式：
      Start line: [首定位行完整内容]
      Start line +1: [中间锚行，可选]
      End line: [尾定位行完整内容]
      Replace with:
      =====REPLACE=====
      [新内容]
      =====END=====

    返回 list，每项是 dict:
      {
        "index":        int,
        "skip":         bool,
        "start_line":   str,
        "end_line":     str,
        "middle_lines": list[str],
        "replace":      str,
        "raw_block":    str,
      }
    """
    # 去除说明块（兼容中英文块名）
    content = re.sub(
        r'#{5,}[^\n]*(?:修改说明|change-log)[^\n]*#{5,}.*?#{5,}[^\n]*(?:说明结束|end-of-change-log)[^\n]*#{5,}',
        '',
        content,
        flags=re.DOTALL | re.IGNORECASE
    )

    results = []

    if not content.startswith('\n'):
        content = '\n' + content
    splits = re.split(
        r'\n\s*\*{0,2}Modification\s+(\d+)\s+of\s+\d+\*{0,2}\s*\n',
        content,
        flags=re.IGNORECASE
    )

    if len(splits) < 3:
        raise ValueError(
            "未在指令文档中找到任何 'Modification X of N' 块。\n"
            "请确认文档格式正确（提示词修改专用格式：Start line / End line / Replace with）。"
        )

    for i in range(1, len(splits), 2):
        idx = int(splits[i])
        block = splits[i + 1] if i + 1 < len(splits) else ""

        # SKIP 检测
        if re.search(r'^\s*\[SKIP[^\]]*\]', block, re.IGNORECASE | re.MULTILINE):
            results.append({
                "index": idx, "skip": True,
                "start_line": "", "end_line": "",
                "middle_lines": [], "replace": "",
                "raw_block": block,
            })
            continue

        # 解析首定位行（注意：用 [ \t]? 保留行内前导缩进）
        start_line_match = re.search(r'^Start line:[ \t]?(.*)$', block, re.IGNORECASE | re.MULTILINE)
        end_line_match   = re.search(r'^End line:[ \t]?(.*)$',   block, re.IGNORECASE | re.MULTILINE)

        if not start_line_match or not end_line_match:
            raise ValueError(
                f"Modification {idx}: 未找到 Start line 或 End line。\n"
                f"提示词修改模式仅支持 boundary 格式：\n"
                f"  Start line: [首定位行完整内容]\n"
                f"  End line: [尾定位行完整内容]\n"
                f"  Replace with:\n"
                f"  =====REPLACE=====\n"
                f"  [新内容]\n"
                f"  =====END====="
            )

        # 解析中间锚行（可选）
        middle_line_matches = re.findall(
            r'^Start line\s*\+\s*(\d+):[ \t]?(.*)$',
            block,
            re.IGNORECASE | re.MULTILINE
        )
        middle_lines = [
            text for _, text in sorted(middle_line_matches, key=lambda p: int(p[0]))
        ]

        # 解析替换内容
        replace_match = re.search(
            r'Replace\s+with\s*:\s*\n=====REPLACE=====\n(.*?)\n=====END=====',
            block,
            re.DOTALL | re.IGNORECASE
        )
        if not replace_match:
            raise ValueError(
                f"Modification {idx}: 未找到 Replace with / =====REPLACE===== / =====END=====。"
            )

        replace_raw = replace_match.group(1).replace('\r\n', '\n').replace('\r', '\n')

        results.append({
            "index": idx,
            "skip": False,
            "start_line": start_line_match.group(1),
            "end_line": end_line_match.group(1),
            "middle_lines": middle_lines,
            "replace": replace_raw,
            "raw_block": block,
        })

    return results


def precheck_prompt(source_content: str, modifications: list[dict]) -> tuple[bool, list[str]]:
    """
    提示词修改专用预检查：验证所有 boundary 定位行能否唯一精确匹配。
    起始定位：start_line 往前取 2 行，共 3 行组合验证唯一性。
    终止定位：end_line 往后取 2 行，共 3 行组合验证唯一性。
    返回 (是否全部通过, 报告行列表)。
    """
    report = []
    all_pass = True
    lines = source_content.split('\n')

    def find_unique_by_combo(
        target_line: str,
        context_before: int,
        context_after: int,
        middle_lines: list[str] = None,
    ) -> tuple[list[int], str]:
        """
        在 lines 中找出所有出现 target_line 的位置，
        再用前后各取 context_before / context_after 行的组合筛选唯一性。
        如果传入 middle_lines（中间锚行列表），先用它过滤候选：
          候选位置 i 之后的第 1..N 行必须依次等于 middle_lines[0..N-1]，
          否则该候选被剔除。这一步用于区分「连续重复行」场景下的不同目标。
        返回 (候选行号列表, 组合描述字符串)。
        """
        candidates = [i for i, l in enumerate(lines) if l == target_line]

        # 用 middle_lines 过滤候选（核心修复：连续重复行场景靠这里区分）
        if middle_lines:
            filtered = []
            for i in candidates:
                ok = True
                for offset, mid_text in enumerate(middle_lines, start=1):
                    if i + offset >= len(lines) or lines[i + offset] != mid_text:
                        ok = False
                        break
                if ok:
                    filtered.append(i)
            candidates = filtered

        if len(candidates) <= 1:
            return candidates, ""
        # 用组合窗口进一步筛选：对每个候选位置取窗口，检查是否与其他候选位置的窗口完全相同
        def window(i: int) -> tuple:
            lo = max(0, i - context_before)
            hi = min(len(lines), i + context_after + 1)
            return tuple(lines[lo:hi])
        windows = [window(i) for i in candidates]
        # 找出窗口唯一的候选
        unique = [candidates[j] for j in range(len(candidates)) if windows.count(windows[j]) == 1]
        return unique, f"（{context_before}前+{context_after}后 组合定位）"

    for mod in modifications:
        idx = mod["index"]

        if mod["skip"]:
            report.append(f"  Mod {idx}: ⚠️  跳过（[SKIP] 确认项，无需修改）")
            continue

        start_line   = mod["start_line"]
        end_line     = mod["end_line"]
        middle_lines = mod.get("middle_lines", [])

        # 起始定位：start_line 往前取 2 行（共 3 行组合），并用中间锚行过滤候选
        start_unique, start_combo_desc = find_unique_by_combo(
            start_line, context_before=2, context_after=0, middle_lines=middle_lines
        )
        # 终止定位：end_line 往后取 2 行（共 3 行组合）
        end_unique, end_combo_desc = find_unique_by_combo(end_line, context_before=0, context_after=2)

        # 原始候选数（用于区分「未找到」vs「不唯一」的报错文字）
        start_all = [i for i, l in enumerate(lines) if l == start_line]
        end_all   = [i for i, l in enumerate(lines) if l == end_line]

        if len(start_all) == 0:
            all_pass = False
            report.append(f"  Mod {idx}: ❌ 首定位行未找到: {start_line!r}")
        elif len(start_unique) == 0:
            all_pass = False
            report.append(
                f"  Mod {idx}: ❌ 首定位行找到 {len(start_all)} 处，3行组合仍不唯一: {start_line!r}\n"
                f"           请在对话中告知 AI 扩展前置上下文行数"
            )
        elif len(start_unique) > 1:
            # 理论上不会到这里，保险起见保留
            all_pass = False
            report.append(f"  Mod {idx}: ❌ 首定位行组合匹配异常，请检查: {start_line!r}")
        elif len(end_all) == 0:
            all_pass = False
            report.append(f"  Mod {idx}: ❌ 尾定位行未找到: {end_line!r}")
        elif len(end_unique) == 0:
            all_pass = False
            report.append(
                f"  Mod {idx}: ❌ 尾定位行找到 {len(end_all)} 处，3行组合仍不唯一: {end_line!r}\n"
                f"           请在对话中告知 AI 扩展后置上下文行数"
            )
        elif len(end_unique) > 1:
            all_pass = False
            report.append(f"  Mod {idx}: ❌ 尾定位行组合匹配异常，请检查: {end_line!r}")
        elif end_unique[0] <= start_unique[0]:
            all_pass = False
            report.append(
                f"  Mod {idx}: ❌ 尾定位行（行{end_unique[0]+1}）不在首定位行（行{start_unique[0]+1}）之后"
            )
        else:
            # 中间锚行连续性校验
            start_idx = start_unique[0]
            end_idx   = end_unique[0]
            continuity_ok  = True
            continuity_err = ""
            for offset, mid_text in enumerate(middle_lines, start=1):
                expected_idx = start_idx + offset
                if expected_idx >= end_idx:
                    continuity_ok = False
                    continuity_err = (
                        f"中间锚行 +{offset} 越过了尾定位行（首行{start_idx+1} → 尾行{end_idx+1}，"
                        f"中间只有 {end_idx-start_idx-1} 行可容纳）"
                    )
                    break
                if lines[expected_idx] != mid_text:
                    continuity_ok = False
                    continuity_err = (
                        f"中间锚行 +{offset} 与源文件第 {expected_idx+1} 行不一致\n"
                        f"           期望: {mid_text!r}\n"
                        f"           实际: {lines[expected_idx]!r}"
                    )
                    break

            if not continuity_ok:
                all_pass = False
                report.append(f"  Mod {idx}: ❌ {continuity_err}")
            else:
                combo_note = f" {start_combo_desc}" if start_combo_desc else ""
                mid_info = f"，含 {len(middle_lines)} 条中间锚行" if middle_lines else ""
                report.append(
                    f"  Mod {idx}: ✅ 定位行模式{combo_note} — 首行{start_idx+1} / 尾行{end_idx+1}"
                    f"，替换中间 {end_idx-start_idx-1} 行{mid_info}"
                )

    return all_pass, report


def execute_prompt_replacements(
    source_path: Path,
    target_path: Path,
    modifications: list[dict],
    encoding: str,
) -> list[str]:
    """提示词修改专用执行器：复制源文件并执行所有 boundary 替换。"""
    report = []

    shutil.copy2(source_path, target_path)
    report.append(f"  ✅ 已复制: {source_path.name} -> {target_path.name}")

    new_content = target_path.read_text(encoding=encoding)

    for mod in modifications:
        idx = mod["index"]
        if mod["skip"]:
            report.append(f"  Mod {idx}: ⚠️  跳过（[SKIP]）")
            continue

        lines        = new_content.split('\n')
        start_line   = mod["start_line"]
        end_line     = mod["end_line"]
        middle_lines = mod.get("middle_lines", [])

        # 起始定位：先用中间锚行过滤候选，再用「前 2 行 + 本行」3 行组合去重
        start_candidates = [i for i, l in enumerate(lines) if l == start_line]
        if middle_lines:
            filtered = []
            for i in start_candidates:
                ok = True
                for offset, mid_text in enumerate(middle_lines, start=1):
                    if i + offset >= len(lines) or lines[i + offset] != mid_text:
                        ok = False
                        break
                if ok:
                    filtered.append(i)
            start_candidates = filtered
        if len(start_candidates) > 1:
            def _start_window(i): return tuple(lines[max(0, i-2):i+1])
            wins = [_start_window(i) for i in start_candidates]
            start_candidates = [start_candidates[j] for j in range(len(start_candidates)) if wins.count(wins[j]) == 1]
        start_idx = start_candidates[0]

        # 终止定位：end_line 往后取 2 行，共 3 行组合找唯一位置
        end_candidates = [i for i, l in enumerate(lines) if l == end_line]
        if len(end_candidates) > 1:
            def _end_window(i): return tuple(lines[i:min(len(lines), i+3)])
            wins = [_end_window(i) for i in end_candidates]
            end_candidates = [end_candidates[j] for j in range(len(end_candidates)) if wins.count(wins[j]) == 1]
        end_idx = end_candidates[0]

        new_lines  = lines[:start_idx + 1] + mod["replace"].split('\n') + lines[end_idx:]
        new_content = '\n'.join(new_lines)
        report.append(f"  Mod {idx}: ✅ 已替换（定位行模式）")

    target_path.write_text(new_content, encoding=encoding)
    report.append(f"  ✅ 已保存: {target_path.name}")

    return report


def read_file_smart(path: Path) -> tuple[str, str]:
    """智能读取文件，自动尝试多种编码。返回 (内容, 编码名)。"""
    raw = path.read_bytes()
    for enc in ENCODING_CANDIDATES:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"无法识别文件编码: {path}")


def detect_version_and_next(filename: str) -> tuple[str, str]:
    """
    从文件名识别版本号，返回 (当前版本号字符串, 新文件名)。
    支持格式如:
      ab-av1_3_01.py    -> ab-av1_3_02.py
      script_v1.py      -> script_v2.py
      tool_3_09.py      -> tool_3_10.py
    """
    stem = Path(filename).stem
    suffix = Path(filename).suffix

    # 匹配末尾的 _数字 或 v数字
    patterns = [
        (r'(.*?)(_)(\d+)$', 2),   # _数字: ab-av1_3_01
        (r'(.*?)(v)(\d+)$', 2),   # v数字: script_v1
        (r'(.*?)(V)(\d+)$', 2),   # V数字: script_V1
    ]
    for pat, _ in patterns:
        m = re.match(pat, stem)
        if m:
            prefix, sep, num_str = m.group(1), m.group(2), m.group(3)
            new_num = int(num_str) + 1
            new_num_str = str(new_num).zfill(len(num_str))  # 保持原位数(01 -> 02)
            new_stem = f"{prefix}{sep}{new_num_str}"
            return num_str, new_stem + suffix

    raise ValueError(
        f"无法从文件名 '{filename}' 识别版本号。\n"
        f"支持的格式：xxx_01.py / xxx_v1.py 等（末尾必须是数字）"
    )


# ============================================================
# 归档工具函数
# ============================================================
def extract_base_name(filename: str) -> str:
    """
    从文件名中提取基础名（去掉末尾版本号后缀）。
    例：Code_Swapper_1_09.py -> Code_Swapper
         bangumi_1_08.py     -> bangumi
         script_v3.py        -> script
    """
    stem = Path(filename).stem
    patterns = [
        r'^(.*?)_\d+$',   # 末尾 _数字
        r'^(.*?)v\d+$',   # 末尾 v数字
        r'^(.*?)V\d+$',   # 末尾 V数字
    ]
    for pat in patterns:
        m = re.match(pat, stem)
        if m:
            # 递归剥离，直到没有更多版本号后缀
            inner = m.group(1).rstrip('_')
            # 再剥一层（处理 Name_1_09 -> Name_1 -> Name）
            for pat2 in patterns:
                m2 = re.match(pat2, inner)
                if m2:
                    return m2.group(1).rstrip('_')
            return inner
    return stem


def archive_files(source_path: Path, instruction_path: Path) -> list[str]:
    """
    将旧版源脚本和指令文档移入「历史版本/<基础名>/」归档文件夹。
    同名冲突时自动追加 _YYYYMMDD_HHMMSS 后缀。
    返回归档报告行列表。
    """
    report = []
    base_name = extract_base_name(source_path.name)
    dest_dir = ARCHIVE_DIR / base_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    report.append(f"  归档目录: {dest_dir}")

    for file_path in (source_path, instruction_path):
        dest = dest_dir / file_path.name
        if dest.exists():
            # 同名冲突：用文件修改时间作后缀
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            suffix_ts = mtime.strftime("%Y%m%d_%H%M%S")
            new_name = f"{file_path.stem}_{suffix_ts}{file_path.suffix}"
            dest = dest_dir / new_name
            report.append(f"  ⚠️  同名冲突，重命名为: {new_name}")
        shutil.move(str(file_path), str(dest))
        report.append(f"  ✅ 已归档: {file_path.name} -> {dest.name}")

    return report


# ============================================================
# 指令文档解析
# ============================================================
def parse_instruction_doc(content: str) -> list[dict]:
    """
    从 Markdown 指令文档中提取所有 Modification 块。

    返回 list，每项是 dict:
      {
        "index":   int,    # 第几个 Modification (1-based)
        "skip":    bool,   # 是否为 [SKIP] 标记
        "locate":  str,    # 原始代码（skip=True 时为空）
        "replace": str,    # 新代码（skip=True 时为空）
        "raw_block": str,  # 原始文本（便于报错时显示）
      }
    """
    # 去除说明块：兼容中英两套块名
    #   中文：修改说明 / 说明结束
    #   英文：change-log / end-of-change-log
    content = re.sub(
        r'#{5,}[^\n]*(?:修改说明|change-log)[^\n]*#{5,}.*?#{5,}[^\n]*(?:说明结束|end-of-change-log)[^\n]*#{5,}',
        '',
        content,
        flags=re.DOTALL | re.IGNORECASE
    )
    results = []

    # 用 "Modification X of N" 作为分隔锚点切分
    # 兼容粗体/非粗体: **Modification 1 of 3** 或 Modification 1 of 3
    # 确保文件开头的 Modification 也能被正则匹配（补一个前导换行）
    if not content.startswith('\n'):
        content = '\n' + content
    splits = re.split(
        r'\n\s*\*{0,2}Modification\s+(\d+)\s+of\s+\d+\*{0,2}\s*\n',
        content,
        flags=re.IGNORECASE
    )
    # splits 结构: [前导文本, "1", 块1内容, "2", 块2内容, ...]

    if len(splits) < 3:
        raise ValueError(
            "未在指令文档中找到任何 'Modification X of N' 块。\n"
            "请确认文档格式正确（粘贴自提示词输出的 Path 2 指令）。"
        )

    for i in range(1, len(splits), 2):
        idx = int(splits[i])
        block = splits[i + 1] if i + 1 < len(splits) else ""

        # 检查是否为 SKIP 块（仅在 Header 区域内匹配“行级 SKIP”）
        header = block

        # 查找 Locate 标记位置（优先 =====LOCATE=====，其次 Locate:）
        locate_pos = block.lower().find('=====locate=====')
        if locate_pos == -1:
            locate_pos = block.lower().find('locate')

        if locate_pos != -1:
            header = block[:locate_pos]

        # 仅在 header 内匹配“行首 SKIP”
        if re.search(r'^\s*\[SKIP[^\]]*\]', header, re.IGNORECASE | re.MULTILINE):
            results.append({
                "index": idx,
                "skip": True,
                "locate": "",
                "replace": "",
                "raw_block": block,
            })
            continue

        # 提取 Locate / Replace 代码块（唯一格式：=====LOCATE===== / =====REPLACE===== / =====END=====）
        locate_match = re.search(
            r'Locate(?:\s+the\s+following\s+code)?\s*:\s*\n=====LOCATE=====\n(.*?)\n=====REPLACE=====',
            block,
            re.DOTALL | re.IGNORECASE
        )
        replace_match = re.search(
            r'=====REPLACE=====\n(.*?)\n=====END=====',
            block,
            re.DOTALL | re.IGNORECASE
        )

        if not locate_match or not replace_match:
            raise ValueError(
                f"Modification {idx}: 无法解析出 Locate 或 Replace 代码块。\n"
                f"格式要求：\n"
                f"  Locate the following code:\n"
                f"  =====LOCATE=====\n"
                f"  [原始代码]\n"
                f"  =====REPLACE=====\n"
                f"  [新代码]\n"
                f"  =====END====="
            )

        results.append({
            "index": idx,
            "skip": False,
            "locate": locate_match.group(1).replace('\r\n', '\n').replace('\r', '\n'),
            "replace": replace_match.group(1).replace('\r\n', '\n').replace('\r', '\n'),
            "raw_block": block,
        })
    return results


# ============================================================
# 预检查
# ============================================================
def precheck(source_content: str, modifications: list[dict]) -> tuple[bool, list[str]]:
    """
    在源文件内容中验证所有 Locate 字符串能否唯一精确匹配。
    返回 (是否全部通过, 报告行列表)。
    """
    report = []
    all_pass = True

    for mod in modifications:
        idx = mod["index"]

        if mod["skip"]:
            report.append(f"  Mod {idx}: ⚠️  跳过（[SKIP] 确认项，无需修改）")
            continue

        locate = mod["locate"]
        count = source_content.count(locate)

        if count == 1:
            report.append(f"  Mod {idx}: ✅ 找到唯一匹配")
        elif count == 0:
            all_pass = False
            preview = locate.strip().split('\n')[0][:60]
            report.append(
                f"  Mod {idx}: ❌ 未找到！Locate 字符串不存在于源文件\n"
                f"           首行预览: {preview!r}"
            )
        else:
            all_pass = False
            preview = locate.strip().split('\n')[0][:60]
            report.append(
                f"  Mod {idx}: ❌ 找到 {count} 处匹配！无法确定改哪一处\n"
                f"           首行预览: {preview!r}"
            )

    return all_pass, report


# ============================================================
# 执行
# ============================================================
def execute_replacements(
    source_path: Path,
    target_path: Path,
    modifications: list[dict],
    encoding: str,
) -> list[str]:
    """复制源文件并执行所有替换。返回执行报告行列表。"""
    report = []

    # 复制源文件 -> 新文件
    shutil.copy2(source_path, target_path)
    report.append(f"  ✅ 已复制: {source_path.name} -> {target_path.name}")

    # 读取新文件内容
    new_content = target_path.read_text(encoding=encoding)

    # 依次执行替换
    for mod in modifications:
        idx = mod["index"]
        if mod["skip"]:
            report.append(f"  Mod {idx}: ⚠️  跳过（[SKIP]）")
            continue

        new_content = new_content.replace(mod["locate"], mod["replace"], 1)
        report.append(f"  Mod {idx}: ✅ 已替换")

    # 写回新文件
    target_path.write_text(new_content, encoding=encoding)
    report.append(f"  ✅ 已保存: {target_path.name}")

    return report


# ============================================================
# 提示词内容（从外部文件读取，与脚本平级）
# ============================================================
try:
    prompt_file = SCRIPT_DIR / "Code_Swapper使用提示词.txt"
    if prompt_file.is_file():
        PROMPT_TEXT = prompt_file.read_text(encoding="utf-8")
    else:
        PROMPT_TEXT = "⚠️ 未找到提示词文件：Code_Swapper使用提示词.txt（应与脚本放在同一目录）"
except Exception as e:
    PROMPT_TEXT = f"⚠️ 读取提示词失败: {e}"


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 60)
    print("脚本修改自动执行器")
    print("=" * 60)

    # ---------- 参数解析：有参数走命令行模式，无参数走菜单模式 ----------
    if len(sys.argv) == 3:
        # 命令行模式（保持兼容）
        source_path = Path(sys.argv[1]).resolve()
        instruction_path = Path(sys.argv[2]).resolve()

        if not source_path.is_file():
            print(f"❌ 源脚本不存在: {source_path}")
            sys.exit(1)
        if not instruction_path.is_file():
            print(f"❌ 指令文档不存在: {instruction_path}")
            sys.exit(1)

    elif len(sys.argv) == 1:
        # 菜单模式
        while True:
            print("\n请选择操作：")
            print("  1. 查看使用提示词（并复制提示词到剪切板）")
            print("  2. 进行代码修改")
            print("  3. 修改提示词")
            choice = input("请输入选项 (1/2/3): ").strip()
            if choice == '1':
                print("\n" + "=" * 60)
                print(PROMPT_TEXT.strip())
                print("=" * 60)
                # 复制提示词到系统剪切板（tkinter 原生，无需第三方库）
                try:
                    root = tk.Tk()
                    root.withdraw()
                    root.clipboard_clear()
                    root.clipboard_append(PROMPT_TEXT.strip())
                    root.update()
                    root.destroy()
                    print("已复制提示词到剪切板。")
                except Exception as e:
                    print(f"⚠️ 复制到剪切板失败: {e}")
                print("\n请选择：")
                print("  1. 继续修改（回车默认）")
                print("  2. 退出")
                c = input("请输入选项 (1/2，回车默认1): ").strip()
                if c == '2':
                    sys.exit(0)
            if choice in ('', '1', '2', '3'):
                if choice != '1':
                    break
            else:
                print("无效输入，请输入 1、2 或 3")
                continue
            if choice in ('2', '3'):
                break
        if choice == '3':
            source_path, instruction_path = run_prompt_modification()
        else:
            print("（弹窗模式）请在弹窗中依次选择源脚本和指令文档...\n")
            source_path, instruction_path = select_files_via_dialog()

    else:
        print(__doc__)
        print("❌ 参数错误：需要 0 个或 2 个参数。\n")
        sys.exit(1)

    print(f"\n源脚本    : {source_path}")
    print(f"指令文档  : {instruction_path}")

    # ---------- 计算新文件名 ----------
    try:
        old_ver, new_filename = detect_version_and_next(source_path.name)
    except ValueError as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    target_path = source_path.parent / new_filename
    print(f"新脚本    : {target_path.name}  （版本 {old_ver} -> +1）")

    if target_path.exists():
        print(f"\n⚠️  目标文件已存在: {target_path}")
        ans = input("    是否覆盖？(y/N): ").strip().lower()
        if ans != 'y':
            print("已取消。")
            sys.exit(0)

    # ---------- 读取文件 ----------
    try:
        source_content, source_encoding = read_file_smart(source_path)
        source_content = source_content.replace('\r\n', '\n').replace('\r', '\n')
        print(f"源文件编码: {source_encoding}")
    except Exception as e:
        print(f"\n❌ 读取源脚本失败: {e}")
        sys.exit(1)

    try:
        instruction_content, _ = read_file_smart(instruction_path)
        instruction_content = instruction_content.replace('\r\n', '\n').replace('\r', '\n')
    except Exception as e:
        print(f"\n❌ 读取指令文档失败: {e}")
        sys.exit(1)

    # ---------- 解析指令（根据模式选择解析器）----------
    use_prompt_mode = (len(sys.argv) == 1 and 'choice' in dir() and locals().get('choice') == '3')
    try:
        if use_prompt_mode:
            modifications = parse_prompt_instruction_doc(instruction_content)
        else:
            modifications = parse_instruction_doc(instruction_content)
    except ValueError as e:
        print(f"\n❌ 指令文档解析失败:\n{e}")
        sys.exit(1)

    total = len(modifications)
    skip_count = sum(1 for m in modifications if m["skip"])
    real_count = total - skip_count
    print(f"\n解析结果  : 共 {total} 项（实际修改 {real_count} 项，跳过 {skip_count} 项）")

    # ---------- 阶段 1：预检查 ----------
    print("\n" + "─" * 60)
    print("【阶段 1】预检查 — 在源文件中验证每个 Locate 字符串")
    print("─" * 60)
    all_pass, precheck_report = (
        precheck_prompt(source_content, modifications)
        if use_prompt_mode else
        precheck(source_content, modifications)
    )
    for line in precheck_report:
        print(line)

    if not all_pass:
        print("\n" + "=" * 60)
        print("❌ 预检查未通过 — 已终止，源文件未被修改，新文件未生成")
        print("=" * 60)

        # 自动生成失败记录文件名（序号递增）
        base_name = instruction_path.stem
        fail_dir = instruction_path.parent
        idx = 1
        while True:
            fail_filename = f"{base_name}_修改失败_{idx:03d}.txt"
            fail_path = fail_dir / fail_filename
            if not fail_path.exists():
                break
            idx += 1

        print(f"\n请选择：")
        print(f"  1. 保存失败记录为 {fail_filename}（回车默认）")
        print(f"  2. 退出")
        c = input("请输入选项 (1/2，回车默认1): ").strip()
        if c != '2':
            # 保存失败记录
            fail_lines = ["预检查失败记录\n", f"指令文档：{instruction_path}\n",
                          f"源脚本：{source_path}\n\n", "失败详情：\n"]
            fail_lines += [line + "\n" for line in precheck_report]
            fail_path.write_text("".join(fail_lines), encoding='utf-8')
            print(f"  ✅ 已保存失败记录：{fail_path}")

            print(f"\n请选择：")
            print(f"  1. 继续代码修改（回车默认）")
            print(f"  2. 退出")
            c2 = input("请输入选项 (1/2，回车默认1): ").strip()
            if c2 == '2':
                sys.exit(0)
            else:
                main()
                return
        sys.exit(2)

    print("\n✅ 预检查全部通过")

    # ---------- 阶段 2：用户确认 ----------
    print("\n" + "─" * 60)
    print("【阶段 2】用户确认")
    print("─" * 60)
    print(f"即将执行:")
    print(f"  - 复制 {source_path.name} 为 {target_path.name}")
    print(f"  - 在新文件上执行 {real_count} 处替换")
    print(f"  - 源文件保持不变")
    ans = input("\n是否继续？(y/N): ").strip().lower()
    if ans != 'y':
        print("已取消，源文件未被修改，新文件未生成。")
        sys.exit(0)

    # ---------- 阶段 3：执行 ----------
    print("\n" + "─" * 60)
    print("【阶段 3】执行替换")
    print("─" * 60)
    try:
        exec_report = (
            execute_prompt_replacements(source_path, target_path, modifications, source_encoding)
            if use_prompt_mode else
            execute_replacements(source_path, target_path, modifications, source_encoding)
        )
        for line in exec_report:
            print(line)
    except Exception as e:
        # 出现意外错误，删除已生成的新文件，避免半成品
        if target_path.exists():
            target_path.unlink()
            print(f"  ⚠️  已删除半成品文件: {target_path.name}")
        print(f"\n❌ 执行过程出错: {e}")
        input("\n按回车键退出...")
        sys.exit(3)

    # ---------- 归档 ----------
    print("\n" + "─" * 60)
    print("【归档】将旧版脚本和指令文档移入历史版本")
    print("─" * 60)
    try:
        archive_report = archive_files(source_path, instruction_path)
        for line in archive_report:
            print(line)
    except Exception as e:
        print(f"  ⚠️  归档过程出错（不影响新脚本）: {e}")

    # ---------- 完成 ----------
    print("\n" + "=" * 60)
    print("✅ 全部完成")
    print("=" * 60)
    print(f"新脚本   : {target_path}")
    print(f"\n请手动测试新脚本，确认行为正确后再投入使用。")

    # 修改成功后询问是否继续
    if len(sys.argv) == 1:
        print("\n请选择：")
        print("  1. 继续代码修改（回车默认）")
        print("  2. 退出")
        c = input("请输入选项 (1/2，回车默认1): ").strip()
        if c == '2':
            sys.exit(0)
        else:
            main()


if __name__ == "__main__":
    init_log()
    try:
        main()
    except Exception as e:
        logging.exception(f"❌ 未处理的异常: {e}")
        input("\n按回车键退出...")
        sys.exit(99)

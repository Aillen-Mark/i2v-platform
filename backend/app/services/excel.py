"""Excel / CSV 读写：导入模板、批量导入解析、导出、密码条。

只负责文件层，不做业务校验（校验与落库在 `app/admin/candidates.py`）。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

TEMPLATE_HEADERS: tuple[str, ...] = ("姓名", "账号", "备注")
EXPORT_HEADERS: tuple[str, ...] = ("账号", "姓名", "初始密码", "状态", "备注")

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="4472C4")


@dataclass(slots=True)
class ParsedCandidateRow:
    row_no: int
    name: str
    account: str | None
    remark: str | None


def _style_header(ws, headers: tuple[str, ...], widths: tuple[int, ...]) -> None:
    for index, (header, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=1, column=index, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A2"


def build_template_bytes() -> bytes:
    """导入模板：姓名 / 账号（可空）/ 备注，附一行示例与填写说明。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "考生导入"
    _style_header(ws, TEMPLATE_HEADERS, (20, 20, 40))
    ws.append(["张三", "", "示例：账号留空则按规则自动生成"])
    ws.append(["李四", "20260001", "示例：也可直接填写学号"])
    ws.append(["", "", "示例：填写规范——姓名必填，账号可空，备注可空"])
    ws.cell(row=6, column=1, value="填写说明：").font = Font(bold=True)
    ws.cell(row=7, column=1, value="1. 第 1 行为表头，请勿修改；从第 2 行开始填写考生。")
    ws.cell(row=8, column=1, value="2. 姓名为空、账号重复（库内或本文件内）的行会被跳过并按行号报错，不影响其他行。")
    ws.cell(row=9, column=1, value="3. 账号留空时系统按 C2026 + 6 位序号规则自动生成（如 C2026000001）。")

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def parse_candidate_rows(data: bytes) -> list[ParsedCandidateRow]:
    """解析上传的 Excel，返回有效数据行（不做业务校验）。"""
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]

    rows: list[ParsedCandidateRow] = []
    for row_no, values in enumerate(ws.iter_rows(min_row=2, max_col=3, values_only=True), start=2):
        name = _as_text(values[0]) if len(values) > 0 else ""
        account = _as_text(values[1]) if len(values) > 1 else ""
        remark = _as_text(values[2]) if len(values) > 2 else ""
        if not name and not account and not remark:
            continue  # 整行空白，直接跳过
        rows.append(
            ParsedCandidateRow(
                row_no=row_no,
                name=name,
                account=account or None,
                remark=remark or None,
            )
        )
    wb.close()
    return rows


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def build_export_bytes(rows: list[tuple[object, ...]]) -> bytes:
    """导出 Excel：账号 / 姓名 / 初始密码 / 状态 / 备注。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "考生账号"
    _style_header(ws, EXPORT_HEADERS, (20, 16, 24, 10, 32))
    for row in rows:
        ws.append(["" if value is None else value for value in row])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def build_csv_bytes(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> bytes:
    """导出 CSV（UTF-8 BOM，Excel 直接双击不乱码）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def build_password_slips_bytes(rows: list[dict[str, object]]) -> bytes:
    """密码条 Excel：每行一个考生，可直接打印裁剪。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "密码条"
    headers = ("账号", "姓名", "初始密码", "可查看截止时间")
    _style_header(ws, headers, (22, 16, 20, 26))
    for row in rows:
        ws.append(
            [
                row.get("account"),
                row.get("name"),
                row.get("password") or "已过期，请重置",
                row.get("expire_at") or "",
            ]
        )
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

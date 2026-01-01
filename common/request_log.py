"""
请求日志模块 - 记录用户请求和回复到CSV文件

CSV格式：时间,用户ID,用户昵称,是否群聊,群名称,消息类型,请求内容,回复内容,回复类型,状态,耗时(秒)

设计原则：
1. 请求开始时立即记录（状态=处理中），防止中途崩溃丢失
2. 完成后更新状态和回复内容
3. 兜底逻辑：所有消息类型都记录，不允许遗漏
"""
import csv
import os
import threading
import time
from datetime import datetime

# 日志文件路径
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "requests.csv")

# 写入锁，防止并发写入冲突
_write_lock = threading.Lock()

# 请求追踪：用于更新已记录的请求 {request_id: line_number}
_request_tracker = {}

# CSV表头
CSV_HEADERS = [
    "timestamp",        # 请求时间
    "user_id",          # 用户ID
    "user_nickname",    # 用户昵称
    "is_group",         # 是否群聊
    "group_name",       # 群名称（私聊为空）
    "msg_type",         # 消息类型（TEXT/IMAGE_CREATE/GACHA_CREATE等）
    "query",            # 用户请求内容
    "reply",            # 回复内容
    "reply_type",       # 回复类型（TEXT/IMAGE/ERROR等）
    "status",           # 状态（处理中/成功/失败）
    "duration"          # 处理耗时（秒）
]


def _ensure_log_dir():
    """确保日志目录存在"""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)


def _ensure_csv_header():
    """确保CSV文件有表头"""
    _ensure_log_dir()
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def _truncate_content(content, max_length=500):
    """截断过长的内容"""
    if content is None:
        return ""
    content = str(content)
    # 替换换行符为空格，便于CSV查看
    content = content.replace('\n', ' ').replace('\r', '')
    if len(content) > max_length:
        return content[:max_length] + "..."
    return content


def _extract_user_info(context):
    """从context中提取用户信息"""
    msg = context.get("msg")
    is_group = context.get("isgroup", False)

    if msg:
        if is_group:
            user_id = getattr(msg, "actual_user_id", "") or ""
            user_nickname = getattr(msg, "actual_user_nickname", "") or ""
            group_name = getattr(msg, "other_user_nickname", "") or ""
        else:
            user_id = getattr(msg, "from_user_id", "") or ""
            user_nickname = getattr(msg, "from_user_nickname", "") or ""
            group_name = ""
    else:
        user_id = context.get("session_id", "")
        user_nickname = ""
        group_name = ""

    return user_id, user_nickname, is_group, group_name


def _get_msg_type(context):
    """获取消息类型名称"""
    if context.type:
        return context.type.name if hasattr(context.type, 'name') else str(context.type)
    return "UNKNOWN"


def log_request_start(context):
    """
    请求开始时记录日志（状态=处理中）
    
    Args:
        context: 请求上下文
    
    Returns:
        request_id: 请求ID，用于后续更新
    """
    try:
        _ensure_csv_header()

        user_id, user_nickname, is_group, group_name = _extract_user_info(context)
        msg_type = _get_msg_type(context)
        query = _truncate_content(context.content)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 生成请求ID
        request_id = f"{timestamp}_{user_id}_{id(context)}"

        row = [
            timestamp,
            user_id,
            user_nickname,
            "是" if is_group else "否",
            group_name,
            msg_type,
            query,
            "",           # reply - 待填充
            "",           # reply_type - 待填充
            "处理中",      # status
            ""            # duration - 待填充
        ]

        with _write_lock:
            with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(row)

        return request_id

    except Exception as e:
        from common.log import logger
        logger.warning(f"[request_log] failed to log request start: {e}")
        return None


def log_request_end(context, reply=None, start_time=None, request_id=None):
    """
    请求结束时记录/更新日志
    
    Args:
        context: 请求上下文
        reply: 回复对象
        start_time: 请求开始时间
        request_id: 请求ID（如果有则更新，否则新增）
    """
    try:
        _ensure_csv_header()

        user_id, user_nickname, is_group, group_name = _extract_user_info(context)
        msg_type = _get_msg_type(context)
        query = _truncate_content(context.content)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 提取回复信息
        if reply and reply.content:
            reply_content = _truncate_content(reply.content)
            reply_type = str(reply.type.name) if hasattr(reply.type, 'name') else str(reply.type)
            success = reply.type.name not in ["ERROR"] if hasattr(reply.type, 'name') else True
        else:
            reply_content = ""
            reply_type = ""
            success = False

        # 计算耗时
        duration = round(time.time() - start_time, 2) if start_time else 0

        status = "成功" if success else "失败"

        row = [
            timestamp,
            user_id,
            user_nickname,
            "是" if is_group else "否",
            group_name,
            msg_type,
            query,
            reply_content,
            reply_type,
            status,
            duration
        ]

        with _write_lock:
            with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(row)

    except Exception as e:
        from common.log import logger
        logger.warning(f"[request_log] failed to log request end: {e}")


# 兼容旧接口
def log_request(context, reply=None, start_time=None):
    """兼容旧接口：直接记录完成的请求"""
    log_request_end(context, reply, start_time)


class RequestTimer:
    """请求计时器，用于记录请求耗时和日志"""

    def __init__(self, context):
        self.context = context
        self.start_time = time.time()
        self.request_id = None
        self.logged_start = False

    def log_start(self):
        """记录请求开始（立即写入CSV，状态=处理中）"""
        if not self.logged_start:
            self.request_id = log_request_start(self.context)
            self.logged_start = True

    def log(self, reply=None):
        """记录请求完成"""
        log_request_end(self.context, reply, self.start_time, self.request_id)
